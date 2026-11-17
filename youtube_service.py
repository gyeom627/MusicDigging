"""
YouTube Data API (설계 보고서 4.5 곡 등록·제보, 7.2 오작동 #7~#9, #29~#31)
- videos.list(1 unit): 공개·임베드 허용·라이브 아님·연령 제한 없음·한국에서 재생 가능·길이·조회수 확인
- search.list(100 units) + videos.list: '아티스트 + 제목'으로 같은 곡 공식 영상들의 조회수를 모아 곡 단위로 판정
- 받은 조회수·길이·채널명·검색 결과는 판정에만 쓰고 저장하지 않는다 (API 약관)
- 키 없음·할당량 초과·네트워크 오류는 YouTubeError로 올린다 → 확인 단계에서는 '등록 보류', 검색 단계에서는 REVIEW
"""
import re

import requests

import config
from music import normalize

API = "https://www.googleapis.com/youtube/v3"
UA = {"User-Agent": "DiggerCoursework/0.1"}
MUSIC_CATEGORY = "10"
COVER_WORDS = ("cover", "커버", "歌ってみた", "karaoke", "노래방")
MV_WORDS = ("mv", "m/v", "music video", "뮤직비디오", "뮤비")


class YouTubeError(Exception):
    """kind = NO_KEY / QUOTA / HTTP"""
    def __init__(self, kind, msg=""):
        super().__init__(msg or kind)
        self.kind = kind


def _get(path, **params):
    if not config.YOUTUBE_API_KEY:
        raise YouTubeError("NO_KEY", "YOUTUBE_API_KEY 없음")
    try:
        r = requests.get(f"{API}/{path}", headers=UA, timeout=15, params={**params, "key": config.YOUTUBE_API_KEY})
    except requests.RequestException as e:
        raise YouTubeError("HTTP", type(e).__name__) from e
    if r.status_code == 403 and "quota" in r.text.lower():
        raise YouTubeError("QUOTA", "할당량 초과")
    if r.status_code != 200:
        raise YouTubeError("HTTP", f"HTTP {r.status_code}")
    return r.json()


