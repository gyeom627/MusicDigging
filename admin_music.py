"""
관리자 곡 관리 (설계 보고서 1.2 관리자 시나리오, 4.5 '게시 후 태그 수정은 관리자만', 7.2 #9 재생 불가 영상)
- 목록: 상태(공개·검토 대기·숨김)별, 제목·아티스트 검색
- 수정: 제목·아티스트·카테고리·하이라이트 시작·한줄 소개·태그, 영상 교체(저작권 차단 등으로 재생이 안 될 때)
- 숨김/복구: 디깅 풀에서 빼되 기록은 남긴다 → 이미 매수한 사람의 포트폴리오·포인트가 깨지지 않음
- 삭제: 매수·게시글 기록이 없는 곡(시험 곡 등)만 완전히 지운다. 기록이 있으면 숨김을 쓰게 한다
"""
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import config
import tag_rules
import tagging
import youtube_service as yt
from db import get_db
from guards import admin_required
from music import extract_video_id, normalize, replace_tags, song_tags

bp = Blueprint("admin_music", __name__)
STATUS_NAMES = {"ACTIVE": "공개", "REVIEW": "검토 대기", "HIDDEN": "숨김"}
PAGE = 50


def _song(music_id):
    m = get_db().execute("SELECT * FROM music WHERE id = ?", (music_id,)).fetchone()
    if m is None:
        abort(404)
    return m


@bp.get("/admin/music")
@admin_required
def music_list():
    status = request.args.get("status", "ACTIVE")
    if status not in STATUS_NAMES:
        status = "ACTIVE"
    q = (request.args.get("q") or "").strip()[:50]
    page = max(1, request.args.get("page", 1, type=int))
    sql, params = "FROM music WHERE status = ?", [status]
    if q:  # 검색어는 ? 바인딩 (SQL 인젝션 방지), LIKE 특수문자는 그대로 글자로 취급
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += " AND (title LIKE ? ESCAPE '\\' OR artist LIKE ? ESCAPE '\\')"
        params += [like, like]
    db = get_db()
    total = db.execute("SELECT COUNT(*) " + sql, params).fetchone()[0]
    rows = db.execute("SELECT id, video_id, title, artist, category, buy_count, error_count, review_reason, created_at "
                      + sql + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [PAGE, (page - 1) * PAGE]).fetchall()
    counts = dict(db.execute("SELECT status, COUNT(*) FROM music GROUP BY status").fetchall())
    return render_template("admin_music.html", rows=rows, status=status, q=q, page=page, total=total, page_size=PAGE,
                           counts=counts, status_names=STATUS_NAMES, categories=config.CATEGORIES)


@bp.get("/admin/music/<int:music_id>")
@admin_required
def music_edit(music_id):
    m = _song(music_id)
    db = get_db()
    refs = {
        "purchases": db.execute("SELECT COUNT(*) FROM purchases WHERE music_id = ?", (music_id,)).fetchone()[0],
        "posts": db.execute("SELECT COUNT(*) FROM posts WHERE music_id = ?", (music_id,)).fetchone()[0],
    }
    return render_template("admin_edit.html", m=m, tags=song_tags(db, music_id), refs=refs,
                           categories=config.CATEGORIES, status_names=STATUS_NAMES,
                           mood_groups=tag_rules.MOOD_GROUPS, genres=tag_rules.GENRES, genre_tree=tag_rules.GENRE_TREE,
                           energies=tag_rules.ENERGIES, situations=tag_rules.SITUATIONS, times=tag_rules.TIMES,
                           instruments=tag_rules.INSTRUMENTS, limits=tag_rules.AUDIO_LIMITS)


def _back(music_id):
    return redirect(url_for("admin_music.music_edit", music_id=music_id))


