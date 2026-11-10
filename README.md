# Digger — 음악 디깅·저점매수 서비스 (소프트웨어개발 3회차)

숨은 곡을 블라인드로 1분씩 듣고 하루 10장의 매수권으로 '저점매수'해 먼저 알아본 안목을 포인트로 증명하는 서비스입니다.
Gemini가 곡의 하이라이트를 직접 듣고 무드 태그를 추천하고, 사용자의 무드 문장을 같은 태그로 해석합니다.

- 스택: Python Flask · SQLite · Jinja2 · Google Gemini API (google-genai)
- 배포: PythonAnywhere (무료)
- 현재 단계: 가입·로그인·권한, 화면 골격, DB 스키마(11개 테이블)·고정 태그 100개, 배포 확인용 `/health`

## 1. 로컬 실행 (Windows)
```
cd digger
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            :: 메모장으로 열어 값 채우기 (FLASK_SECRET_KEY, GEMINI_API_KEY 등)
python manage.py init-db          :: 테이블 + 태그 100개
python manage.py create-admin     :: 관리자 계정
python -m unittest discover tests -v
python app.py                     :: http://127.0.0.1:5000
```
`FLASK_SECRET_KEY` 만들기: `python -c "import secrets; print(secrets.token_hex(32))"`

## 2. 서버 배포 (PythonAnywhere)
1. Bash 콘솔: `git clone https://github.com/<아이디>/digger.git` → `cd digger`
2. `python3.12 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
3. `nano .env` 로 키 입력(로컬과 같은 항목, SECRET_KEY는 새로 생성) → `python manage.py init-db` → `python manage.py create-admin`
4. Web 탭 → Add a new web app → Manual configuration → Python 3.12
   - Source code: `/home/<사용자명>/digger`, Virtualenv: `/home/<사용자명>/digger/venv`
   - WSGI configuration file을 `pythonanywhere_wsgi.py` 내용으로 바꾸고 사용자명 수정
   - Static files: URL `/static/` → `/home/<사용자명>/digger/static`
5. Reload → `https://<사용자명>.pythonanywhere.com/health` 에서 `"ok": true` 확인
6. 외부 API 점검: `python scripts/check_apis.py`

업데이트: 로컬에서 push → 서버 콘솔에서 `cd ~/digger && git pull` → Web 탭 Reload
무료 웹앱은 1개월마다 Web 탭의 "Run until 1 month from today"로 연장해야 합니다.

## 3. 폴더 구조
```
app.py          라우트, 로그인·권한·CSRF
db.py           get_db_connection(), init_db()
schema.sql      테이블 11개
config.py       정책 상수(매수권 10, 조회수 100만 등), .env 로드
tag_rules.py    고정 태그 100개, Gemini JSON 스키마·프롬프트, 응답 검증, 무드 매칭 점수
scoring.py      매수권(한국 시간 자정 초기화) — 포인트·랭킹·뱃지 추가 예정
manage.py       init-db, create-admin
scripts/        check_apis.py (서버에서 외부 API 연결 점검)
tests/          test_smoke.py
```

## 4. 보안 규칙
- API 키·SECRET_KEY는 `.env`에만 둡니다. `.env`와 `*.db`는 `.gitignore`로 커밋하지 않습니다.
- SQL은 모두 `?` 바인딩, 화면은 Jinja2 자동 이스케이프, POST는 CSRF 토큰 검사.
