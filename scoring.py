"""계산으로 구하는 값들 (저장하지 않음, 설계 보고서 4.3·4.4)
- 매수권: 오늘(한국 시간) 매수 수로 계산
- 디깅 포인트: 매수 한 건 = (지금 누적 매수 − 매수 직전 누적 매수 − 1) × 가중치(초기 발굴 2, 아니면 1), 사용자 점수 = 합
  → purchases와 music.buy_count만으로 계산하므로 저장값과 실제 기록이 어긋날 일이 없다
- 랭킹 점수 = 디깅 포인트 + 내 글이 받은 공감 수
- 뱃지: 조건으로 계산해 표시
"""
from datetime import datetime, time, timedelta, timezone

import config

UTC_FMT = "%Y-%m-%d %H:%M:%S"


def today_bounds_utc(now=None):
    """한국 시간 '오늘' 00:00 ~ 내일 00:00을 UTC 문자열 구간으로 돌려준다 (created_at 비교용)"""
    now = now or datetime.now(timezone.utc)
    kst_day = now.astimezone(config.KST).date()
    start = datetime.combine(kst_day, time(0, 0), tzinfo=config.KST).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    return start.strftime(UTC_FMT), end.strftime(UTC_FMT)


def tickets_left(conn, user_id, now=None):
    start, end = today_bounds_utc(now)
    used = conn.execute(
        "SELECT COUNT(*) FROM purchases WHERE user_id = ? AND created_at >= ? AND created_at < ?",
        (user_id, start, end),
    ).fetchone()[0]
    return max(0, config.DAILY_BUY_LIMIT - used)


def to_kst(utc_text):
    """DB의 UTC 문자열 → 화면용 한국 시간 문자열"""
    dt = datetime.strptime(utc_text, UTC_FMT).replace(tzinfo=timezone.utc)
    return dt.astimezone(config.KST).strftime("%Y-%m-%d %H:%M")


def purchase_points(count_now, count_at_buy, is_early):
    """매수 한 건의 포인트. 예: 5번째로 산 곡(count_at_buy = 4)이 지금 25회면 (25 − 4 − 1) × 2 = 40"""
    return max(0, count_now - count_at_buy - 1) * (config.EARLY_WEIGHT if is_early else 1)


# SQL에서 같은 식으로 계산 (랭킹처럼 여러 사람을 한 번에 셀 때)
POINTS_SQL = (f"COALESCE(SUM(MAX(0, m.buy_count - p.count_at_buy - 1) * "
              f"CASE p.is_early WHEN 1 THEN {config.EARLY_WEIGHT} ELSE 1 END), 0)")


def user_points(conn, user_id):
    return conn.execute(f"SELECT {POINTS_SQL} FROM purchases p JOIN music m ON m.id = p.music_id WHERE p.user_id = ?",
                        (user_id,)).fetchone()[0]


def likes_received(conn, user_id):
    """내 글(공개 중)이 받은 공감 수. 자기 글 공감은 게시판에서 막는다"""
    return conn.execute("SELECT COUNT(*) FROM post_likes l JOIN posts s ON s.id = l.post_id "
                        "WHERE s.user_id = ? AND s.status = 'ACTIVE'", (user_id,)).fetchone()[0]


def badges(conn, user_id, points=None):
    """[{name, desc, earned, progress}] — 얻은 뱃지와 아직 못 얻은 뱃지(진행 상황)를 함께"""
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(p.is_early), 0) AS early, "
        "COALESCE(MAX(CASE p.is_early WHEN 1 THEN m.buy_count END), 0) AS early_top "
        "FROM purchases p JOIN music m ON m.id = p.music_id WHERE p.user_id = ?", (user_id,)).fetchone()
    points = user_points(conn, user_id) if points is None else points
    sharp, gold = config.BADGE_SHARP_COUNT, config.BADGE_GOLD_POINTS
    return [
        {"name": "첫 삽", "desc": "첫 매수", "earned": row["n"] >= 1,
         "progress": "디깅에서 마음에 드는 곡을 매수해 보세요"},
        {"name": "First Digger", "desc": f"누적 매수 {config.EARLY_BUYER_LIMIT}회 미만일 때 매수(초기 발굴)",
         "earned": row["early"] >= 1, "progress": "아직 덜 알려진 곡을 먼저 매수해 보세요"},
        {"name": "촉 좋은 디거", "desc": f"초기 발굴한 곡이 누적 매수 {sharp}회 도달", "earned": row["early_top"] >= sharp,
         "progress": f"초기 발굴한 곡의 최고 누적 매수 {row['early_top']} / {sharp}회"},
        {"name": "황금 곡괭이", "desc": f"디깅 포인트 {gold}점 이상", "earned": points >= gold,
         "progress": f"지금 {points} / {gold}점"},
    ]
