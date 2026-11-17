"""
디깅·매수 테스트 (설계 보고서 7.1 오작동 조건, 8.3 검증 체크리스트)
  python -m unittest discover tests -v
"""
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone

os.environ.setdefault("DIGGER_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402
import gemini_service  # noqa: E402
import music  # noqa: E402
from app import app  # noqa: E402

TAGS = {"moods": ["잔잔한", "쓸쓸한"], "genre": "록", "subgenres": ["인디 록"], "instruments": ["보컬"]}
_seq = [0]


def new_user(name):
    conn = db.get_db_connection()
    uid = conn.execute("INSERT INTO users (username, password_hash, nickname) VALUES (?, 'x', ?)",
                       (name, name)).lastrowid
    conn.commit(); conn.close()
    return uid


def new_song(category="KR", title=None):
    _seq[0] += 1
    n = _seq[0]
    conn = db.get_db_connection()
    mid = music.insert_song(conn, video_id=f"vid{n:08d}", title=title or f"곡{n}", artist=f"가수{n}",
                            category=category, start_sec=30, tags=TAGS)
    conn.commit(); conn.close()
    return mid


def client_for(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
        s["csrf"] = "tok"
    return c


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRFToken": "tok"})


def hide_all_songs():
    conn = db.get_db_connection()
    conn.execute("UPDATE music SET status = 'HIDDEN'")
    conn.commit(); conn.close()


class DigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        hide_all_songs()  # 테스트마다 자기 곡만 풀에 남도록

    def test_normalize(self):
        self.assertEqual(music.normalize("SURL (설)"), "surl")
        self.assertEqual(music.normalize("방백 (傍白)"), "방백")
        self.assertEqual(music.normalize("Love (feat. Someone)"), "love")
        self.assertEqual(music.normalize("A Star In the Sky"), "astarinthesky")

    def test_next_is_blind(self):
        new_song()
        c = client_for(new_user("blind_u"))
        d = post(c, "/api/dig/next").get_json()
        self.assertTrue(d["ok"]); self.assertFalse(d["empty"])
        for hidden in ("title", "artist", "genre", "buy_count"):
            self.assertNotIn(hidden, d)
        self.assertEqual(d["moods"], ["잔잔한", "쓸쓸한"])  # 1순위가 앞

    def test_buy_flow_and_reveal(self):
        mid = new_song(title="공개될 제목")
        c = client_for(new_user("buy_u"))
        self.assertEqual(post(c, "/api/dig/buy").status_code, 409)  # 현재 곡 없이 매수 불가
        post(c, "/api/dig/next")
        d = post(c, "/api/dig/buy").get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["title"], "공개될 제목")
        self.assertEqual(d["rank"], 1); self.assertTrue(d["is_early"]); self.assertEqual(d["tickets_left"], 9)
        self.assertEqual(post(c, "/api/dig/buy").status_code, 409)  # 같은 곡 두 번 불가
        conn = db.get_db_connection()
        self.assertEqual(conn.execute("SELECT buy_count FROM music WHERE id = ?", (mid,)).fetchone()[0], 1)
        conn.close()
        self.assertTrue(post(c, "/api/dig/next").get_json()["empty"])  # 산 곡은 다시 안 나옴

    def test_csrf_required_on_api(self):
        new_song()
        c = client_for(new_user("csrf_u"))
        r = c.post("/api/dig/next", json={})
        self.assertEqual(r.status_code, 400)

    def test_category_filter(self):
        new_song("JP")
        c = client_for(new_user("cat_u"))
        self.assertTrue(post(c, "/api/dig/next", {"category": "KR"}).get_json()["empty"])
        self.assertEqual(post(c, "/api/dig/next", {"category": "JP"}).get_json()["category"], "JP")
        self.assertEqual(post(c, "/api/dig/next", {"category": "XX"}).status_code, 400)

    def test_pass_recorded_only_with_tickets(self):
        a, b = new_song(), new_song()
        uid = new_user("pass_u")
        c = client_for(uid)
        post(c, "/api/dig/next")
        self.assertTrue(post(c, "/api/dig/pass").get_json()["recorded"])
        # 매수권을 모두 쓴 상태 만들기: 오늘 매수 10건
        conn = db.get_db_connection()
        for _ in range(10):
            m = music.insert_song(conn, video_id=f"t{_seq[0]:03d}{_:07d}"[:11], title=f"x{_seq[0]}{_}",
                                  artist="x", category="POP", start_sec=0, tags=TAGS, status="HIDDEN")
            conn.execute("INSERT INTO purchases (user_id, music_id, count_at_buy, is_early) VALUES (?, ?, 0, 1)", (uid, m))
        conn.commit(); conn.close()
        d = post(c, "/api/dig/next").get_json()
        self.assertEqual(d["tickets_left"], 0)
        self.assertFalse(post(c, "/api/dig/pass").get_json()["recorded"])  # 0장이면 기록 안 함
        post(c, "/api/dig/next")
        r = post(c, "/api/dig/buy")
        self.assertEqual(r.status_code, 409)  # 11번째 매수는 거부
        self.assertEqual(r.get_json()["tickets_left"], 0)

    def test_error_hides_after_three_users(self):
        mid = new_song()
        for i in range(3):
            c = client_for(new_user(f"err_u{i}"))
            post(c, "/api/dig/next")
            post(c, "/api/dig/error")
        conn = db.get_db_connection()
        row = conn.execute("SELECT status, error_count FROM music WHERE id = ?", (mid,)).fetchone()
        conn.close()
        self.assertEqual((row["status"], row["error_count"]), ("HIDDEN", 3))

    def test_concurrent_buys_keep_counts_consistent(self):
        mid = new_song()
        clients = [client_for(new_user(f"con_u{i}")) for i in range(8)]
        for c in clients:
            post(c, "/api/dig/next")
        results = []
        barrier = threading.Barrier(len(clients))

        def worker(c):
            barrier.wait()
            results.append(post(c, "/api/dig/buy").status_code)

        threads = [threading.Thread(target=worker, args=(c,)) for c in clients]
        for t in threads: t.start()
        for t in threads: t.join()
        conn = db.get_db_connection()
        buy_count = conn.execute("SELECT buy_count FROM music WHERE id = ?", (mid,)).fetchone()[0]
        counts = sorted(r[0] for r in conn.execute("SELECT count_at_buy FROM purchases WHERE music_id = ?", (mid,)))
        conn.close()
        self.assertEqual(results.count(200), 8)
        self.assertEqual(buy_count, 8)
        self.assertEqual(counts, list(range(8)))  # 0~7이 한 번씩: 매수 순번이 겹치지 않음

    def test_prev_returns_earlier_songs_like_reels(self):
        new_song(); new_song()
        c = client_for(new_user("prev_u"))
        self.assertTrue(post(c, "/api/dig/prev").get_json()["none"])  # 아직 들은 곡이 없음
        first = post(c, "/api/dig/next").get_json()
        post(c, "/api/dig/pass")
        second = post(c, "/api/dig/next").get_json()
        self.assertNotEqual(first["video_id"], second["video_id"])
        back = post(c, "/api/dig/prev").get_json()  # 실수로 넘긴 곡으로 돌아감
        self.assertEqual(back["video_id"], first["video_id"])
        self.assertFalse(back["bought"])
        self.assertNotIn("title", back)  # 돌아가도 여전히 블라인드
        self.assertTrue(post(c, "/api/dig/prev").get_json()["none"])  # 더 이전 곡은 없음
        self.assertEqual(post(c, "/api/dig/buy").status_code, 200)  # 넘겼던 곡도 다시 매수할 수 있음
        fwd = post(c, "/api/dig/next").get_json()  # 다시 내리면 들었던 다음 곡으로
        self.assertEqual(fwd["video_id"], second["video_id"])
        again = post(c, "/api/dig/prev").get_json()  # 매수한 곡으로 돌아가면 공개된 정보
        self.assertTrue(again["bought"])
        self.assertTrue(again["title"].startswith("곡"))
        self.assertEqual(post(c, "/api/dig/buy").status_code, 409)  # 두 번 매수는 안 됨

    def test_prev_from_empty_goes_to_last_song(self):
        new_song()
        c = client_for(new_user("prev_empty_u"))
        only = post(c, "/api/dig/next").get_json()
        post(c, "/api/dig/pass")
        self.assertTrue(post(c, "/api/dig/next").get_json()["empty"])
        back = post(c, "/api/dig/prev").get_json()
        self.assertEqual(back["video_id"], only["video_id"])

    def test_prev_skips_songs_hidden_meanwhile(self):
        new_song(); new_song()
        c = client_for(new_user("prev_hidden_u"))
        post(c, "/api/dig/next"); post(c, "/api/dig/pass")
        post(c, "/api/dig/next")
        conn = db.get_db_connection()
        conn.execute("UPDATE music SET status = 'HIDDEN'")  # 들었던 곡들이 그사이 숨겨짐
        conn.commit(); conn.close()
        self.assertTrue(post(c, "/api/dig/prev").get_json()["none"])

    def test_empty_explains_why_and_reset_brings_passed_back(self):
        new_song(); new_song()
        c = client_for(new_user("reset_u"))
        post(c, "/api/dig/next"); post(c, "/api/dig/pass")
        post(c, "/api/dig/next"); post(c, "/api/dig/buy")
        d = post(c, "/api/dig/next").get_json()
        self.assertTrue(d["empty"])
        self.assertEqual(d["stats"], {"active": 2, "passed": 1, "bought": 1})
        self.assertEqual(post(c, "/api/dig/reset").get_json()["restored"], 1)
        again = post(c, "/api/dig/next").get_json()
        self.assertFalse(again["empty"])  # 넘긴 곡이 다시 나옴 (매수한 곡은 그대로 빠짐)
        post(c, "/api/dig/pass")
        self.assertTrue(post(c, "/api/dig/next").get_json()["empty"])

    def test_empty_pool_has_zero_active(self):
        c = client_for(new_user("nopool_u"))
        d = post(c, "/api/dig/next").get_json()
        self.assertEqual(d["stats"]["active"], 0)

    def test_same_user_error_counts_once(self):
        mid = new_song()
        c = client_for(new_user("err_once_u"))
        post(c, "/api/dig/next"); post(c, "/api/dig/error")
        post(c, "/api/dig/reset")  # 넘긴 곡을 다시 들으며
        post(c, "/api/dig/next"); post(c, "/api/dig/error")  # 같은 곡을 또 보고해도
        conn = db.get_db_connection()
        n = conn.execute("SELECT error_count FROM music WHERE id = ?", (mid,)).fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)  # 한 사람은 한 번만 셈

    def test_mood_genre_filter_and_facets(self):
        conn = db.get_db_connection()
        music.insert_song(conn, video_id="filt0000001", title="잔잔곡", artist="A", category="KR", start_sec=0,
                                 tags={"moods": ["잔잔한"], "genre": "포크·어쿠스틱", "subgenres": [], "instruments": []})
        music.insert_song(conn, video_id="filt0000002", title="강한곡", artist="B", category="KR", start_sec=0,
                          tags={"moods": ["강렬한"], "genre": "록", "subgenres": [], "instruments": []})
        conn.commit(); conn.close()
        c = client_for(new_user("filter_u"))
        f = post(c, "/api/dig/facets", {"moods": ["잔잔한"]}).get_json()
        self.assertEqual(f["matching"], 1)
        self.assertEqual({m["name"]: m["n"] for m in f["moods"]}["강렬한"], 1)  # 다른 무드 칸은 그 무드만 따로 셈
        self.assertEqual({x["name"]: x["n"] for x in f["genres"]}["록"], 0)     # 잔잔한 + 록은 없음
        d = post(c, "/api/dig/next", {"moods": ["잔잔한"], "fresh": True}).get_json()
        self.assertEqual(d["video_id"], "filt0000001")
        post(c, "/api/dig/pass")
        self.assertTrue(post(c, "/api/dig/next", {"moods": ["잔잔한"], "fresh": True}).get_json()["empty"])
        d = post(c, "/api/dig/next", {"genres": ["록"], "fresh": True}).get_json()
        self.assertEqual(d["video_id"], "filt0000002")
        self.assertEqual(post(c, "/api/dig/next", {"moods": ["없는무드"]}).status_code, 400)


