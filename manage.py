"""
관리 명령 (로컬·PythonAnywhere 콘솔 공통)
  python manage.py init-db          테이블 생성 + 고정 태그 100개 (여러 번 실행해도 안전)
  python manage.py create-admin     관리자 계정 만들기 (비밀번호는 화면에 보이지 않게 입력)
  python manage.py reset-passes 아이디   그 계정이 넘긴 곡을 다시 나오게 (매수 기록은 그대로)
  python manage.py pool-status [아이디]  디깅 풀 상태 (곡 수·숨김 수, 아이디를 주면 넘긴·매수한 곡 수)
  python manage.py restore-errors      재생 오류로 자동 숨김된 곡을 다시 공개 (오류 보고 기록도 비움)
"""
import getpass
import sqlite3
import sys

from werkzeug.security import generate_password_hash

import config
from db import get_db_connection, init_db


def create_admin():
    username = input("관리자 아이디(영문 소문자·숫자·밑줄 4~20자): ").strip().lower()
    nickname = input("닉네임: ").strip()
    password = getpass.getpass("비밀번호(8자 이상): ")
    if len(password) < 8:
        sys.exit("비밀번호는 8자 이상이어야 합니다.")
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (username, password_hash, nickname, role) VALUES (?, ?, ?, 'ADMIN')",
                     (username, generate_password_hash(password), nickname))
        conn.commit()
        print(f"관리자 {username} 생성 완료")
    except sqlite3.IntegrityError:
        conn.execute("UPDATE users SET role = 'ADMIN' WHERE username = ?", (username,))
        conn.commit()
        print(f"이미 있는 아이디라 {username}의 권한만 ADMIN으로 바꿨습니다.")
    finally:
        conn.close()


def reset_passes(username):
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
        if row is None:
            sys.exit(f"{username} 계정을 찾을 수 없습니다.")
        n = conn.execute("DELETE FROM passes WHERE user_id = ?", (row[0],)).rowcount
        conn.commit()
        print(f"{username}: 넘긴 곡 {n}개를 다시 디깅 풀에 돌려놓았습니다.")
    finally:
        conn.close()


def pool_status(username=None):
    conn = get_db_connection()
    try:
        for status, n in conn.execute("SELECT status, COUNT(*) FROM music GROUP BY status"):
            print(f"곡 {status}: {n}")
        n = conn.execute("SELECT COUNT(*) FROM music WHERE status = 'HIDDEN' AND error_count >= ?",
                         (config.AUTO_HIDE_ERRORS,)).fetchone()[0]
        print(f"  그중 재생 오류로 자동 숨김: {n}")
        if username:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
            if row is None:
                sys.exit(f"{username} 계정을 찾을 수 없습니다.")
            for label, table in (("넘긴 곡", "passes"), ("매수한 곡", "purchases"), ("오류 보고", "error_reports")):
                c = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (row[0],)).fetchone()[0]
                print(f"{username} {label}: {c}")
    finally:
        conn.close()


def restore_errors():
    conn = get_db_connection()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM music WHERE status = 'HIDDEN' AND error_count >= ?",
                                          (config.AUTO_HIDE_ERRORS,))]
        conn.executemany("UPDATE music SET status = 'ACTIVE', error_count = 0 WHERE id = ?", [(i,) for i in ids])
        conn.executemany("DELETE FROM error_reports WHERE music_id = ?", [(i,) for i in ids])
        conn.commit()
        print(f"재생 오류로 숨겨졌던 곡 {len(ids)}개를 다시 공개했습니다.")
    finally:
        conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-db":
        print(f"DB: {config.DATABASE}\n태그 {init_db()}개 준비 완료")
    elif cmd == "create-admin":
        create_admin()
    elif cmd == "reset-passes" and len(sys.argv) > 2:
        reset_passes(sys.argv[2])
    elif cmd == "pool-status":
        pool_status(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "restore-errors":
        restore_errors()
    else:
        print(__doc__)
