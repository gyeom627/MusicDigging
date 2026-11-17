"""
시드 곡 넣기 — AI 검증 때 직접 확정한 태그를 그대로 디깅 풀에 넣는다 (origin = USER).
  python scripts/seed_songs.py                 # data/seed_songs.csv
  python scripts/seed_songs.py 다른파일.csv
CSV 칸: video_url,start_sec,title,artist,category(KR/JP/POP),my_mood,my_genre,my_subgenres,my_instruments
- 여러 값은 ; 로 구분, my_mood는 적은 순서가 1·2·3순위
- 이미 있는 곡(같은 영상 또는 같은 아티스트·제목)은 건너뛴다 → 여러 번 실행해도 안전
- known 칸이 '유명'인 줄은 조회수 기준(곡 단위 100만 회 미만)에 걸리므로 넣지 않는다
- 시드 곡은 관리자가 조회수를 확인하고 넣는 것으로 하며, 이 스크립트는 YouTube API를 부르지 않는다
"""
import csv
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from db import get_db_connection  # noqa: E402
from music import extract_video_id, insert_song  # noqa: E402


def split(v):
    return [x.strip() for x in (v or "").split(";") if x.strip()]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "seed_songs.csv")
    conn = get_db_connection()
    added = skipped = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        for no, r in enumerate(csv.DictReader(f), 1):
            label = f"{no}. {r.get('title')} / {r.get('artist')}"
            if r.get("known") == "유명":
                print(f"건너뜀(유명곡): {label}"); skipped += 1; continue
            vid = extract_video_id(r.get("video_url", ""))
            category = (r.get("category") or "").strip().upper()
            if not vid or category not in config.CATEGORIES:
                print(f"건너뜀(링크 또는 category 오류): {label}"); skipped += 1; continue
            tags = {"moods": split(r.get("my_mood")), "genre": (r.get("my_genre") or "").strip() or None,
                    "subgenres": split(r.get("my_subgenres")), "instruments": split(r.get("my_instruments"))}
            if not tags["moods"]:
                print(f"건너뜀(분위기 없음): {label}"); skipped += 1; continue
            try:
                insert_song(conn, video_id=vid, title=r["title"].strip(), artist=r["artist"].strip(),
                            category=category, start_sec=int(r.get("start_sec") or 0), tags=tags)
                conn.commit(); added += 1
                print(f"추가: {label}")
            except sqlite3.IntegrityError:
                conn.rollback(); skipped += 1
                print(f"건너뜀(이미 있음): {label}")
            except ValueError as e:
                conn.rollback(); skipped += 1
                print(f"건너뜀({e}): {label}")
    total = conn.execute("SELECT COUNT(*) FROM music WHERE status = 'ACTIVE'").fetchone()[0]
    conn.close()
    print(f"\n추가 {added}곡, 건너뜀 {skipped}곡 → 디깅 풀(ACTIVE) {total}곡")


if __name__ == "__main__":
    main()
