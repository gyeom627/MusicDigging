"""
Digger — 음악 디깅·저점매수 서비스 (3회차)
11주차: 가입·로그인·권한, 화면 골격, 배포 확인용 /health
12주차: 곡 시드, 디깅 플레이어(dig.py), 매수·매수권
다음 단계: 포트폴리오 → 곡 등록(AI 추천 이식) → 게시판
"""
import os
import re
import secrets
import sqlite3

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config
import scoring
import tag_rules
from db import close_db, get_db
from guards import admin_required, login_required
from music import normalize, song_tags
from tag_rules import MOOD_GROUP_OF


app = Flask(__name__)


@app.url_defaults
def static_version(endpoint, values):
    """url_for('static', ...)에 ?v=파일 수정 시각을 붙인다. git pull로 파일이 바뀌면 브라우저가 새로 받는다"""
    if endpoint == "static" and "filename" in values:
        try:
            values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
        except OSError:
            pass
if config.SECRET_KEY:
    app.secret_key = config.SECRET_KEY
else:  # 로컬 개발용. 배포에서는 반드시 FLASK_SECRET_KEY를 설정한다(재시작마다 로그인이 풀림)
    app.secret_key = secrets.token_hex(32)
    print("⚠ FLASK_SECRET_KEY가 없어 임시 키를 씁니다. .env에 설정하세요.")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  MAX_CONTENT_LENGTH=64 * 1024)

USERNAME_RE = re.compile(r"^[a-z0-9_]{4,20}$")
app.teardown_appcontext(close_db)

from dig import bp as dig_bp  # noqa: E402  (app 설정 뒤에 등록)
app.register_blueprint(dig_bp)
from mood import bp as mood_bp  # noqa: E402
app.register_blueprint(mood_bp)
from submit import bp as submit_bp  # noqa: E402
app.register_blueprint(submit_bp)
from admin_music import bp as admin_music_bp  # noqa: E402
app.register_blueprint(admin_music_bp)