_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(iso):
    """'PT4M1S' → 241"""
    m = _DUR.fullmatch(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _video(item):
    sn, cd, st, stats = item.get("snippet", {}), item.get("contentDetails", {}), item.get("status", {}), \
        item.get("statistics", {})
    region = cd.get("regionRestriction", {})
    kr_blocked = "KR" in region.get("blocked", []) or ("allowed" in region and "KR" not in region["allowed"])
    views = stats.get("viewCount")
    return {
        "video_id": item.get("id"),
        "title": sn.get("title", ""),
        "channel": sn.get("channelTitle", ""),
        "category_id": sn.get("categoryId"),
        "live": sn.get("liveBroadcastContent", "none") != "none",
        "duration": parse_duration(cd.get("duration")),
        "age_restricted": cd.get("contentRating", {}).get("ytRating") == "ytAgeRestricted",
        "kr_blocked": kr_blocked,
        "public": st.get("privacyStatus") == "public",
        "embeddable": bool(st.get("embeddable")),
        "views": int(views) if views is not None else None,  # None = 조회수 비공개
    }


def fetch_videos(ids):
    """videos.list 한 번에 최대 50개. {video_id: info}"""
    if not ids:
        return {}
    data = _get("videos", part="snippet,contentDetails,status,statistics", id=",".join(ids[:50]), maxResults=50)
    return {it["id"]: _video(it) for it in data.get("items", [])}


def fetch_video(video_id):
    """영상 1개 정보 또는 None(없는 영상·삭제됨)"""
    return fetch_videos([video_id]).get(video_id)


def problems(info, start_sec=None):
    """등록을 막는 조건 목록 (비어 있으면 통과). 조회수 비공개는 막지 않고 REVIEW 사유로 따로 본다"""
    out = []
    if info is None:
        return ["영상을 찾을 수 없어요 (삭제되었거나 주소가 틀렸어요)."]
    if not info["public"]:
        out.append("공개 영상이 아니에요.")
    if not info["embeddable"]:
        out.append("다른 사이트에서 재생(퍼가기)이 막힌 영상이에요.")
    if info["live"]:
        out.append("라이브·예정 영상은 등록할 수 없어요.")
    if info["age_restricted"]:
        out.append("연령 제한 영상은 등록할 수 없어요.")
    if info["kr_blocked"]:
        out.append("한국에서 재생할 수 없는 영상이에요.")
    if info["views"] is not None and info["views"] >= config.VIEW_LIMIT:
        out.append(f"조회수가 {config.VIEW_LIMIT // 10000}만 회 이상인 영상이에요. 숨은 곡만 등록할 수 있어요.")
    if start_sec is not None and info["duration"] and start_sec + config.HIGHLIGHT_SEC > info["duration"]:
        out.append("하이라이트 시작 + 60초가 영상 길이를 넘어요. 시작 시점을 앞당겨 주세요.")
    return out


def _same_song(v, title_n, artist_n):
    t = normalize(v["title"])
    if any(w in v["title"].lower() for w in COVER_WORDS):
        return False
    return bool(title_n) and title_n in t and (artist_n in t or artist_n in normalize(v["channel"]))


def is_mv(title):
    low = title.lower()
    return any(w in low for w in MV_WORDS)


def song_views(artist, title, my_video_id):
    """곡 단위 조회수 판정. 반환 dict:
    status = PASS / REJECT / REVIEW, reason(사유 문장), over(100만 이상 영상 제목), mv(제안할 MV {video_id, title} 또는 None)
    할당량 초과·오류도 REVIEW로 돌려준다 (거부하지 않고 관리자 검토, 설계 #30)"""
    try:
        data = _get("search", part="snippet", q=f"{artist} {title}", type="video", maxResults=10)
        found = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
        infos = fetch_videos(found + ([my_video_id] if my_video_id not in found else []))
    except YouTubeError as e:
        reason = "검색 할당량이 끝나" if e.kind == "QUOTA" else "검색이 실패해"
        return {"status": "REVIEW", "reason": f"{reason} 곡 단위 조회수를 확인하지 못했어요.", "over": None, "mv": None}
    title_n, artist_n = normalize(title), normalize(artist)
    same = [v for v in infos.values() if v["video_id"] == my_video_id or _same_song(v, title_n, artist_n)]
    over = [v for v in same if v["views"] is not None and v["views"] >= config.VIEW_LIMIT]
    if over:
        top = max(over, key=lambda v: v["views"])
        return {"status": "REJECT", "over": top["title"], "mv": None,
                "reason": f"같은 곡의 다른 영상 조회수가 {config.VIEW_LIMIT // 10000}만 회를 넘어요: {top['title']}"}
    mine = infos.get(my_video_id)
    mv = None
    if mine and not is_mv(mine["title"]):
        mvs = [v for v in same if v["video_id"] != my_video_id and is_mv(v["title"]) and not problems(v)]
        if mvs:
            mv = {"video_id": mvs[0]["video_id"], "title": mvs[0]["title"]}
    in_search = [v for v in same if v["video_id"] in found and _same_song(v, title_n, artist_n)]
    if not in_search:  # 검색으로 이 곡을 찾지 못함(표기 차이 등) → 다른 인기 영상이 없다고 장담할 수 없어 관리자 확인
        return {"status": "REVIEW", "reason": "검색에서 같은 곡의 다른 영상을 찾지 못해 관리자가 확인해요.",
                "over": None, "mv": mv}
    if mine and mine["views"] is None:
        return {"status": "REVIEW", "reason": "등록한 영상의 조회수가 비공개라 관리자가 확인해요.", "over": None, "mv": mv}
    return {"status": "PASS", "reason": "", "over": None, "mv": mv}
