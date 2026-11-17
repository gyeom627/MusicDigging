"""
무드 문장 디깅 (설계 보고서 3.3 무드 디깅 플로우, 4.8 해석·점수·추첨)
1) 문장(100자 이내)을 정규화해 mood_cache에서 찾는다 → 있으면 Gemini를 부르지 않는다
2) 처음 보는 문장만 Gemini가 고정 태그 목록 안에서 해석 → 목록 밖 값은 버리고 캐시에 저장
3) 디깅할 때는 캐시의 해석으로 후보 곡마다 점수를 매기고, 최고점의 절반(올림) 이상인 묶음에서 무작위 1곡
   (최고점이 0이면 무드를 빼고 카테고리 전체에서 무작위로 고른 뒤 안내)
- 하루 AI 해석 횟수 제한(DAILY_MOOD_LIMIT)은 ai_logs로 센다. 캐시 적중은 세지 않는다
- Gemini가 실패해도 화면은 깨지지 않는다: 오류 안내 + 무드·장르 칩을 직접 고르는 방법으로 대체
"""
import json
import math

from flask import Blueprint, g, jsonify, request

import config
import gemini_service
import scoring
from db import get_db
from guards import login_required
from music import song_tags
from tag_rules import MOOD_GROUP_OF, match_score, normalize_query

bp = Blueprint("mood", __name__)


def labels(tags):
    """해석 결과를 칩 순서대로 [{text, g}] (예: 잔잔한 · 밤 · 산책 · 포크·어쿠스틱). g = 분위기 그룹(색)"""
    out = [{"text": m, "g": MOOD_GROUP_OF.get(m)} for m in tags.get("moods", [])]
    rest = list(tags.get("times", [])) + list(tags.get("situations", []))
    if tags.get("energy"):
        rest.append(f"에너지 {tags['energy']}")
    if tags.get("genre"):
        rest.append(tags["genre"])
    rest += list(tags.get("subgenres", [])) + list(tags.get("instruments", []))
    return out + [{"text": x, "g": None} for x in rest]


def cached(db, text):
    """캐시에 있는 해석 {status, tags} 또는 None"""
    row = db.execute("SELECT parsed_json FROM mood_cache WHERE query_norm = ?", (normalize_query(text),)).fetchone()
    return json.loads(row["parsed_json"]) if row else None


def _log(db, uid, status, call=None):
    call = call or {}
    db.execute("INSERT INTO ai_logs (user_id, kind, status, seconds, in_tokens, out_tokens) VALUES (?, 'MOOD', ?, ?, ?, ?)",
               (uid, status, call.get("seconds"), call.get("in_tok"), call.get("out_tok")))


def calls_today(db, uid):
    start, end = scoring.today_bounds_utc()
    return db.execute("SELECT COUNT(*) FROM ai_logs WHERE user_id = ? AND kind = 'MOOD' AND status != 'CACHED' "
                      "AND created_at >= ? AND created_at < ?", (uid, start, end)).fetchone()[0]


@bp.post("/api/mood/interpret")
@login_required
def interpret():
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return jsonify(ok=False, error="듣고 싶은 느낌을 한 문장으로 적어 주세요."), 400
    if len(text) > config.MOOD_TEXT_MAX:
        return jsonify(ok=False, error=f"{config.MOOD_TEXT_MAX}자 이내로 적어 주세요."), 400
    db, uid = get_db(), g.user["id"]

    hit = cached(db, text)
    if hit:  # 같은 문장은 다시 부르지 않는다 (무료 쿼터 보호, 같은 문장 = 같은 해석)
        _log(db, uid, "CACHED")
        db.commit()
        return jsonify(ok=True, cached=True, status=hit["status"], tags=hit["tags"], labels=labels(hit["tags"]))

    if calls_today(db, uid) >= config.DAILY_MOOD_LIMIT:
        return jsonify(ok=False, error=f"오늘 AI 해석 {config.DAILY_MOOD_LIMIT}회를 모두 썼어요. "
                                       "아래에서 무드·장르를 직접 고르거나, 전에 쓴 문장을 다시 쓸 수 있어요."), 429

    status, tags, call = gemini_service.interpret_mood(text)
    _log(db, uid, status, call)
    if status == "FAILED":
        db.commit()
        return jsonify(ok=False, error="AI가 지금 응답하지 않아요. 아래에서 무드·장르를 직접 골라 디깅할 수 있어요."), 503
    db.execute("INSERT OR IGNORE INTO mood_cache (query_norm, parsed_json) VALUES (?, ?)",
               (normalize_query(text), json.dumps({"status": status, "tags": tags}, ensure_ascii=False)))
    db.commit()
    return jsonify(ok=True, cached=False, status=status, tags=tags, labels=labels(tags))


def pick_by_mood(db, query, candidate_ids, rng):
    """후보 곡에 점수를 매겨 최고점의 절반(올림) 이상인 묶음에서 1곡. 반환: (곡 id, 무드로 골랐는지)"""
    scored = [(mid, match_score(query, song_tags(db, mid))) for mid in candidate_ids]
    top = max(s for _, s in scored)
    if top == 0:  # 문장과 겹치는 곡이 하나도 없음 → 무드 없이 무작위
        return rng.choice(candidate_ids), False
    cut = math.ceil(top / 2)
    return rng.choice([mid for mid, s in scored if s >= cut]), True
