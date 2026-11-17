"""곡 저장·조회 공통 함수 (시드 스크립트, 곡 등록, 디깅이 함께 씀)"""
import re
import unicodedata

AXIS_OF = {"moods": "MOOD", "energy": "ENERGY", "situations": "SITUATION", "times": "TIME",
           "instruments": "INSTRUMENT", "genre": "GENRE", "subgenres": "SUBGENRE"}

_BRACKETS = re.compile(r"[\(\[\{（【].*?[\)\]\}）】]")
_FEAT = re.compile(r"\b(feat|ft)\.?\s.*$", re.IGNORECASE)
_KEEP = re.compile(r"[^0-9a-z぀-ヿ㐀-鿿가-힣]")


def normalize(text):
    """중복 판정용: 소문자, 괄호 안 내용·feat. 이후 제거, 문자·숫자만 남김
    예) 'SURL (설)' → 'surl', '방백 (傍白)' → '방백', 'Love (feat. X)' → 'love'"""
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = _FEAT.sub("", _BRACKETS.sub("", t))
    return _KEEP.sub("", t)


_VID = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def extract_video_id(url):
    m = _VID.search(url or "")
    return m.group(1) if m else None


def tag_ids(conn):
    """{(axis, name): id}"""
    return {(r["axis"], r["name"]): r["id"] for r in conn.execute("SELECT id, axis, name FROM tags")}


def insert_song(conn, *, video_id, title, artist, category, start_sec, tags, origin="USER",
                submitted_by=None, intro=None, status="ACTIVE", audio_status="SKIPPED"):
    """곡 1개 + 확정 태그 저장 (커밋은 호출한 쪽에서). tags = {moods[순위순], genre, subgenres, instruments, ...}
    반환: 새 music.id. 목록에 없는 태그는 ValueError."""
    ids = tag_ids(conn)
    rows = []
    for key, axis in AXIS_OF.items():
        vals = tags.get(key) or []
        if isinstance(vals, str):
            vals = [vals]
        for rank, name in enumerate(vals, 1):
            if (axis, name) not in ids:
                raise ValueError(f"목록에 없는 태그: {axis} {name}")
            rows.append((ids[(axis, name)], rank if axis == "MOOD" else None))
    cur = conn.execute(
        "INSERT INTO music (video_id, title, artist, title_norm, artist_norm, category, start_sec, intro, "
        "submitted_by, status, audio_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (video_id, title, artist, normalize(title), normalize(artist), category, start_sec, intro,
         submitted_by, status, audio_status))
    mid = cur.lastrowid
    conn.executemany("INSERT INTO music_tags (music_id, tag_id, origin, accepted, rank) VALUES (?, ?, ?, 1, ?)",
                     [(mid, tid, origin, rank) for tid, rank in rows])
    return mid


def song_tags(conn, music_id):
    """확정 태그(accepted=1)를 축별로: {moods[순위순], genre, subgenres, instruments, situations, times, energy}"""
    out = {k: [] for k in AXIS_OF}
    rows = conn.execute(
        "SELECT t.axis, t.name, mt.rank FROM music_tags mt JOIN tags t ON t.id = mt.tag_id "
        "WHERE mt.music_id = ? AND mt.accepted = 1 ORDER BY t.axis, COALESCE(mt.rank, 9), t.sort", (music_id,))
    key_of = {v: k for k, v in AXIS_OF.items()}
    for r in rows:
        out[key_of[r["axis"]]].append(r["name"])
    out["genre"] = out["genre"][0] if out["genre"] else None
    out["energy"] = out["energy"][0] if out["energy"] else None
    return out
