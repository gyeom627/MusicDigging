"""DB 연결·스키마 적용·태그 시드 (1·2회차와 같은 get_db_connection 패턴)"""
import os
import sqlite3

import config
import tag_rules

SCHEMA_PATH = os.path.join(config.BASE_DIR, "schema.sql")


def get_db_connection(path=None):
    conn = sqlite3.connect(path or config.DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def tag_seed_rows():
    """tag_rules의 고정 목록 → (axis, name, grp, sort) 100행"""
    rows = []
    for g, moods in tag_rules.MOOD_GROUPS.items():
        rows += [("MOOD", m, g) for m in moods]
    rows += [("ENERGY", v, None) for v in tag_rules.ENERGIES]
    rows += [("SITUATION", v, None) for v in tag_rules.SITUATIONS]
    rows += [("TIME", v, None) for v in tag_rules.TIMES]
    rows += [("INSTRUMENT", v, None) for v in tag_rules.INSTRUMENTS]
    rows += [("GENRE", v, None) for v in tag_rules.GENRES]
    for g, subs in tag_rules.GENRE_TREE.items():
        rows += [("SUBGENRE", s, g) for s in subs]
    return [(axis, name, grp, i) for i, (axis, name, grp) in enumerate(rows)]


def init_db(path=None):
    """테이블을 만들고 태그를 넣는다. 여러 번 실행해도 기존 데이터는 지우지 않는다."""
    conn = get_db_connection(path)
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.executemany("INSERT OR IGNORE INTO tags (axis, name, grp, sort) VALUES (?, ?, ?, ?)", tag_seed_rows())
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    finally:
        conn.close()