class MoodTest(unittest.TestCase):
    """무드 문장 디깅: Gemini 응답은 가짜로 바꿔 끼워, 캐시·제한·검증·점수 추첨만 확인한다"""
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        hide_all_songs()
        self.calls = []
        self._real = gemini_service._call_mood_model
        self.reply = '{"moods": ["잔잔한"], "situations": ["산책"], "times": ["밤"], "instruments": [], ' \
                     '"subgenres": [], "energy": "없음", "genre": "없음"}'

        def fake(text):
            self.calls.append(text)
            return {"raw": self.reply, "seconds": 0.1, "in_tok": 10, "out_tok": 5, "error": ""}
        gemini_service._call_mood_model = fake

    def tearDown(self):
        gemini_service._call_mood_model = self._real

    def song(self, vid, moods, genre="록", times=(), situations=()):
        conn = db.get_db_connection()
        mid = music.insert_song(conn, video_id=vid, title=vid, artist="A", category="KR", start_sec=0,
                                tags={"moods": moods, "genre": genre, "subgenres": [], "instruments": [],
                                      "times": list(times), "situations": list(situations)})
        conn.commit(); conn.close()
        return mid

    def test_interpret_caches_and_validates(self):
        c = client_for(new_user("mood_cache_u"))
        self.reply = '{"moods": ["잔잔한", "없는무드"], "situations": [], "times": ["밤"], "instruments": [], ' \
                     '"subgenres": [], "energy": "없음", "genre": "없음"}'
        d = post(c, "/api/mood/interpret", {"text": "밤에  듣는 잔잔한 노래"}).get_json()
        self.assertTrue(d["ok"]); self.assertFalse(d["cached"])
        self.assertEqual(d["tags"]["moods"], ["잔잔한"])  # 목록 밖 값은 버림
        self.assertEqual([x["text"] for x in d["labels"]], ["잔잔한", "밤"])
        self.assertEqual(d["labels"][0]["g"], "차분한")
        d2 = post(c, "/api/mood/interpret", {"text": "밤에 듣는 잔잔한 노래 "}).get_json()  # 공백만 다른 문장
        self.assertTrue(d2["cached"])
        self.assertEqual(len(self.calls), 1)  # Gemini는 한 번만

    def test_failure_does_not_break(self):
        gemini_service._call_mood_model = lambda t: {"raw": "", "seconds": 1, "in_tok": None, "out_tok": None,
                                                     "error": "TimeoutError"}
        c = client_for(new_user("mood_fail_u"))
        r = post(c, "/api/mood/interpret", {"text": "아무 문장 실패용"})
        self.assertEqual(r.status_code, 503)
        self.assertIn("직접", r.get_json()["error"])
        self.assertEqual(post(c, "/api/dig/next", {"mood_text": "아무 문장 실패용"}).status_code, 400)  # 캐시 없음

    def test_daily_limit_counts_only_real_calls(self):
        c = client_for(new_user("mood_limit_u"))
        old, config.DAILY_MOOD_LIMIT = config.DAILY_MOOD_LIMIT, 2
        try:
            for i in range(2):
                self.assertTrue(post(c, "/api/mood/interpret", {"text": f"제한 확인 문장 {i}"}).get_json()["ok"])
            self.assertTrue(post(c, "/api/mood/interpret", {"text": "제한 확인 문장 0"}).get_json()["cached"])
            self.assertEqual(post(c, "/api/mood/interpret", {"text": "제한 확인 새 문장"}).status_code, 429)
        finally:
            config.DAILY_MOOD_LIMIT = old

    def test_too_long_or_empty(self):
        c = client_for(new_user("mood_len_u"))
        self.assertEqual(post(c, "/api/mood/interpret", {"text": "가" * 101}).status_code, 400)
        self.assertEqual(post(c, "/api/mood/interpret", {"text": "  "}).status_code, 400)

    def test_dig_picks_from_top_half(self):
        self.song("moodbest001", ["잔잔한"], times=["밤"], situations=["산책"])  # 3+2+2 = 7점
        self.song("moodlow0001", ["강렬한"])                                               # 0점
        c = client_for(new_user("mood_pick_u"))
        post(c, "/api/mood/interpret", {"text": "밤에 산책할 때 잔잔한"})
        d = post(c, "/api/dig/next", {"mood_text": "밤에 산책할 때 잔잔한"}).get_json()
        self.assertEqual(d["video_id"], "moodbest001")  # 0점 곡은 묶음(4점 이상)에 못 들어감
        self.assertNotIn("mood_fallback", d)

    def test_no_match_falls_back_to_random(self):
        self.song("moodnone001", ["강렬한"])
        c = client_for(new_user("mood_none_u"))
        post(c, "/api/mood/interpret", {"text": "잔잔한 밤 산책 노래"})
        d = post(c, "/api/dig/next", {"mood_text": "잔잔한 밤 산책 노래"}).get_json()
        self.assertEqual(d["video_id"], "moodnone001")
        self.assertTrue(d["mood_fallback"])


if __name__ == "__main__":
    unittest.main()
