-- Digger 스키마 (요구사항 및 설계 보고서 5장, 테이블 11개)
-- 시각(created_at)은 모두 UTC 'YYYY-MM-DD HH:MM:SS'로 저장하고 화면에서만 한국 시간으로 바꾼다.
-- 디깅 포인트·랭킹·뱃지·남은 매수권은 저장하지 않고 purchases·post_likes에서 계산한다.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nickname      TEXT NOT NULL UNIQUE,
    role          TEXT NOT NULL DEFAULT 'USER' CHECK (role IN ('USER', 'ADMIN')),
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS music (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL UNIQUE CHECK (length(video_id) = 11),
    title          TEXT NOT NULL,
    artist         TEXT NOT NULL,
    title_norm     TEXT NOT NULL,
    artist_norm    TEXT NOT NULL,
    category       TEXT NOT NULL CHECK (category IN ('KR', 'JP', 'POP')),
    start_sec      INTEGER NOT NULL CHECK (start_sec >= 0),
    intro          TEXT CHECK (intro IS NULL OR length(intro) <= 100),
    discogs_url    TEXT,
    discogs_styles TEXT,                       -- Discogs 스타일 원문(쉼표 구분, 참고용)
    buy_count      INTEGER NOT NULL DEFAULT 0 CHECK (buy_count >= 0),
    submitted_by   INTEGER REFERENCES users (id),
    status         TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'REVIEW', 'HIDDEN')),
    review_reason  TEXT,
    audio_status   TEXT NOT NULL DEFAULT 'SKIPPED' CHECK (audio_status IN ('DONE', 'FAILED', 'NOT_MUSIC', 'SKIPPED')),
    web_status     TEXT CHECK (web_status IS NULL OR web_status IN ('FOUND', 'NOT_FOUND', 'MISMATCH', 'FAILED')),
    error_count    INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (artist_norm, title_norm)
);
CREATE INDEX IF NOT EXISTS idx_music_status_category ON music (status, category);
CREATE INDEX IF NOT EXISTS idx_music_submitted ON music (submitted_by, created_at);

-- 고정 태그 100개 (tag_rules.py에서 시드)
CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    axis TEXT NOT NULL CHECK (axis IN ('MOOD', 'ENERGY', 'SITUATION', 'TIME', 'INSTRUMENT', 'GENRE', 'SUBGENRE')),
    name TEXT NOT NULL,
    grp  TEXT,                                 -- 분위기 그룹 또는 세부 장르의 대분류
    sort INTEGER NOT NULL DEFAULT 0,
    UNIQUE (axis, name)
);

-- 태그마다 출처와 채택 여부를 남긴다 (AI 채택률 = AI·BOTH 중 accepted=1 비율)
CREATE TABLE IF NOT EXISTS music_tags (
    music_id   INTEGER NOT NULL REFERENCES music (id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags (id),
    origin     TEXT NOT NULL CHECK (origin IN ('AI', 'DISCOGS', 'BOTH', 'USER', 'ADMIN')),
    accepted   INTEGER NOT NULL DEFAULT 1 CHECK (accepted IN (0, 1)),
    rank       INTEGER CHECK (rank IS NULL OR rank BETWEEN 1 AND 3),      -- 분위기 최종 순위
    ai_rank    INTEGER CHECK (ai_rank IS NULL OR ai_rank BETWEEN 1 AND 3), -- AI가 매긴 분위기 순위
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (music_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_music_tags_tag ON music_tags (tag_id, accepted);

CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users (id),
    music_id     INTEGER NOT NULL REFERENCES music (id),
    count_at_buy INTEGER NOT NULL CHECK (count_at_buy >= 0),  -- 내 매수 직전 누적 매수 수
    is_early     INTEGER NOT NULL CHECK (is_early IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, music_id)
);
CREATE INDEX IF NOT EXISTS idx_purchases_user_time ON purchases (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_purchases_music ON purchases (music_id);

CREATE TABLE IF NOT EXISTS passes (
    user_id    INTEGER NOT NULL REFERENCES users (id),
    music_id   INTEGER NOT NULL REFERENCES music (id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, music_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users (id),
    music_id   INTEGER NOT NULL REFERENCES music (id),     -- BGM 곡
    content    TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 200),
    status     TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'HIDDEN', 'DELETED')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_posts_time ON posts (status, created_at);

CREATE TABLE IF NOT EXISTS post_likes (
    post_id    INTEGER NOT NULL REFERENCES posts (id),
    user_id    INTEGER NOT NULL REFERENCES users (id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL REFERENCES posts (id),
    user_id    INTEGER NOT NULL REFERENCES users (id),
    content    TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 200),
    status     TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'HIDDEN', 'DELETED')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments (post_id, created_at);

CREATE TABLE IF NOT EXISTS mood_cache (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query_norm  TEXT NOT NULL UNIQUE,
    parsed_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- AI 호출 기록: 1인 하루 AI 추천 10회 제한 계산 + 응답 시간·실패율
CREATE TABLE IF NOT EXISTS ai_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users (id),
    kind       TEXT NOT NULL CHECK (kind IN ('AUDIO', 'MOOD')),
    video_id   TEXT,
    status     TEXT NOT NULL,
    seconds    REAL,
    in_tokens  INTEGER,
    out_tokens INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_logs_user ON ai_logs (user_id, kind, created_at);
