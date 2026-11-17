"""
곡 제보 테스트 (설계 보고서 4.5, 4.7, 7.2 #7~#13, #29~#31)
YouTube·Discogs·Gemini는 가짜로 바꿔 끼워, 서버의 판정·재검사·출처 기록·제한만 확인한다
  python -m unittest discover tests -v
"""
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("DIGGER_DB", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import db  # noqa: E402
import discogs_service  # noqa: E402
import gemini_service  # noqa: E402
import youtube_service as yt  # noqa: E402
from app import app  # noqa: E402
from submit import guess_artist_title  # noqa: E402

_n = [0]


def video(vid, **kw):
    v = {"video_id": vid, "title": "새소년 - 난춘 [MV]", "channel": "새소년", "category_id": "10", "live": False,
         "duration": 240, "age_restricted": False, "kr_blocked": False, "public": True, "embeddable": True,
         "views": 5000}
    v.update(kw)
    return v


def user(role="USER"):
    _n[0] += 1
    conn = db.get_db_connection()
    uid = conn.execute("INSERT INTO users (username, password_hash, nickname, role) VALUES (?, 'x', ?, ?)",
                       (f"sub{_n[0]}", f"제보{_n[0]}", role)).lastrowid
    conn.commit(); conn.close()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
        s["csrf"] = "tok"
    return c, uid


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={"X-CSRFToken": "tok"})


AI_JSON = json.dumps({"is_music": True, "moods": ["쓸쓸한", "잔잔한"], "energy": "낮음", "situations": ["산책"],
                      "times": ["밤"], "instruments": ["어쿠스틱 기타"], "genre": "록", "subgenres": ["인디 록"]},
                     ensure_ascii=False)


class SubmitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()

    def setUp(self):
        self.videos = {}
        self.views = {"status": "PASS", "reason": "", "over": None, "mv": None}
        self.web = {"status": "FOUND", "genre": "포크·어쿠스틱", "subgenres": ["포크"], "styles": ["Folk"],
                    "url": "https://www.discogs.com/release/1"}
        self.ai_calls = 0
        self.saved = (yt.fetch_video, yt.song_views, discogs_service.lookup, gemini_service._call_audio_model)
        yt.fetch_video = lambda vid: self.videos.get(vid)
        yt.song_views = lambda a, t, vid: self.views
        discogs_service.lookup = lambda a, t: self.web

        def fake_audio(vid, start):
            self.ai_calls += 1
            return {"raw": AI_JSON, "seconds": 3.0, "in_tok": 100, "out_tok": 20, "error": ""}
        gemini_service._call_audio_model = fake_audio

    def tearDown(self):
        yt.fetch_video, yt.song_views, discogs_service.lookup, gemini_service._call_audio_model = self.saved

    def new_vid(self, **kw):
        _n[0] += 1
        vid = f"sv{_n[0]:09d}"
        self.videos[vid] = video(vid, **kw)
        return vid

    def form(self, title="난춘", artist="새소년", start=30):
        return {"title": title, "artist": artist, "category": "KR", "start_sec": start}

    def flow(self, c, vid, **form):
        r = post(c, "/api/submit/check", {"url": f"https://youtu.be/{vid}"})
        self.assertEqual(r.status_code, 200, r.get_json())
        return post(c, "/api/submit/suggest", self.form(**form)).get_json()

    def test_guess_artist_title(self):
        self.assertEqual(guess_artist_title("새소년 - 난춘 [MV]", "x"), ("새소년", "난춘"))
        self.assertEqual(guess_artist_title("Vapour Trail", "Ride - Topic"), ("Ride", "Vapour Trail"))

    def test_check_blocks_bad_videos(self):
        c, _ = user()
        for kw, word in (({"embeddable": False}, "퍼가기"), ({"views": 1_200_000}, "만 회"), ({"public": False}, "공개"),
                         ({"age_restricted": True}, "연령"), ({"kr_blocked": True}, "한국"), ({"live": True}, "라이브")):
            vid = self.new_vid(**kw)
            r = post(c, "/api/submit/check", {"url": f"https://www.youtube.com/watch?v={vid}"})
            self.assertEqual(r.status_code, 400)
            self.assertIn(word, r.get_json()["error"])
        self.assertEqual(post(c, "/api/submit/check", {"url": "https://example.com"}).status_code, 400)

    def test_full_flow_records_origins(self):
        c, uid = user()
        vid = self.new_vid()
        d = self.flow(c, vid)
        self.assertTrue(d["ok"])
        self.assertEqual(d["selected"]["genre"], "포크·어쿠스틱")          # Discogs가 일치하면 Discogs 장르
        self.assertEqual(d["suggested"]["genre"], {"록": "AI", "포크·어쿠스틱": "DISCOGS"})
        self.assertEqual(d["selected"]["subgenres"], ["포크", "인디 록"])
        tags = dict(d["selected"])
        tags["moods"] = ["잔잔한", "쓸쓸한", "몽환적인"]   # 순서 바꾸고 하나 추가
        tags["instruments"] = []                          # 추천 끔
        r = post(c, "/api/submit/publish", {**self.form(), "intro": "밤 산책용", "tags": tags})
        self.assertEqual(r.status_code, 200, r.get_json())
        mid = r.get_json()["music_id"]
        conn = db.get_db_connection()
        rows = {(x["name"]): (x["origin"], x["accepted"], x["rank"], x["ai_rank"]) for x in conn.execute(
            "SELECT t.name, mt.origin, mt.accepted, mt.rank, mt.ai_rank FROM music_tags mt JOIN tags t ON t.id = mt.tag_id "
            "WHERE mt.music_id = ?", (mid,))}
        song = conn.execute("SELECT status, audio_status, web_status, submitted_by FROM music WHERE id = ?", (mid,)).fetchone()
        conn.close()
        self.assertEqual(rows["잔잔한"], ("AI", 1, 1, 2))      # 등록자 1순위, AI는 2순위
        self.assertEqual(rows["몽환적인"], ("USER", 1, 3, None))
        self.assertEqual(rows["어쿠스틱 기타"], ("AI", 0, None, None))  # 끈 추천은 accepted = 0
        self.assertEqual(rows["록"], ("AI", 0, None, None))
        self.assertEqual(rows["포크·어쿠스틱"], ("DISCOGS", 1, None, None))
        self.assertEqual(tuple(song), ("REVIEW", "DONE", "FOUND", uid))  # 사용자 제보는 모두 관리자 승인 대기
        self.assertEqual(post(c, "/api/submit/publish", {**self.form(), "tags": tags}).status_code, 409)  # 세션 끝남

    def test_song_level_views_reject(self):
        c, _ = user()
        self.views = {"status": "REJECT", "reason": "같은 곡의 다른 영상 조회수가 100만 회를 넘어요: X [MV]",
                      "over": "X [MV]", "mv": None}
        d = self.flow(c, self.new_vid(), title="인기곡", artist="유명가수")
        self.assertFalse(d["ok"]); self.assertTrue(d["rejected"])
        self.assertEqual(self.ai_calls, 0)  # 거부되면 AI를 부르지 않음

    def test_review_when_not_found_or_not_music(self):
        c, _ = user()
        self.views = {"status": "REVIEW", "reason": "검색에서 찾지 못함", "over": None, "mv": None}
        vid = self.new_vid()
        d = self.flow(c, vid, title="리뷰곡", artist="리뷰가수")
        r = post(c, "/api/submit/publish", {**self.form("리뷰곡", "리뷰가수"), "tags": d["selected"]}).get_json()
        self.assertEqual(r["status"], "REVIEW")

    def test_changed_title_after_search_must_redo(self):
        c, _ = user()
        d = self.flow(c, self.new_vid(), title="바꿀곡", artist="가수A")
        r = post(c, "/api/submit/publish", {**self.form("다른곡", "가수A"), "tags": d["selected"]})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.get_json()["redo"])

    def test_recheck_on_publish(self):
        c, _ = user()
        vid = self.new_vid()
        d = self.flow(c, vid, title="재검사곡", artist="가수B")
        self.videos[vid]["embeddable"] = False  # 확인 뒤에 퍼가기가 막힘
        r = post(c, "/api/submit/publish", {**self.form("재검사곡", "가수B"), "tags": d["selected"]})
        self.assertEqual(r.status_code, 400)
        yt.fetch_video = lambda v: (_ for _ in ()).throw(yt.YouTubeError("QUOTA"))
        self.videos[vid]["embeddable"] = True
        r = post(c, "/api/submit/publish", {**self.form("재검사곡", "가수B"), "tags": d["selected"]})
        self.assertEqual(r.status_code, 503)  # 조회가 안 되면 보류

    def test_same_video_not_reanalyzed(self):
        c, _ = user()
        vid = self.new_vid()
        self.flow(c, vid, title="한번만", artist="가수C")
        post(c, "/api/submit/suggest", self.form("한번만", "가수C"))
        self.assertEqual(self.ai_calls, 1)
        post(c, "/api/submit/suggest", self.form("한번만", "가수C", start=40))  # 시작 시점을 바꾸면 다시
        self.assertEqual(self.ai_calls, 2)

    def test_ai_failure_still_allows_manual(self):
        c, _ = user()
        gemini_service._call_audio_model = lambda v, s: {"raw": "", "seconds": 60, "in_tok": None, "out_tok": None,
                                                         "error": "Timeout"}
        d = self.flow(c, self.new_vid(), title="실패곡", artist="가수D")
        self.assertTrue(d["ok"]); self.assertEqual(d["audio_status"], "FAILED")
        self.assertEqual(d["selected"]["moods"], [])
        tags = {"moods": ["신나는"], "genre": "팝", "subgenres": [], "situations": [], "times": [], "instruments": []}
        r = post(c, "/api/submit/publish", {**self.form("실패곡", "가수D"), "tags": tags})
        self.assertEqual(r.status_code, 200, r.get_json())

    def test_tag_rules_on_publish(self):
        c, _ = user()
        d = self.flow(c, self.new_vid(), title="규칙곡", artist="가수E")
        bad = dict(d["selected"], moods=[])
        self.assertIn("분위기", post(c, "/api/submit/publish", {**self.form("규칙곡", "가수E"), "tags": bad}).get_json()["error"])
        bad = dict(d["selected"], moods=["없는값"])
        self.assertEqual(post(c, "/api/submit/publish", {**self.form("규칙곡", "가수E"), "tags": bad}).status_code, 400)

    def test_duplicates_and_daily_limit(self):
        c, uid = user()
        vid = self.new_vid()
        d = self.flow(c, vid, title="중복곡", artist="가수F")
        post(c, "/api/submit/publish", {**self.form("중복곡", "가수F"), "tags": d["selected"]})
        self.assertEqual(post(c, "/api/submit/check", {"url": f"https://youtu.be/{vid}"}).status_code, 409)
        r = self.flow(c, self.new_vid(), title="중복곡 (Live)", artist="가수F")  # 정규화하면 같은 곡
        self.assertIn("이미 등록된 곡", r["error"])
        old, config.DAILY_SUBMIT_LIMIT = config.DAILY_SUBMIT_LIMIT, 1
        try:
            d = self.flow(c, self.new_vid(), title="제한곡", artist="가수F")
            r = post(c, "/api/submit/publish", {**self.form("제한곡", "가수F"), "tags": d["selected"]})
            self.assertEqual(r.status_code, 429)
        finally:
            config.DAILY_SUBMIT_LIMIT = old

    def test_admin_approves_review(self):
        c, _ = user()
        self.views = {"status": "REVIEW", "reason": "검색에서 찾지 못함", "over": None, "mv": None}
        d = self.flow(c, self.new_vid(), title="승인곡", artist="가수H")
        mid = post(c, "/api/submit/publish", {**self.form("승인곡", "가수H"), "tags": d["selected"]}).get_json()["music_id"]
        self.assertEqual(c.post(f"/admin/music/{mid}/review", data={"action": "approve", "csrf_token": "tok"}).status_code, 403)
        a, _ = user("ADMIN")
        page = a.get("/admin").get_data(as_text=True)
        self.assertIn("승인곡", page)
        a.post(f"/admin/music/{mid}/review", data={"action": "approve", "csrf_token": "tok",
                                                    "title": "승인곡", "artist": "가수H"})
        conn = db.get_db_connection()
        self.assertEqual(conn.execute("SELECT status FROM music WHERE id = ?", (mid,)).fetchone()[0], "ACTIVE")
        conn.close()

    def test_admin_submission_goes_live_and_fix_on_approve(self):
        a, _ = user("ADMIN")
        d = self.flow(a, self.new_vid(), title="관리자곡", artist="가수I")
        r = post(a, "/api/submit/publish", {**self.form("관리자곡", "가수I"), "tags": d["selected"]}).get_json()
        self.assertEqual(r["status"], "ACTIVE")  # 관리자 등록 + 자동 확인 통과 → 바로 공개
        c, _ = user()
        d = self.flow(c, self.new_vid(), title="I'll Love The Evening(전역을 사랑하겠어)", artist="연정")
        mid = post(c, "/api/submit/publish", {**self.form("I'll Love The Evening(전역을 사랑하겠어)", "연정"),
                                              "tags": d["selected"]}).get_json()["music_id"]
        a.post(f"/admin/music/{mid}/review", data={"action": "approve", "csrf_token": "tok",
                                                    "title": "I'll Love The Evening (저녁을 사랑하겠어)", "artist": "연정"})
        conn = db.get_db_connection()
        row = conn.execute("SELECT status, title FROM music WHERE id = ?", (mid,)).fetchone()
        conn.close()
        self.assertEqual(tuple(row), ("ACTIVE", "I'll Love The Evening (저녁을 사랑하겠어)"))

    def test_highlight_must_fit(self):
        c, _ = user()
        d = self.flow(c, self.new_vid(duration=80), title="짧은곡", artist="가수G", start=30)
        self.assertIn("영상 길이", d["error"])


