"""
골격 동작 확인 (표준 라이브러리 unittest, 추가 설치 없음)
  python -m unittest discover tests -v
임시 DB를 쓰므로 digger.db는 건드리지 않는다.
"""
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone

TMP = tempfile.mkdtemp()
os.environ["DIGGER_DB"] = os.path.join(TMP, "test.db")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import scoring  # noqa: E402
from app import app  # noqa: E402


def token(client, path):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)


class SkeletonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tag_count = db.init_db()

    def setUp(self):
        self.client = app.test_client()

    def signup(self, username="tester01", nickname="테스터"):
        t = token(self.client, "/signup")
        return self.client.post("/signup", data={"csrf_token": t, "username": username, "nickname": nickname,
                                                 "password": "password123", "password2": "password123"})

    def test_tags_seeded_100(self):
        self.assertEqual(self.tag_count, 100)
        self.assertEqual(db.init_db(), 100)  # 다시 실행해도 그대로

    def test_signup_login_logout(self):
        r = self.signup("flow_user", "흐름")
        self.assertEqual(r.status_code, 302)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("매수권 10/10", page)
        t = token(self.client, "/")
        self.client.post("/logout", data={"csrf_token": t})
        self.assertEqual(self.client.get("/").status_code, 302)  # 로그인 필요
        t = token(self.client, "/login")
        r = self.client.post("/login", data={"csrf_token": t, "username": "flow_user", "password": "password123"})
        self.assertEqual(r.status_code, 302)

    def test_duplicate_and_wrong_password(self):
        self.signup("dup_user", "중복1")
        c2 = app.test_client()
        t = token(c2, "/signup")
        r = c2.post("/signup", data={"csrf_token": t, "username": "dup_user", "nickname": "중복2",
                                     "password": "password123", "password2": "password123"})
        self.assertIn("이미 사용 중", r.get_data(as_text=True))
        t = token(c2, "/login")
        r = c2.post("/login", data={"csrf_token": t, "username": "dup_user", "password": "wrongpass"})
        self.assertIn("올바르지 않습니다", r.get_data(as_text=True))

    def test_csrf_required(self):
        r = self.client.post("/login", data={"username": "x", "password": "y"})
        self.assertEqual(r.status_code, 400)

    def test_admin_forbidden_for_user(self):
        self.signup("plain_user", "일반")
        self.assertEqual(self.client.get("/admin").status_code, 403)

    def test_open_redirect_blocked(self):
        self.signup("redir_user", "리다이렉트")
        t = token(self.client, "/")
        self.client.post("/logout", data={"csrf_token": t})
        t = token(self.client, "/login")
        r = self.client.post("/login?next=//evil.example", data={"csrf_token": t, "username": "redir_user",
                                                                 "password": "password123"})
        self.assertTrue(r.headers["Location"].endswith("/"))

    def test_health(self):
        data = self.client.get("/health").get_json()
        self.assertTrue(data["db"])
        self.assertEqual(data["tags"], 100)
        self.assertNotIn("test-secret", str(data))  # 비밀값은 여부만

    def test_tickets_reset_at_kst_midnight(self):
        conn = db.get_db_connection()
        uid = conn.execute("INSERT INTO users (username, password_hash, nickname) VALUES ('kst_user', 'x', 'KST')").lastrowid
        mid = conn.execute("INSERT INTO music (video_id, title, artist, title_norm, artist_norm, category, start_sec) "
                           "VALUES ('abcdefghijk', 't', 'a', 't', 'a', 'KR', 0)").lastrowid
        # 11/3 23:59:59 KST = 11/3 14:59:59 UTC 에 매수
        conn.execute("INSERT INTO purchases (user_id, music_id, count_at_buy, is_early, created_at) "
                     "VALUES (?, ?, 0, 1, '2026-11-03 14:59:59')", (uid, mid))
        conn.commit()
        before = datetime(2026, 11, 3, 14, 59, 59, tzinfo=timezone.utc)  # 아직 11/3 KST
        after = datetime(2026, 11, 3, 15, 0, 0, tzinfo=timezone.utc)     # 11/4 00:00 KST
        self.assertEqual(scoring.tickets_left(conn, uid, before), 9)
        self.assertEqual(scoring.tickets_left(conn, uid, after), 10)
        conn.close()


if __name__ == "__main__":
    unittest.main()
