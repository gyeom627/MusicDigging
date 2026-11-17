"""
디깅 API (설계 보고서 3.1, 4.1, 4.2, 6.2)
- 매수 전 응답에는 제목·아티스트·장르·매수 수를 넣지 않는다 (블라인드)
- 매수·패스·오류 보고는 세션에 저장된 '현재 곡'에만 할 수 있다 (곡 id를 클라이언트에서 받지 않음)
- 매수는 BEGIN IMMEDIATE 트랜잭션 안에서 중복·매수권·곡 상태를 다시 확인한다
- 들은 순서는 세션의 dig_tl(최근 30곡)과 dig_pos(지금 위치)에 남겨, 릴스처럼 이전 곡으로 돌아갈 수 있다.
  돌아간 곡도 매수 전이면 여전히 가려져 있고, 이미 매수한 곡이면 공개된 정보로 보여 준다
"""
import secrets
import sqlite3

from flask import Blueprint, g, jsonify, request, session

import config
import scoring
from db import get_db
from guards import login_required
from music import song_tags
from tag_rules import MOOD_GROUP_OF

bp = Blueprint("dig", __name__)
_rng = secrets.SystemRandom()


def _fail(msg, code=409, **extra):
    return jsonify(ok=False, error=msg, **extra), code


TIMELINE_MAX = 30


def _timeline():
    return list(session.get("dig_tl", [])), session.get("dig_pos", -1)


def _save_timeline(tl, pos):
    if len(tl) > TIMELINE_MAX:  # 오래된 곡부터 잊는다
        cut = len(tl) - TIMELINE_MAX
        tl, pos = tl[cut:], pos - cut
    session["dig_tl"], session["dig_pos"] = tl, pos


def _song_payload(db, uid, mid):
    """곡 하나의 응답. 매수 전이면 가린 채로, 매수했으면 공개 정보까지. 들을 수 없는 곡이면 None"""
    row = db.execute("SELECT id, video_id, start_sec, category, title, artist, status FROM music WHERE id = ?",
                     (mid,)).fetchone()
    if row is None or row["status"] != "ACTIVE":
        return None
    tags = song_tags(db, mid)
    moods = tags["moods"]
    out = dict(ok=True, empty=False, video_id=row["video_id"], start_sec=row["start_sec"],
               length=config.HIGHLIGHT_SEC, category=row["category"], moods=moods,
               mood_groups=[MOOD_GROUP_OF[m] for m in moods], tickets_left=scoring.tickets_left(db, uid))
    bought = db.execute("SELECT count_at_buy, is_early FROM purchases WHERE user_id = ? AND music_id = ?",
                        (uid, mid)).fetchone()
    if bought:
        session.pop("dig_current", None)  # 이미 매수한 곡은 다시 매수할 수 없다
        out.update(bought=True, title=row["title"], artist=row["artist"], genre=tags["genre"],
                   rank=bought["count_at_buy"] + 1, is_early=bool(bought["is_early"]))
    else:
        session["dig_current"] = mid
        out["bought"] = False
    return out


def _step(db, uid, tl, pos, direction):
    """타임라인에서 한 칸씩 이동. 그사이 숨겨진 곡은 건너뛰고 목록에서 뺀다"""
    here = pos
    pos += direction
    while 0 <= pos < len(tl):
        payload = _song_payload(db, uid, tl[pos])
        if payload:
            _save_timeline(tl, pos)
            return payload
        tl.pop(pos)
        if direction < 0:
            pos -= 1
            here -= 1
    _save_timeline(tl, here)  # 갈 곳이 없으면 제자리 (빠진 곡만 정리)
    return None


@bp.post("/api/dig/next")
@login_required
def dig_next():
    data = request.get_json(silent=True) or {}
    category = data.get("category", "ALL")
    if category != "ALL" and category not in config.CATEGORIES:
        return _fail("카테고리가 올바르지 않습니다.", 400)
    db, uid = get_db(), g.user["id"]
    tl, pos = _timeline()
    # 이전 곡으로 돌아가 있었다면 먼저 들었던 다음 곡으로 (카테고리를 바꾼 경우는 새로 뽑기)
    if not data.get("fresh") and pos + 1 < len(tl):
        payload = _step(db, uid, tl, pos, +1)
        if payload:
            return jsonify(payload)
        tl, pos = _timeline()
    tl = tl[:max(pos, -1) + 1]
    sql = ("SELECT id FROM music m WHERE status = 'ACTIVE' "
           "AND NOT EXISTS (SELECT 1 FROM purchases p WHERE p.user_id = ? AND p.music_id = m.id) "
           "AND NOT EXISTS (SELECT 1 FROM passes s WHERE s.user_id = ? AND s.music_id = m.id)")
    params = [uid, uid]
    if category != "ALL":
        sql += " AND category = ?"
        params.append(category)
    rows = [r["id"] for r in db.execute(sql, params).fetchall()]
    recent = set(tl)
    candidates = [r for r in rows if r not in recent] or rows  # 최근에 들은 곡은 되도록 피함
    if not candidates:
        session.pop("dig_current", None)
        _save_timeline(tl, len(tl))  # '끝' 위치: 이전 곡을 누르면 마지막에 들은 곡으로
        return jsonify(ok=True, empty=True, tickets_left=scoring.tickets_left(db, uid))
    pick = _rng.choice(candidates)
    tl.append(pick)
    _save_timeline(tl, len(tl) - 1)
    return jsonify(_song_payload(db, uid, pick))