@bp.post("/admin/music/<int:music_id>")
@admin_required
def music_save(music_id):
    m = _song(music_id)
    f = request.form
    title, artist, intro = f.get("title", "").strip(), f.get("artist", "").strip(), f.get("intro", "").strip() or None
    category = f.get("category")
    try:
        mm, ss = f.get("start", "").strip().split(":")
        start = int(mm) * 60 + int(ss)
        assert 0 <= int(ss) < 60
    except (ValueError, AssertionError):
        start = -1
    errors = []
    if not (0 < len(title) <= 100 and 0 < len(artist) <= 100 and normalize(title) and normalize(artist)):
        errors.append("제목과 아티스트를 100자 이내로 입력해 주세요.")
    if category not in config.CATEGORIES:
        errors.append("카테고리가 올바르지 않습니다.")
    if start < 0:
        errors.append("하이라이트 시작은 분:초(예: 1:05)로 입력해 주세요.")
    if intro and len(intro) > config.INTRO_MAX:
        errors.append(f"한줄 소개는 {config.INTRO_MAX}자 이내입니다.")
    moods = [x for x in (f.get("mood1"), f.get("mood2"), f.get("mood3")) if x]
    tags, tag_errors = tagging.clean_final({
        "moods": moods, "genre": f.get("genre") or None, "energy": f.get("energy") or None,
        "subgenres": f.getlist("subgenres"), "situations": f.getlist("situations"),
        "times": f.getlist("times"), "instruments": f.getlist("instruments")})
    errors += tag_errors
    if len(set(moods)) != len(moods):
        errors.append("분위기 순위에 같은 값이 두 번 들어갔습니다.")

    # 영상 교체: 저작권 차단 등으로 재생이 안 될 때 같은 곡의 다른 영상으로 (등록 때와 같은 조건 확인)
    video_id = m["video_id"]
    new_url = f.get("video_url", "").strip()
    if new_url:
        vid = extract_video_id(new_url)
        if not vid:
            errors.append("새 영상 주소를 확인해 주세요.")
        elif vid != video_id:
            try:
                info = yt.fetch_video(vid)
                errors += yt.problems(info, start if start >= 0 else None)
                video_id = vid
            except yt.YouTubeError:
                errors.append("YouTube 정보를 확인하지 못해 영상을 바꾸지 않았습니다. 잠시 후 다시 시도해 주세요.")
    if errors:
        for e in errors:
            flash(e, "error")
        return _back(music_id)

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE music SET title = ?, artist = ?, title_norm = ?, artist_norm = ?, category = ?, start_sec = ?, "
                   "intro = ?, video_id = ?, error_count = CASE WHEN video_id = ? THEN error_count ELSE 0 END "
                   "WHERE id = ?", (title, artist, normalize(title), normalize(artist), category, start, intro,
                                    video_id, video_id, music_id))
        if video_id != m["video_id"]:  # 새 영상이면 이전 영상의 재생 오류 보고는 의미가 없다
            db.execute("DELETE FROM error_reports WHERE music_id = ?", (music_id,))
        replace_tags(db, music_id, tags)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("같은 영상이나 같은 제목·아티스트의 곡이 이미 있습니다.", "error")
        return _back(music_id)
    flash("저장했습니다." + (" 영상을 바꾸고 재생 오류 기록을 비웠습니다." if video_id != m["video_id"] else ""))
    return _back(music_id)


@bp.post("/admin/music/<int:music_id>/status")
@admin_required
def music_status(music_id):
    """공개 / 숨김 / 검토 대기로 상태 변경. 공개로 돌릴 때는 쌓인 재생 오류 수를 비운다"""
    _song(music_id)
    to = request.form.get("to")
    if to not in STATUS_NAMES:
        abort(400)
    db = get_db()
    if to == "ACTIVE":
        db.execute("UPDATE music SET status = 'ACTIVE', error_count = 0 WHERE id = ?", (music_id,))
        db.execute("DELETE FROM error_reports WHERE music_id = ?", (music_id,))
    else:
        db.execute("UPDATE music SET status = ? WHERE id = ?", (to, music_id))
    db.commit()
    flash(f"상태를 '{STATUS_NAMES[to]}'(으)로 바꿨습니다.")
    return _back(music_id)


@bp.post("/admin/music/<int:music_id>/delete")
@admin_required
def music_delete(music_id):
    """완전 삭제는 매수·게시글 기록이 없는 곡만. 있으면 숨김으로 (포트폴리오·포인트·게시판이 깨지지 않게)"""
    m = _song(music_id)
    if request.form.get("confirm", "").strip() != m["title"]:
        flash("삭제하려면 확인 칸에 곡 제목을 그대로 입력해 주세요.", "error")
        return _back(music_id)
    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        used = db.execute("SELECT (SELECT COUNT(*) FROM purchases WHERE music_id = ?) + "
                          "(SELECT COUNT(*) FROM posts WHERE music_id = ?)", (music_id, music_id)).fetchone()[0]
        if used:
            db.rollback()
            flash("매수하거나 게시글에 쓴 기록이 있어 지울 수 없습니다. 숨김을 사용하세요.", "error")
            return _back(music_id)
        for table in ("passes", "error_reports"):
            db.execute(f"DELETE FROM {table} WHERE music_id = ?", (music_id,))
        db.execute("DELETE FROM music WHERE id = ?", (music_id,))  # music_tags는 ON DELETE CASCADE
        db.commit()
    except sqlite3.Error:
        db.rollback()
        flash("삭제하지 못했습니다.", "error")
        return _back(music_id)
    flash(f"'{m['title']}'을(를) 삭제했습니다.")
    return redirect(url_for("admin_music.music_list", status=m["status"]))
