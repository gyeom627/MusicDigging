"""
외부 API 연결 점검 — PythonAnywhere Bash 콘솔에서 실행해 무료 계정의 외부 접속 허용 목록을 통과하는지 확인한다.
  cd ~/digger && python scripts/check_apis.py
키가 없는 항목은 건너뛴다. 키 값은 화면에 출력하지 않는다.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402  (.env 로드)

try:
    import truststore  # 로컬 학교망용. 서버에는 없어도 된다
    truststore.inject_into_ssl()
except ImportError:
    pass

import requests  # noqa: E402

UA = {"User-Agent": "DiggerCoursework/0.1"}


def hide(text):
    for secret in (config.GEMINI_API_KEY, config.YOUTUBE_API_KEY, config.DISCOGS_TOKEN):
        if secret:
            text = text.replace(secret, "***")
    return text[:200]


def run(name, fn):
    t0 = time.time()
    try:
        detail = fn()
        print(f"[OK]   {name} ({time.time() - t0:.1f}s) {detail}")
    except Exception as e:  # 어떤 오류든 원인만 보여 주고 다음 항목으로
        print(f"[FAIL] {name} ({time.time() - t0:.1f}s) {type(e).__name__}: {hide(str(e))}")


def gemini():
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.GEMINI_API_KEY, http_options=types.HttpOptions(
        timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)))
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents="OK라고만 답해.")
    return f"model={config.GEMINI_MODEL} 응답={resp.text.strip()[:20]!r}"


def youtube():
    r = requests.get("https://www.googleapis.com/youtube/v3/videos", headers=UA, timeout=15,
                     params={"part": "statistics,status", "id": "jNQXAC9IVRw", "key": config.YOUTUBE_API_KEY})
    r.raise_for_status()
    items = r.json().get("items", [])
    return f"영상 {len(items)}개, viewCount 있음={bool(items and 'viewCount' in items[0]['statistics'])}"


def youtube_search():
    r = requests.get("https://www.googleapis.com/youtube/v3/search", headers=UA, timeout=15,
                     params={"part": "snippet", "q": "JANNABI HONG KONG", "type": "video", "maxResults": 3,
                             "key": config.YOUTUBE_API_KEY})
    r.raise_for_status()
    return f"검색 결과 {len(r.json().get('items', []))}개 (search.list는 할당량을 많이 쓰므로 점검 때만)"


def discogs():
    r = requests.get("https://api.discogs.com/database/search", timeout=15,
                     headers={**UA, "Authorization": f"Discogs token={config.DISCOGS_TOKEN}"},
                     params={"artist": "Ride", "track": "Vapour Trail", "type": "release", "per_page": 1})
    r.raise_for_status()
    res = r.json().get("results", [])
    return f"결과 {len(res)}개, 장르={res[0].get('genre') if res else None}, 스타일={res[0].get('style') if res else None}"


if __name__ == "__main__":
    checks = [("Gemini", config.GEMINI_API_KEY, gemini), ("YouTube videos.list", config.YOUTUBE_API_KEY, youtube),
              ("YouTube search.list", config.YOUTUBE_API_KEY, youtube_search), ("Discogs", config.DISCOGS_TOKEN, discogs)]
    for name, key, fn in checks:
        if key:
            run(name, fn)
        else:
            print(f"[SKIP] {name} — .env에 키가 없습니다")
