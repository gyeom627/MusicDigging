"""
Discogs 장르 참고 (설계 보고서 4.7: 채택 조건, 장르 변환, 출처 표기)
- 아티스트·곡명을 정규화해, 검색된 릴리스의 아티스트가 일치하고 트랙 목록에 곡명이 있을 때만 쓴다 (오매칭 #12)
- 장르·스타일은 변환표에 있는 것만 우리 목록의 값으로 바꾼다. 장르·스타일은 CC0 데이터라 저장할 수 있다
- 토큰이 없거나 실패해도 등록은 계속된다 (status = SKIPPED / FAILED → AI 추천만 보여 줌)
"""
import re

import requests

import config
from music import normalize

API = "https://api.discogs.com"
UA = {"User-Agent": "DiggerCoursework/0.1"}

GENRE_MAP = {
    "Rock": "록", "Pop": "팝", "Hip Hop": "힙합", "Funk / Soul": "R&B·소울", "Electronic": "일렉트로닉",
    "Jazz": "재즈·블루스", "Blues": "재즈·블루스", "Folk, World, & Country": "포크·어쿠스틱",
    "Stage & Screen": "기타", "Classical": "기타",
}
STYLE_MAP = {
    "Shoegaze": "슈게이즈", "Britpop": "브릿팝", "City Pop": "시티팝", "Dream Pop": "드림팝", "Indie Rock": "인디 록",
    "Indie Pop": "인디 팝", "Synth-pop": "신스팝", "Alternative Rock": "얼터너티브 록", "Post Rock": "포스트 록",
    "Punk": "펑크 록", "Pop Punk": "펑크 록", "Hard Rock": "하드 록·메탈", "Heavy Metal": "하드 록·메탈",
    "Ballad": "발라드", "Power Pop": "인디 팝", "House": "하우스", "Lo-Fi": "로파이", "Ambient": "앰비언트",
    "Synthwave": "신스웨이브", "Trap": "트랩", "Boom Bap": "붐뱁", "RnB/Swing": "R&B", "Contemporary R&B": "R&B",
    "Neo Soul": "네오 소울", "Soul": "소울", "Funk": "펑크(Funk)", "Folk": "포크", "Folk Rock": "포크",
    "Acoustic": "어쿠스틱 팝", "Singer/Songwriter": "싱어송라이터", "Jazz-Funk": "재즈", "Contemporary Jazz": "재즈",
    "Blues Rock": "블루스", "Bossa Nova": "보사노바", "Anison": "애니송", "Shibuya-kei": "시부야케이",
    "Soundtrack": "OST", "Video Game Music": "게임 음악", "Trot": "트로트", "J-pop": "댄스 팝", "K-pop": "댄스 팝",
}
_ARTIST_NUM = re.compile(r"\s*\(\d+\)$")  # Discogs 동명이인 표기 'Ride (2)'


def _get(path, **params):
    r = requests.get(f"{API}/{path}", timeout=15, params=params,
                     headers={**UA, "Authorization": f"Discogs token={config.DISCOGS_TOKEN}"})
    r.raise_for_status()
    return r.json()


def _matches(release, artist_n, title_n):
    artists = [normalize(_ARTIST_NUM.sub("", a.get("name", ""))) for a in release.get("artists", [])]
    if artist_n not in artists:
        return False
    return any(normalize(t.get("title", "")) == title_n for t in release.get("tracklist", []))


def lookup(artist, title):
    """반환 dict: status(FOUND / NOT_FOUND / MISMATCH / FAILED / SKIPPED), genre, subgenres, styles(원문), url"""
    out = {"status": "SKIPPED", "genre": None, "subgenres": [], "styles": [], "url": None}
    if not config.DISCOGS_TOKEN:
        return out
    artist_n, title_n = normalize(artist), normalize(title)
    try:
        res = _get("database/search", artist=artist, track=title, type="release", per_page=5).get("results", [])
        if not res:
            return {**out, "status": "NOT_FOUND"}
        for r in res[:3]:  # 상세 조회는 3개까지만 (요청 수 제한)
            rel = _get(f"releases/{r['id']}")
            if _matches(rel, artist_n, title_n):
                genres, styles = rel.get("genres", []), rel.get("styles", [])
                mapped = [GENRE_MAP[x] for x in genres if x in GENRE_MAP]
                subs = []
                for st in styles:
                    if st in STYLE_MAP and STYLE_MAP[st] not in subs:
                        subs.append(STYLE_MAP[st])
                return {"status": "FOUND", "genre": mapped[0] if mapped else None, "subgenres": subs[:2],
                        "styles": styles[:6], "url": rel.get("uri")}
        return {**out, "status": "MISMATCH"}
    except (requests.RequestException, ValueError, KeyError):
        return {**out, "status": "FAILED"}