class SongViewsTest(unittest.TestCase):
    """곡 단위 조회수 판정 (#29 다른 영상으로 우회, #30 같은 곡 판정 실패)"""
    def setUp(self):
        self.saved = yt._get
        self.items = []

    def tearDown(self):
        yt._get = self.saved

    def fake(self, vids):
        def _get(path, **params):
            if path == "search":
                return {"items": [{"id": {"videoId": v["id"]}} for v in vids if v.get("in_search", True)]}
            ids = params["id"].split(",")
            return {"items": [{"id": v["id"], "snippet": {"title": v["title"], "channelTitle": v.get("ch", "")},
                               "contentDetails": {"duration": "PT4M"},
                               "status": {"privacyStatus": "public", "embeddable": True},
                               "statistics": {"viewCount": str(v["views"])} if v["views"] is not None else {}}
                              for v in vids if v["id"] in ids]}
        yt._get = _get

    def test_popular_mv_rejects_lyric_video(self):
        self.fake([{"id": "mine0000001", "title": "새소년 - 난춘 (가사)", "views": 3000},
                   {"id": "mv000000001", "title": "새소년 (SE SO NEON) - 난춘 [Official MV]", "views": 4_000_000}])
        r = yt.song_views("새소년", "난춘", "mine0000001")
        self.assertEqual(r["status"], "REJECT")
        self.assertIn("Official MV", r["reason"])

    def test_cover_is_ignored_and_mv_suggested(self):
        self.fake([{"id": "mine0000001", "title": "가수 - 숨은곡 (Audio)", "views": 3000},
                   {"id": "cover000001", "title": "가수 - 숨은곡 cover by 누구", "views": 2_000_000},
                   {"id": "mv000000001", "title": "가수 - 숨은곡 MV", "views": 9000}])
        r = yt.song_views("가수", "숨은곡", "mine0000001")
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["mv"]["video_id"], "mv000000001")

    def test_not_found_goes_review(self):
        self.fake([{"id": "other000001", "title": "전혀 다른 곡", "views": 10},
                   {"id": "mine0000001", "title": "表記違い", "views": 10, "in_search": False}])
        self.assertEqual(yt.song_views("가수", "숨은곡", "mine0000001")["status"], "REVIEW")

    def test_quota_goes_review(self):
        def _get(path, **params):
            raise yt.YouTubeError("QUOTA")
        yt._get = _get
        r = yt.song_views("가수", "숨은곡", "mine0000001")
        self.assertEqual(r["status"], "REVIEW")
        self.assertIn("할당량", r["reason"])


if __name__ == "__main__":
    unittest.main()