@bp.post("/api/dig/prev")
@login_required
def dig_prev():
    db, uid = get_db(), g.user["id"]
    tl, pos = _timeline()
    payload = _step(db, uid, tl, min(pos, len(tl)), -1) if pos > 0 else None
    if payload is None:
        return jsonify(ok=True, none=True)
    return jsonify(payload)


@bp.post("/api/dig/buy")
@login_required
def dig_buy():
    mid = session.get("dig_current")
    if not mid:
        return _fail("지금 듣는 곡이 없습니다. 다음 곡을 먼저 받아 주세요.")
    db, uid = get_db(), g.user["id"]
    try:
        db.execute("BEGIN IMMEDIATE")  # 쓰기 잠금을 먼저 잡아 동시 매수에도 buy_count가 어긋나지 않게
        song = db.execute("SELECT id, title, artist, buy_count, status FROM music WHERE id = ?", (mid,)).fetchone()
        if song is None or song["status"] != "ACTIVE":
            db.rollback(); session.pop("dig_current", None)
            return _fail("지금은 매수할 수 없는 곡입니다.")
        if db.execute("SELECT 1 FROM purchases WHERE user_id = ? AND music_id = ?", (uid, mid)).fetchone():
            db.rollback(); session.pop("dig_current", None)
            return _fail("이미 매수한 곡입니다.")
        if scoring.tickets_left(db, uid) <= 0:
            db.rollback()
            return _fail("오늘의 매수권을 모두 썼습니다. 한국 시간 자정에 다시 채워집니다.", tickets_left=0)
        count_at_buy = song["buy_count"]
        is_early = 1 if count_at_buy < config.EARLY_BUYER_LIMIT else 0
        db.execute("INSERT INTO purchases (user_id, music_id, count_at_buy, is_early) VALUES (?, ?, ?, ?)",
                   (uid, mid, count_at_buy, is_early))
        db.execute("UPDATE music SET buy_count = buy_count + 1 WHERE id = ?", (mid,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return _fail("이미 매수한 곡입니다.")
    except sqlite3.OperationalError:  # 잠금 대기 시간 초과 등
        db.rollback()
        return _fail("요청이 많아 처리하지 못했습니다. 잠시 후 다시 눌러 주세요.", 503)
    session.pop("dig_current", None)
    tags = song_tags(db, mid)
    return jsonify(ok=True, title=song["title"], artist=song["artist"], genre=tags["genre"],
                   subgenres=tags["subgenres"], moods=tags["moods"], rank=count_at_buy + 1,
                   is_early=bool(is_early), tickets_left=scoring.tickets_left(db, uid))


@bp.post("/api/dig/pass")
@login_required
def dig_pass():
    mid = session.pop("dig_current", None)
    if not mid:
        return _fail("지금 듣는 곡이 없습니다.")
    db, uid = get_db(), g.user["id"]
    recorded = False
    if scoring.tickets_left(db, uid) > 0:  # 매수권이 0이면 패스로 기록하지 않음 → 내일 다시 만날 수 있음
        db.execute("INSERT OR IGNORE INTO passes (user_id, music_id) VALUES (?, ?)", (uid, mid))
        db.commit()
        recorded = True
    return jsonify(ok=True, recorded=recorded)


@bp.post("/api/dig/error")
@login_required
def dig_error():
    """재생 오류 보고. 같은 사용자는 같은 곡을 한 번만 보고하게 되므로(패스로 기록) 3명이 보고해야 숨겨진다"""
    mid = session.pop("dig_current", None)
    if not mid:
        return _fail("지금 듣는 곡이 없습니다.")
    db, uid = get_db(), g.user["id"]
    db.execute("INSERT OR IGNORE INTO passes (user_id, music_id) VALUES (?, ?)", (uid, mid))
    db.execute("UPDATE music SET error_count = error_count + 1, "
               "status = CASE WHEN error_count + 1 >= ? THEN 'HIDDEN' ELSE status END WHERE id = ?",
               (config.AUTO_HIDE_ERRORS, mid))
    db.commit()
    tl, pos = _timeline()  # 재생되지 않는 곡은 이전 곡 목록에서도 뺀다
    if 0 <= pos < len(tl) and tl[pos] == mid:
        tl.pop(pos)
        _save_timeline(tl, pos - 1)
    return jsonify(ok=True)
