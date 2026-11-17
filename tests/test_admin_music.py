"""
관리자 곡 관리 테스트: 목록·검색, 정보·태그 수정(출처 기록), 영상 교체, 숨김/복구, 삭제 조건, 권한
  python -m unittest discover tests -v
"""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("DIGGER_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import music  # noqa: E402
import youtube_service as yt  # noqa: E402
from app import app  # noqa: E402

_n = [0]
TAGS = {"moods": ["잔잔한", "쓸쓸한"], "genre": "록", "subgenres": ["인디 록"], "instruments": ["보컬"]}


def client(role):
    _n[0] += 1
    conn = db.get_db_connection()
    uid = conn.execute("INSERT INTO users (username, password_hash, nickname, role) VALUES (?, 'x', ?, ?)",
                       (f"am{_n[0]}", f"관리곡{_n[0]}", role)).lastrowid
    conn.commit(); conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
        s["csrf"] = "tok"
    return c, uid


def song(title=None):
    _n[0] += 1
    conn = db.get_db_connection()
    mid = music.insert_song(conn, video_id=f"am{_n[0]:09d}", title=title or f"관리곡{_n[0]}", artist="가수",
                            category="KR", start_sec=30, tags=TAGS, origin="AI")
    conn.commit(); conn.close()
    return mid


def row(sql, *args):
    conn = db.get_db_connection()
    r = conn.execute(sql, args).fetchone()
    conn.close()
    return r


FORM = {"csrf_token": "tok", "title": "고친 제목", "artist": "고친 가수", "category": "JP", "start": "1:05",
        "intro": "", "video_url": "", "mood1": "몽환적인", "mood2": "잔잔한", "mood3": "", "genre": "팝", "energy": "낮음"}


class AdminMusicTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.saved = yt.fetch_video

    def tearDown(self):
        yt.fetch_video = self.saved

    def test_only_admin(self):
        c, _ = client("USER")
        mid = song()
        self.assertEqual(c.get("/admin/music").status_code, 403)
        self.assertEqual(c.post(f"/admin/music/{mid}/status", data={"to": "HIDDEN", "csrf_token": "tok"}).status_code, 403)

    def test_list_and_search(self):
        a, _ = client("ADMIN")
        song("검색되는곡%_")
        page = a.get("/admin/music?q=검색되는곡%25_").get_data(as_text=True)
        self.assertIn("검색되는곡%_", page)
        self.assertIn("맞는 곡이 없습니다", a.get("/admin/music?q=없는검색어").get_data(as_text=True))

    def test_edit_info_and_tags_keeps_history(self):
        a, _ = client("ADMIN")
        mid = song()
        r = a.post(f"/admin/music/{mid}", data={**FORM, "subgenres": ["드림팝"], "situations": ["산책"]})
        self.assertEqual(r.status_code, 302)
        m = row("SELECT title, artist, title_norm, category, start_sec FROM music WHERE id = ?", mid)
        self.assertEqual(tuple(m), ("고친 제목", "고친 가수", "고친제목", "JP", 65))
        conn = db.get_db_connection()
        tags = {r["name"]: (r["origin"], r["accepted"], r["rank"]) for r in conn.execute(
            "SELECT t.name, mt.origin, mt.accepted, mt.rank FROM music_tags mt JOIN tags t ON t.id = mt.tag_id "
            "WHERE mt.music_id = ?", (mid,))}
        conn.close()
        self.assertEqual(tags["몽환적인"], ("ADMIN", 1, 1))   # 새로 넣은 태그
        self.assertEqual(tags["잔잔한"], ("AI", 1, 2))         # 남은 태그는 출처 그대로, 순위만 바뀜
        self.assertEqual(tags["쓸쓸한"], ("AI", 0, None))      # 뺀 태그는 기록만 남음
        self.assertEqual(tags["록"], ("AI", 0, None))
        self.assertEqual(music.song_tags(db.get_db_connection(), mid)["genre"], "팝")

    def test_edit_rejects_bad_input(self):
        a, _ = client("ADMIN")
        mid = song()
        a.post(f"/admin/music/{mid}", data={**FORM, "mood1": "", "genre": ""})
        self.assertEqual(row("SELECT title FROM music WHERE id = ?", mid)[0][:3], "관리곡")  # 저장 안 됨
        a.post(f"/admin/music/{mid}", data={**FORM, "mood2": "몽환적인"})  # 같은 분위기 두 번
        self.assertEqual(row("SELECT title FROM music WHERE id = ?", mid)[0][:3], "관리곡")

    def test_replace_video_resets_errors(self):
        a, _ = client("ADMIN")
        mid = song()
        conn = db.get_db_connection()
        conn.execute("UPDATE music SET error_count = 2 WHERE id = ?", (mid,)); conn.commit(); conn.close()
        yt.fetch_video = lambda v: {"video_id": v, "title": "t", "channel": "c", "category_id": "10", "live": False,
                                    "duration": 240, "age_restricted": False, "kr_blocked": False, "public": True,
                                    "embeddable": True, "views": 100}
        form = {**FORM, "title": "영상교체곡"}
        a.post(f"/admin/music/{mid}", data={**form, "video_url": "https://youtu.be/NEWvideo001"})
        self.assertEqual(tuple(row("SELECT video_id, error_count FROM music WHERE id = ?", mid)), ("NEWvideo001", 0))
        yt.fetch_video = lambda v: (_ for _ in ()).throw(yt.YouTubeError("QUOTA"))
        a.post(f"/admin/music/{mid}", data={**form, "video_url": "https://youtu.be/NEWvideo002"})
        self.assertEqual(row("SELECT video_id FROM music WHERE id = ?", mid)[0], "NEWvideo001")  # 확인 못 하면 안 바꿈

    def test_hide_and_restore(self):
        a, _ = client("ADMIN")
        mid = song()
        a.post(f"/admin/music/{mid}/status", data={"to": "HIDDEN", "csrf_token": "tok"})
        self.assertEqual(row("SELECT status FROM music WHERE id = ?", mid)[0], "HIDDEN")
        a.post(f"/admin/music/{mid}/status", data={"to": "ACTIVE", "csrf_token": "tok"})
        self.assertEqual(row("SELECT status FROM music WHERE id = ?", mid)[0], "ACTIVE")

    def test_delete_only_without_history(self):
        a, uid = client("ADMIN")
        free, bought = song(), song()
        conn = db.get_db_connection()
        conn.execute("INSERT INTO purchases (user_id, music_id, count_at_buy, is_early) VALUES (?, ?, 0, 1)", (uid, bought))
        conn.execute("INSERT INTO passes (user_id, music_id) VALUES (?, ?)", (uid, free))
        conn.commit(); conn.close()
        a.post(f"/admin/music/{free}/delete", data={"confirm": "틀린 제목", "csrf_token": "tok"})
        self.assertIsNotNone(row("SELECT 1 FROM music WHERE id = ?", free))   # 확인 문구가 다르면 안 지움
        title = row("SELECT title FROM music WHERE id = ?", free)[0]
        a.post(f"/admin/music/{free}/delete", data={"confirm": title, "csrf_token": "tok"})
        self.assertIsNone(row("SELECT 1 FROM music WHERE id = ?", free))
        self.assertIsNone(row("SELECT 1 FROM music_tags WHERE music_id = ?", free))
        title = row("SELECT title FROM music WHERE id = ?", bought)[0]
        a.post(f"/admin/music/{bought}/delete", data={"confirm": title, "csrf_token": "tok"})
        self.assertIsNotNone(row("SELECT 1 FROM music WHERE id = ?", bought))  # 매수 기록이 있으면 못 지움


if __name__ == "__main__":
    unittest.main()
