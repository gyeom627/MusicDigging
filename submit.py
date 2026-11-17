"""
곡 제보 API (설계 보고서 3.2 곡 등록·태깅 플로우, 4.5, 4.7, 7.2)
  1단계 /api/submit/check   링크 → videos.list로 영상 조건 확인, 제목·채널 미리 채움
  3단계 /api/submit/suggest 제목·아티스트 → 곡 단위 조회수 판정 → Gemini 오디오 분석 + Discogs → 추천 칩
  4단계 /api/submit/publish 등록자가 고친 칩으로 게시 (영상 재조회·검색 조합 확인·출처 기록)
- 진행 상태는 세션 submit에만 둔다: 브라우저가 보낸 video_id·출처·판정 결과는 믿지 않는다
- YouTube API 결과(조회수·길이·채널명·검색 결과)는 판정에만 쓰고 DB에 저장하지 않는다
"""
import re
import sqlite3

from flask import Blueprint, g, jsonify, request, session

import config
import discogs_service
import gemini_service
import scoring
import tagging
import youtube_service as yt
from db import get_db
from guards import login_required
from music import extract_video_id, insert_song, normalize

bp = Blueprint("submit_api", __name__)

_TAG_WORDS = re.compile(r"[\[\(【](?:[^\]\)】]*?(?:mv|m/v|official|audio|lyric|live|가사|뮤직비디오|뮤비)[^\]\)】]*)[\]\)】]",
                        re.IGNORECASE)


