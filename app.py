"""
Digger — 음악 디깅·저점매수 서비스 (3회차)
11주차: 가입·로그인·권한, 화면 골격, 배포 확인용 /health
12주차: 곡 시드, 디깅 플레이어(dig.py), 매수·매수권
다음 단계: 포트폴리오 → 곡 등록(AI 추천 이식) → 게시판
"""
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


app = Flask(__name__)
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
    return placeholder("포트폴리오", "매수한 곡, 매수 당시 → 현재 누적 매수 수, 디깅 포인트, 뱃지가 여기에 나옵니다.")


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
    review = db.execute(
        "SELECT m.id, m.video_id, m.title, m.artist, m.category, m.review_reason, m.created_at, u.nickname "
        "FROM music m LEFT JOIN users u ON u.id = m.submitted_by WHERE m.status = 'REVIEW' ORDER BY m.id").fetchall()
    return render_template("admin.html", stats=stats, review=review, categories=config.CATEGORIES)


@app.post("/admin/music/<int:music_id>/review")
@admin_required
def admin_review(music_id):
    """검토 대기 곡 승인(디깅 풀에 공개) 또는 숨김. CSRF는 before_request에서 검사"""
    action = request.form.get("action")
    if action not in ("approve", "hide"):
        abort(400)
    db = get_db()
    cur = db.execute("UPDATE music SET status = ? WHERE id = ? AND status = 'REVIEW'",
                     ("ACTIVE" if action == "approve" else "HIDDEN", music_id))
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
