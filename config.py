"""설정값과 정책 상수 (요구사항 및 설계 보고서 4장). 비밀값은 .env에서만 읽는다."""
import os
from datetime import timedelta, timezone

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---- 비밀값 (.env / PythonAnywhere 환경변수) ----
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
GEMINI_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_MS", "60000"))
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
DISCOGS_TOKEN = os.environ.get("DISCOGS_TOKEN")
WEB_SEARCH_ENABLED = os.environ.get("WEB_SEARCH_ENABLED", "false").lower() == "true"

DATABASE = os.environ.get("DIGGER_DB", os.path.join(BASE_DIR, "digger.db"))

# ---- 정책 상수 ----
DAILY_BUY_LIMIT = 10          # 하루 매수권
EARLY_BUYER_LIMIT = 10        # count_at_buy가 이 값 미만이면 초기 발굴(포인트 2배)
EARLY_WEIGHT = 2
VIEW_LIMIT = 1_000_000        # 곡 단위 조회수 상한 (이 값 이상이면 거부)
DAILY_SUBMIT_LIMIT = 5        # 1인 하루 제보 곡 수
DAILY_SUGGEST_LIMIT = 10      # 1인 하루 AI 추천 요청 수
HIGHLIGHT_SEC = 60            # 미리듣기·오디오 분석 구간
MOOD_TEXT_MAX = 100           # 무드 문장 최대 글자 수
POST_MAX = 200
INTRO_MAX = 100
AUTO_HIDE_ERRORS = 3          # 재생 오류가 이만큼 쌓이면 자동 숨김

CATEGORIES = {"KR": "국내", "JP": "J-Pop", "POP": "Pop"}

# 한국은 서머타임이 없어 고정 오프셋으로 충분하다 (Windows에서 tzdata 없이도 동작)
KST = timezone(timedelta(hours=9), "KST")