def _fail(msg, code=400, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def _yt_error(e):
    if e.kind == "NO_KEY":
        return _fail("YouTube 키가 아직 설정되지 않아 지금은 제보를 받을 수 없어요. 관리자에게 알려 주세요.", 503)
    if e.kind == "QUOTA":
        return _fail("오늘 YouTube 조회 할당량이 끝났어요. 내일 다시 제보해 주세요.", 503)
    return _fail("영상 정보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", 503)


def guess_artist_title(video_title, channel):
    """'아티스트 - 제목 [MV]' 꼴이면 나눠서 미리 채운다 (등록자가 고칠 수 있음)"""
    t = _TAG_WORDS.sub("", video_title).strip()
    for sep in (" - ", " – ", " — ", " | "):
        if sep in t:
            a, b = t.split(sep, 1)
            return a.strip(), b.strip().strip("'\"‘’“”")
    return channel.replace(" - Topic", "").strip(), t.strip("'\"‘’“”")


def _today_count(db, sql, uid):
    start, end = scoring.today_bounds_utc()
    return db.execute(sql + " AND created_at >= ? AND created_at < ?", (uid, start, end)).fetchone()[0]


def _form(data):
    """제목·아티스트·카테고리·시작 시점 검사. 반환: (값 dict, 오류 문장 또는 None)"""
    title, artist = (data.get("title") or "").strip(), (data.get("artist") or "").strip()
    category = data.get("category")
    try:
        start = int(data.get("start_sec"))
    except (TypeError, ValueError):
        start = -1
    if not title or not artist:
        return None, "제목과 아티스트를 입력해 주세요."
    if len(title) > 100 or len(artist) > 100:
        return None, "제목과 아티스트는 100자 이내로 입력해 주세요."
    if not normalize(title) or not normalize(artist):
        return None, "제목과 아티스트에 글자나 숫자가 있어야 해요."
    if category not in config.CATEGORIES:
        return None, "카테고리를 골라 주세요."
    if start < 0:
        return None, "하이라이트 시작 시점을 분:초로 입력해 주세요."
    return {"title": title, "artist": artist, "category": category, "start_sec": start}, None


def _duplicate(db, video_id=None, artist=None, title=None):
    if video_id and db.execute("SELECT 1 FROM music WHERE video_id = ?", (video_id,)).fetchone():
        return "이미 등록된 영상이에요."
    if artist and db.execute("SELECT 1 FROM music WHERE artist_norm = ? AND title_norm = ?",
                             (normalize(artist), normalize(title))).fetchone():
        return "이미 등록된 곡이에요 (다른 영상으로 등록되어 있어요)."
    return None


@bp.post("/api/submit/check")
@login_required
def check():
    """1단계: 링크 확인"""
    vid = extract_video_id((request.get_json(silent=True) or {}).get("url", ""))
    if not vid:
        return _fail("유튜브 영상 주소를 넣어 주세요. (youtube.com/watch?v=… 또는 youtu.be/…)")
    db = get_db()
    dup = _duplicate(db, video_id=vid)
    if dup:
        return _fail(dup, 409)
    try:
        info = yt.fetch_video(vid)
    except yt.YouTubeError as e:
        return _yt_error(e)
    bad = yt.problems(info)
    if bad:
        return _fail(" ".join(bad), 400, problems=bad)
    session["submit"] = {"video_id": vid, "duration": info["duration"]}  # 새 링크면 이전 진행 상태는 버린다
    artist, title = guess_artist_title(info["title"], info["channel"])
    warn = None if info["category_id"] == yt.MUSIC_CATEGORY else "음악 카테고리 영상이 아니에요. 곡이 맞는지 확인해 주세요."
    return jsonify(ok=True, video_id=vid, video_title=info["title"], channel=info["channel"],
                   duration=info["duration"], artist=artist, title=title, warn=warn)


@bp.post("/api/submit/suggest")
@login_required
def suggest():
    """3단계: 곡 단위 조회수 판정 → AI 추천 칩"""
    st = session.get("submit")
    if not st:
        return _fail("링크 확인부터 해 주세요.", 409)
    f, err = _form(request.get_json(silent=True) or {})
    if err:
        return _fail(err)
    if st["duration"] and f["start_sec"] + config.HIGHLIGHT_SEC > st["duration"]:
        return _fail("하이라이트 시작 + 60초가 영상 길이를 넘어요. 시작 시점을 앞당겨 주세요.")
    db, uid = get_db(), g.user["id"]
    dup = _duplicate(db, artist=f["artist"], title=f["title"])
    if dup:
        return _fail(dup, 409)

    pair = [normalize(f["artist"]), normalize(f["title"])]
    if st.get("pair") != pair:  # 제목·아티스트가 바뀌면 검색·Discogs를 다시 한다
        views = yt.song_views(f["artist"], f["title"], st["video_id"])
        if views["status"] == "REJECT":
            st.pop("pair", None)
            session["submit"] = st
            return _fail(views["reason"], 400, rejected=True)
        st.update(pair=pair, views={"status": views["status"], "reason": views["reason"]}, mv=views["mv"],
                  web=discogs_service.lookup(f["artist"], f["title"]))

    # Gemini 오디오 분석: 같은 영상·같은 시작 시점이면 세션의 결과를 다시 쓴다 (재분석 금지, 설계 4.7)
    audio_msg = None
    if st.get("ai_start") != f["start_sec"]:
        if _today_count(db, "SELECT COUNT(*) FROM ai_logs WHERE user_id = ? AND kind = 'AUDIO'", uid) \
                >= config.DAILY_SUGGEST_LIMIT:
            st.update(ai={}, audio_status="SKIPPED", ai_start=None)
            audio_msg = f"오늘 AI 추천 {config.DAILY_SUGGEST_LIMIT}회를 모두 썼어요. 칩을 직접 골라 주세요."
        else:
            status, tags, call = gemini_service.analyze_audio(st["video_id"], f["start_sec"])
            db.execute("INSERT INTO ai_logs (user_id, kind, video_id, status, seconds, in_tokens, out_tokens) "
                       "VALUES (?, 'AUDIO', ?, ?, ?, ?, ?)",
                       (uid, st["video_id"], status, call["seconds"], call["in_tok"], call["out_tok"]))
            db.commit()
            st.update(ai=tags if status != "FAILED" else {}, audio_status=status,
                      ai_start=f["start_sec"] if status != "FAILED" else None)
    if st["audio_status"] == "FAILED":
        audio_msg = "AI가 지금 소리를 분석하지 못했어요. 칩을 직접 골라 주세요."
    elif st["audio_status"] == "NOT_MUSIC":
        audio_msg = "AI가 음악이 아닌 영상으로 판단했어요. 그래도 게시하면 관리자 검토 후 공개돼요."

    selected, suggested = tagging.recommend(st["ai"], st["web"])
    st["suggested"] = suggested
    session["submit"] = st
    web = st["web"]
    return jsonify(ok=True, views=st["views"], mv=st.get("mv"), audio_status=st["audio_status"], audio_msg=audio_msg,
                   web={"status": web["status"], "url": web["url"]}, selected=selected, suggested=suggested)


@bp.post("/api/submit/publish")
@login_required
def publish():
    """4단계: 게시. 영상을 다시 조회하고, 검색한 조합과 같은지 확인한 뒤 저장"""
    st = session.get("submit")
    if not st or "suggested" not in st:
        return _fail("AI 추천 받기까지 마친 뒤 게시할 수 있어요.", 409)
    data = request.get_json(silent=True) or {}
    f, err = _form(data)
    if err:
        return _fail(err)
    if [normalize(f["artist"]), normalize(f["title"])] != st.get("pair"):
        return _fail("제목이나 아티스트가 바뀌었어요. [AI 추천 받기]를 다시 눌러 주세요.", 409, redo=True)
    intro = (data.get("intro") or "").strip() or None
    if intro and len(intro) > config.INTRO_MAX:
        return _fail(f"한줄 소개는 {config.INTRO_MAX}자 이내로 써 주세요.")
    tags, errors = tagging.clean_final(data.get("tags"))
    if errors:
        return _fail(" ".join(errors))

    db, user = get_db(), g.user
    if _today_count(db, "SELECT COUNT(*) FROM music WHERE submitted_by = ?", user["id"]) >= config.DAILY_SUBMIT_LIMIT:
        return _fail(f"제보는 하루 {config.DAILY_SUBMIT_LIMIT}곡까지예요. 내일 다시 제보해 주세요.", 429)
    try:  # 최종 재검사 (#7 확인 후 다른 링크·조작 방지): 조회가 안 되면 보류
        info = yt.fetch_video(st["video_id"])
    except yt.YouTubeError:
        return _fail("영상 정보를 다시 확인하지 못해 등록을 보류했어요. 잠시 후 다시 게시해 주세요.", 503)
    bad = yt.problems(info, f["start_sec"])
    if bad:
        return _fail(" ".join(bad), 400, problems=bad)

    reasons = []
    if st["views"]["status"] == "REVIEW":
        reasons.append(st["views"]["reason"])
    if st.get("audio_status") == "NOT_MUSIC":
        reasons.append("AI가 음악이 아닌 영상으로 판단")
    status = "REVIEW" if reasons else "ACTIVE"
    is_admin = user["role"] == "ADMIN"
    origin_of, rejected = tagging.origins(tags, st["suggested"], "ADMIN" if is_admin else "USER")
    ai_moods = (st.get("ai") or {}).get("moods") or []
    web = st["web"]
    try:
        db.execute("BEGIN IMMEDIATE")
        mid = insert_song(db, video_id=st["video_id"], title=f["title"], artist=f["artist"], category=f["category"],
                          start_sec=f["start_sec"], tags=tags, submitted_by=user["id"], intro=intro, status=status,
                          audio_status=st.get("audio_status", "SKIPPED"), origin_of=origin_of, rejected=rejected,
                          ai_ranks={m: i for i, m in enumerate(ai_moods, 1)}, review_reason=" / ".join(reasons) or None,
                          web_status=None if web["status"] == "SKIPPED" else web["status"],
                          discogs_url=web["url"], discogs_styles=web["styles"])
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return _fail("방금 같은 곡이 먼저 등록되었어요.", 409)
    session.pop("submit", None)
    return jsonify(ok=True, music_id=mid, status=status, review_reason=" / ".join(reasons) or None)
