# Digger — 음악 디깅·저점매수 서비스 (소프트웨어개발 3회차)

숨은 곡을 블라인드로 듣고 하루 10장의 매수권으로 '저점매수'해 먼저 알아본 안목을 포인트로 증명하는 서비스입니다.
Gemini가 곡의 하이라이트를 직접 듣고 무드 태그를 추천하고, 사용자의 무드 문장을 같은 태그로 해석합니다.

- 스택: Python Flask · SQLite · Jinja2 · Google Gemini API (google-genai)
- 배포: PythonAnywhere (무료)
- 11주차: 가입·로그인·권한, 화면 골격, DB 스키마(11개 테이블)·고정 태그 100개, 배포 확인용 `/health`
- 12주차: 시드 곡 12곡, 디깅 플레이어(블라인드 전체 재생·하이라이트부터/처음부터·이전 곡·무드 서치·매수·패스·재생 오류 보고), 매수 트랜잭션·하루 매수권 10장
- 12주차 Gemini 연동: 무드 문장 디깅 — 문장을 고정 태그로 해석(캐시·하루 20회 제한·실패 시 칩으로 대체), 최고점 절반 이상 묶음에서 추첨
- 12주차 곡 제보: 링크 확인 → 곡 단위 조회수 판정 → Gemini 오디오 분석 + Discogs 추천 칩 → 등록자 수정 → 게시 → 관리자 승인(제목·아티스트 수정 가능) 후 디깅 풀 공개
- 13주차: 포트폴리오 — 디깅 포인트(매수 후 늘어난 매수 수 × 초기 발굴 2배), 뱃지 4종, 매수한 곡 표·듣기, 관리자 곡 관리
- 13주차: 매수 플레이리스트(/playlist) — 매수 순서·셔플 이어 듣기, 곡이 끝나면 자동 다음 곡, 재생 목록에서 바로 이동, 메뉴에 플레이리스트 탭
- 13주차: 디깅·플레이리스트 '곡이 끝나면 자동으로 다음 곡' 스위치 (디깅에서 자동으로 넘긴 곡은 패스로 기록하지 않음)

## 1. 로컬 실행 (Windows)
```
cd digger
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env            :: 메모장으로 열어 값 채우기 (FLASK_SECRET_KEY, GEMINI_API_KEY 등)
python manage.py init-db          :: 테이블 + 태그 100개
python manage.py create-admin     :: 관리자 계정
python manage.py reset-passes 아이디   :: 테스트로 넘긴 곡을 다시 나오게
python manage.py pool-status [아이디]  :: 디깅 풀 상태 확인
python manage.py restore-errors      :: 재생 오류로 숨겨진 곡 다시 공개
python manage.py list-users          :: 계정 목록
python manage.py set-password 아이디   :: 비밀번호 다시 정하기
python scripts/try_mood.py "문장"   :: 무드 문장 해석을 실제 Gemini로 시험
python scripts\seed_songs.py      :: 시드 곡 넣기 (data/seed_songs.csv, 여러 번 실행해도 안전)
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
schema.sql      테이블 12개 (12주차에 error_reports 추가)
config.py       정책 상수(매수권 10, 조회수 100만 등), .env 로드
tag_rules.py    고정 태그 100개, Gemini JSON 스키마·프롬프트, 응답 검증, 무드 매칭 점수
scoring.py      매수권(한국 시간 자정 초기화)·디깅 포인트·받은 공감·뱃지 계산 (저장하지 않음)
dig.py          디깅 API: /api/dig/next·prev·buy·pass·error·reset·facets
mood.py         무드 문장 디깅: /api/mood/interpret (캐시 → Gemini → 검증), 점수 추첨
gemini_service.py  Gemini 호출: 무드 문장 해석(20초)·하이라이트 오디오 분석(60초), 503만 한 번 재시도, 실패는 FAILED로
submit.py       곡 제보 API: /api/submit/check·suggest·publish (영상 조건·곡 단위 조회수·재검사·출처 기록)
youtube_service.py  videos.list·search.list, 같은 곡 판정(커버 제외), MV 제안 — 결과는 저장하지 않음
discogs_service.py  릴리스 검색·아티스트/트랙 일치 확인·장르 변환 (토큰 없으면 건너뜀)
admin_music.py  관리자 곡 관리: 목록·검색, 정보·태그 수정, 영상 교체, 숨김/복구, 삭제(기록 없는 곡만)
tagging.py      추천 칩 병합(Discogs 장르 우선), 태그 출처 판정(AI/DISCOGS/BOTH/USER/ADMIN), 개수 규칙 검사
music.py        곡 저장·태그 조회·정규화(중복 판정)
guards.py       로그인·관리자 데코레이터
data/           seed_songs.csv (검증 때 확정한 태그)
manage.py       init-db, create-admin, reset-passes, pool-status, restore-errors, list-users, set-password
scripts/        check_apis.py (외부 API 연결 점검), seed_songs.py (시드 곡), try_mood.py (무드 해석 시험)
tests/          test_smoke.py, test_dig.py, test_submit.py, test_admin_music.py, test_portfolio.py (외부 API는 가짜로 바꿔 판정·재검사·출처·제한 확인)
```

## 4. 보안 규칙
- API 키·SECRET_KEY는 `.env`에만 둡니다. `.env`와 `*.db`는 `.gitignore`로 커밋하지 않습니다.
- SQL은 모두 `?` 바인딩, 화면은 Jinja2 자동 이스케이프, POST는 CSRF 토큰 검사.