# ---------------- 로그인 사용자 / 권한 ----------------
@app.before_request
def load_user():
    g.user = None
    uid = session.get("user_id")
    if uid:
        g.user = get_db().execute("SELECT id, username, nickname, role FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:  # 삭제된 계정의 세션
            session.clear()


# ---------------- CSRF: 폼은 hidden 필드, fetch는 X-CSRFToken 헤더 ----------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="요청이 만료되었습니다. 새로고침 후 다시 시도하세요."), 400
            abort(400)


@app.context_processor
def inject_common():
    tickets = None
    if g.get("user"):
        tickets = scoring.tickets_left(get_db(), g.user["id"])
    return {"tickets_left": tickets, "daily_limit": config.DAILY_BUY_LIMIT}


def safe_next(target):
    """오픈 리다이렉트 방지: 우리 사이트 안의 경로만 허용"""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("index")


# ---------------- 가입·로그인 ----------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if g.user:
        return redirect(url_for("index"))
    form = {"username": "", "nickname": ""}
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        nickname = request.form.get("nickname", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        form = {"username": username, "nickname": nickname}
        error = None
        if not USERNAME_RE.match(username):
            error = "아이디는 영문 소문자·숫자·밑줄로 4~20자입니다."
        elif not 2 <= len(nickname) <= 12:
            error = "닉네임은 2~12자입니다."
        elif len(password) < 8:
            error = "비밀번호는 8자 이상입니다."
        elif password != password2:
            error = "비밀번호 확인이 일치하지 않습니다."
        if error is None:
            try:
                db = get_db()
                cur = db.execute("INSERT INTO users (username, password_hash, nickname) VALUES (?, ?, ?)",
                                 (username, generate_password_hash(password), nickname))
                db.commit()
            except sqlite3.IntegrityError:
                error = "이미 사용 중인 아이디 또는 닉네임입니다."
            else:
                session.clear()
                session["user_id"] = cur.lastrowid
                flash(f"{nickname}님, 가입을 환영합니다. 오늘의 매수권 {config.DAILY_BUY_LIMIT}장이 지급되었습니다.")
                return redirect(url_for("index"))
        flash(error, "error")
    return render_template("signup.html", form=form)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("index"))
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()  # 세션 고정 공격 방지
            session["user_id"] = user["id"]
            return redirect(safe_next(request.args.get("next")))
        flash("아이디 또는 비밀번호가 올바르지 않습니다.", "error")  # 어느 쪽이 틀렸는지 알려주지 않음
    return render_template("login.html", username=username)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- 화면 (골격) ----------------
@app.route("/")
@login_required
def index():
    return render_template("index.html", categories=config.CATEGORIES, mood_max=config.MOOD_TEXT_MAX)


def placeholder(title, desc):
    return render_template("placeholder.html", title=title, desc=desc)


@app.route("/portfolio")
@login_required
def portfolio():
    """내 포트폴리오: 디깅 포인트·뱃지·매수한 곡 표 (설계 4.1 매수 후 공개 정보, 4.3 포인트, 4.4 뱃지)
    포인트는 저장하지 않고 지금 기록으로 계산한다. 매수한 곡만 제목·아티스트를 보여 준다"""
    db, uid = get_db(), g.user["id"]
    sort = request.args.get("sort", "recent")
    rows = db.execute(
        "SELECT p.count_at_buy, p.is_early, p.created_at AS bought_at, m.id, m.video_id, m.title, m.artist, m.category, "
        "m.start_sec, m.intro, m.buy_count, m.status, m.discogs_url "
        "FROM purchases p JOIN music m ON m.id = p.music_id WHERE p.user_id = ? ORDER BY p.id DESC", (uid,)).fetchall()
    songs = []
    for r in rows:
        tags = song_tags(db, r["id"])
        songs.append({**dict(r), "genre": tags["genre"], "subgenres": tags["subgenres"], "moods": tags["moods"],
                      "rank": r["count_at_buy"] + 1, "grown": max(0, r["buy_count"] - r["count_at_buy"] - 1),
                      "points": scoring.purchase_points(r["buy_count"], r["count_at_buy"], r["is_early"]),
                      "bought_kst": scoring.to_kst(r["bought_at"])})
    if sort == "points":
        songs.sort(key=lambda x: (-x["points"], -x["buy_count"]))
    points = sum(x["points"] for x in songs)
    likes = scoring.likes_received(db, uid)
    stats = {"points": points, "likes": likes, "score": points + likes, "bought": len(songs),
             "early": sum(1 for x in songs if x["is_early"])}
    return render_template("portfolio.html", songs=songs, stats=stats, sort=sort,
                           badges=scoring.badges(db, uid, points), categories=config.CATEGORIES,
                           early_limit=config.EARLY_BUYER_LIMIT, weight=config.EARLY_WEIGHT)


@app.route("/playlist")
@login_required
def playlist():
    """매수한 곡 전체 듣기: 매수 순서대로 또는 셔플. 디깅 카드와 같은 플레이어 (매수한 곡이라 정보는 공개)
    디깅 풀에서 빠진(숨김) 곡은 재생하지 않는다"""
    db, uid = get_db(), g.user["id"]
    rows = db.execute(
        "SELECT p.count_at_buy, p.is_early, m.id, m.video_id, m.title, m.artist, m.category, m.start_sec, m.intro "
        "FROM purchases p JOIN music m ON m.id = p.music_id WHERE p.user_id = ? AND m.status = 'ACTIVE' "
        "ORDER BY p.id", (uid,)).fetchall()
    songs = []
    for r in rows:
        tags = song_tags(db, r["id"])
        songs.append({"video_id": r["video_id"], "title": r["title"], "artist": r["artist"],
                      "category": config.CATEGORIES.get(r["category"], r["category"]), "start_sec": r["start_sec"],
                      "intro": r["intro"], "rank": r["count_at_buy"] + 1, "early": bool(r["is_early"]),
                      "genre": tags["genre"], "moods": tags["moods"],
                      "groups": [MOOD_GROUP_OF[m] for m in tags["moods"]]})
    order = "shuffle" if request.args.get("order") == "shuffle" else "bought"
    return render_template("playlist.html", songs=songs, order=order, length=config.HIGHLIGHT_SEC)


@app.route("/board")
def board():
    return placeholder("게시판", "탑 디거 매거진과 BGM 한줄평이 여기에 나옵니다.")


@app.route("/ranking")
def ranking():
    return placeholder("랭킹", "디깅 포인트 + 받은 공감 순위가 여기에 나옵니다.")


@app.route("/submit")
@login_required
def submit():
    """곡 제보: 링크 확인 → 정보 입력 → AI 추천 칩 확인·수정 → 게시 (submit.py API + static/js/submit.js)"""
    options = {"moods": tag_rules.MOOD_GROUPS, "energy": tag_rules.ENERGIES, "situations": tag_rules.SITUATIONS,
               "times": tag_rules.TIMES, "instruments": tag_rules.INSTRUMENTS, "genre": tag_rules.GENRES,
               "subgenres": tag_rules.GENRE_TREE}
    limits = {axis: [mn, mx] for axis, (_, mn, mx) in tag_rules.AUDIO_LIMITS.items()}
    return render_template("submit.html", categories=config.CATEGORIES, options=options, limits=limits,
                           highlight=config.HIGHLIGHT_SEC, intro_max=config.INTRO_MAX)


@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    stats = {
        "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        "music": db.execute("SELECT COUNT(*) FROM music WHERE status = 'ACTIVE'").fetchone()[0],
        "review": db.execute("SELECT COUNT(*) FROM music WHERE status = 'REVIEW'").fetchone()[0],
        "tags": db.execute("SELECT COUNT(*) FROM tags").fetchone()[0],
    }
    rows = db.execute(
        "SELECT m.id, m.video_id, m.title, m.artist, m.category, m.start_sec, m.intro, m.review_reason, "
        "m.audio_status, m.web_status, m.discogs_url, m.created_at, u.nickname "
        "FROM music m LEFT JOIN users u ON u.id = m.submitted_by WHERE m.status = 'REVIEW' "
        "ORDER BY m.review_reason IS NULL, m.id").fetchall()  # 자동 확인에서 걸린 곡을 먼저
    review = []
    for m in rows:
        tags = db.execute(
            "SELECT t.axis, t.name, mt.origin, mt.accepted, mt.rank FROM music_tags mt JOIN tags t ON t.id = mt.tag_id "
            "WHERE mt.music_id = ? ORDER BY t.axis, COALESCE(mt.rank, 9), t.sort", (m["id"],)).fetchall()
        tags = sorted(tags, key=lambda t: list(ADMIN_AXIS_NAMES).index(t["axis"]))  # 분위기 → 장르 → … 순서로
        review.append({"m": m, "tags": [t for t in tags if t["accepted"]], "off": [t for t in tags if not t["accepted"]]})
    return render_template("admin.html", stats=stats, review=review, categories=config.CATEGORIES,
                           axis_names=ADMIN_AXIS_NAMES)


ADMIN_AXIS_NAMES = {"MOOD": "분위기", "GENRE": "장르", "SUBGENRE": "세부 장르", "ENERGY": "에너지",
                    "SITUATION": "상황", "TIME": "시간·날씨", "INSTRUMENT": "악기"}


@app.post("/admin/music/<int:music_id>/review")
@admin_required
def admin_review(music_id):
    """검토 대기 곡 승인(디깅 풀에 공개) 또는 숨김. CSRF는 before_request에서 검사"""
    """승인할 때 제목·아티스트 오기를 고칠 수 있다 (정규화 값도 함께 갱신해 중복 판정이 맞게)"""
    action = request.form.get("action")
    if action not in ("approve", "hide"):
        abort(400)
    db = get_db()
    if action == "hide":
        cur = db.execute("UPDATE music SET status = 'HIDDEN' WHERE id = ? AND status = 'REVIEW'", (music_id,))
    else:
        title = (request.form.get("title") or "").strip()
        artist = (request.form.get("artist") or "").strip()
        if not (0 < len(title) <= 100 and 0 < len(artist) <= 100 and normalize(title) and normalize(artist)):
            flash("제목과 아티스트를 100자 이내로 입력해 주세요.", "error")
            return redirect(url_for("admin"))
        try:
            cur = db.execute("UPDATE music SET status = 'ACTIVE', title = ?, artist = ?, title_norm = ?, artist_norm = ? "
                             "WHERE id = ? AND status = 'REVIEW'",
                             (title, artist, normalize(title), normalize(artist), music_id))
        except sqlite3.IntegrityError:
            db.rollback()
            flash("고친 제목·아티스트와 같은 곡이 이미 있습니다. 중복이면 숨김을 눌러 주세요.", "error")
            return redirect(url_for("admin"))
    db.commit()
    if cur.rowcount:
        flash("디깅 풀에 공개했습니다." if action == "approve" else "숨겼습니다.")
    else:
        flash("이미 처리된 곡입니다.", "error")
    return redirect(url_for("admin"))


# ---------------- 배포 확인용 (비밀값은 있는지 여부만) ----------------
@app.route("/health")
def health():
    try:
        tags = get_db().execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        db_ok = True
    except sqlite3.Error:
        tags, db_ok = 0, False
    env = {k: bool(getattr(config, k)) for k in ["SECRET_KEY", "GEMINI_API_KEY", "YOUTUBE_API_KEY", "DISCOGS_TOKEN"]}
    return jsonify(ok=db_ok and tags == 100, db=db_ok, tags=tags, env=env)


@app.errorhandler(403)
def forbidden(_e):
    return render_template("placeholder.html", title="권한이 없습니다", desc="관리자만 볼 수 있는 페이지입니다."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("placeholder.html", title="페이지를 찾을 수 없습니다", desc="주소를 다시 확인해 주세요."), 404


if __name__ == "__main__":
    app.run(debug=True)
