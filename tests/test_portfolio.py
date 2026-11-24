"""
포트폴리오·디깅 포인트·뱃지 테스트 (설계 보고서 4.3, 4.4)
  python -m unittest discover tests -v
"""
import json
import os
import re
import sys
import tempfile
import unittest

os.environ.setdefault("DIGGER_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import music  # noqa: E402
import scoring  # noqa: E402
from app import app  # noqa: E402

_n = [0]


def user():
    _n[0] += 1
    conn = db.get_db_connection()
    uid = conn.execute("INSERT INTO users (username, password_hash, nickname) VALUES (?, 'x', ?)",
                       (f"pf{_n[0]}", f"포폴{_n[0]}")).lastrowid
    conn.commit(); conn.close()
    return uid


def song(buy_count=0, status="ACTIVE"):
    _n[0] += 1
    conn = db.get_db_connection()
    mid = music.insert_song(conn, video_id=f"pf{_n[0]:09d}", title=f"포폴곡{_n[0]}", artist="가수", category="KR",
                            start_sec=10, tags={"moods": ["잔잔한"], "genre": "록"}, status=status)
    conn.execute("UPDATE music SET buy_count = ? WHERE id = ?", (buy_count, mid))
    conn.commit(); conn.close()
    return mid


def buy(uid, mid, count_at_buy):
    conn = db.get_db_connection()
    conn.execute("INSERT INTO purchases (user_id, music_id, count_at_buy, is_early) VALUES (?, ?, ?, ?)",
                 (uid, mid, count_at_buy, int(count_at_buy < 10)))
    conn.commit(); conn.close()


class PointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def test_formula_matches_design_example(self):
        self.assertEqual(scoring.purchase_points(25, 4, True), 40)   # (25 − 4 − 1) × 2
        self.assertEqual(scoring.purchase_points(25, 14, False), 10)
        self.assertEqual(scoring.purchase_points(5, 4, True), 0)     # 내가 마지막 매수자면 0

    def test_sql_equals_python(self):
        uid = user()
        buy(uid, song(25), 4)      # 40
        buy(uid, song(25), 14)     # 10
        buy(uid, song(1), 0)       # 0
        conn = db.get_db_connection()
        self.assertEqual(scoring.user_points(conn, uid), 50)
        conn.close()

    def test_badges(self):
        uid = user()
        conn = db.get_db_connection()
        names = lambda: {b["name"] for b in scoring.badges(conn, uid) if b["earned"]}  # noqa: E731
        self.assertEqual(names(), set())
        buy(uid, song(12), 11)                     # 초기 발굴 아님
        self.assertEqual(names(), {"첫 삽"})
        buy(uid, song(30), 2)                      # 초기 발굴, 지금 30회 → (30 − 2 − 1) × 2 = 54점
        self.assertEqual(names(), {"첫 삽", "First Digger", "촉 좋은 디거"})
        buy(uid, song(130), 3)                     # (130 − 3 − 1) × 2 = 252 → 합 306점
        self.assertIn("황금 곡괭이", names())
        conn.close()

    def test_portfolio_page(self):
        uid = user()
        hidden = song(5, status="HIDDEN")
        buy(uid, song(25), 4)
        buy(uid, hidden, 1)
        c = app.test_client()
        self.assertEqual(c.get("/portfolio").status_code, 302)  # 로그인 필요
        with c.session_transaction() as s:
            s["user_id"] = uid
        page = c.get("/portfolio").get_data(as_text=True)
        self.assertIn('<span class="big">46</span>', page)  # 40 + (5 − 1 − 1) × 2 = 46
        self.assertIn("5번째 → 25회", page)
        self.assertIn("디깅 풀에서 빠진 곡", page)
        self.assertIn("First Digger", page)
        self.assertEqual(page.count('class="play"'), 1)      # 숨긴 곡은 듣기 버튼 없음
        sorted_page = c.get("/portfolio?sort=points").get_data(as_text=True)
        self.assertLess(sorted_page.index("5번째"), sorted_page.index("2번째"))

    def test_empty_portfolio(self):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = user()
        page = c.get("/portfolio").get_data(as_text=True)
        self.assertIn("아직 매수한 곡이 없어요", page)
        self.assertNotIn("iframe_api", page)

    def test_playlist_page(self):
        uid = user()
        a, hidden = song(3), song(2, status="HIDDEN")
        buy(uid, a, 1)
        buy(uid, hidden, 0)
        c = app.test_client()
        self.assertEqual(c.get("/playlist").status_code, 302)
        with c.session_transaction() as s:
            s["user_id"] = uid
        page = c.get("/playlist?order=shuffle").get_data(as_text=True)
        data = json.loads(re.search(r'id="pl-data">(.*?)</script>', page, re.S).group(1))
        self.assertEqual(len(data["songs"]), 1)              # 숨긴 곡은 재생 목록에서 빠짐
        self.assertEqual(data["order"], "shuffle")
        self.assertEqual(data["songs"][0]["rank"], 2)
        self.assertIn("전체 듣기", c.get("/portfolio").get_data(as_text=True))

    def test_playlist_empty(self):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = user()
        page = c.get("/playlist").get_data(as_text=True)
        self.assertIn("아직 들을 곡이 없어요", page)
        self.assertNotIn("iframe_api", page)

    def test_hide_from_playlist_keeps_points(self):
        uid, other = user(), user()
        a, b = song(25), song(3)
        buy(uid, a, 4)
        buy(uid, b, 1)
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["csrf"] = "tok"
        c.post(f"/portfolio/{a}/playlist", data={"action": "hide", "csrf_token": "tok"})
        page = c.get("/playlist").get_data(as_text=True)
        data = json.loads(re.search(r'id="pl-data">(.*?)</script>', page, re.S).group(1))
        self.assertEqual([x["id"] for x in data["songs"]], [b])            # 뺀 곡은 플레이리스트에서 빠짐
        pf = c.get("/portfolio").get_data(as_text=True)
        self.assertIn('<span class="big">42</span>', pf)                   # 포인트는 그대로 (40 + 2)
        self.assertIn("플레이리스트에서 뺀 곡 1곡", pf)
        c.post(f"/portfolio/{a}/playlist", data={"action": "show", "csrf_token": "tok"})
        self.assertNotIn("플레이리스트에서 뺀 곡", c.get("/portfolio").get_data(as_text=True))
        r = c.post("/api/playlist/hide", json={"music_id": b}, headers={"X-CSRFToken": "tok"})
        self.assertTrue(r.get_json()["ok"])
        # 남의 곡·매수하지 않은 곡은 뺄 수 없음
        other_song = song(1)
        buy(other, other_song, 0)
        r = c.post("/api/playlist/hide", json={"music_id": other_song}, headers={"X-CSRFToken": "tok"})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
