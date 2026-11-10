"""
관리 명령 (로컬·PythonAnywhere 콘솔 공통)
  python manage.py init-db          테이블 생성 + 고정 태그 100개 (여러 번 실행해도 안전)
  python manage.py create-admin     관리자 계정 만들기 (비밀번호는 화면에 보이지 않게 입력)
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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-db":
        print(f"DB: {config.DATABASE}\n태그 {init_db()}개 준비 완료")
    elif cmd == "create-admin":
        create_admin()
    else:
        print(__doc__)
