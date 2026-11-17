"""로그인·관리자 권한 데코레이터 (app.py와 블루프린트가 함께 씀)"""
from functools import wraps

from flask import abort, g, jsonify, redirect, request, url_for


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(ok=False, error="로그인이 필요합니다."), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "ADMIN":
            abort(403)
        return view(*args, **kwargs)
    return wrapped
