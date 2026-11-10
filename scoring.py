"""계산으로 구하는 값들 (저장하지 않음). 지금은 매수권만, 포인트·랭킹·뱃지는 다음 단계에서 추가."""
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
