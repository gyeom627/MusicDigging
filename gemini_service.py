"""
Gemini 호출부 (설계 보고서 3.3, 4.8, 6장)
- 무드 문장 해석: 사용자가 쓴 문장을 고정 태그 목록(tag_rules)의 값으로만 옮긴다. 곡을 고르는 일은 서버의 추첨이 한다.
- 응답은 JSON 스키마(enum)로 1차 제한하고, tag_rules.validate_mood_query가 목록 밖 값을 다시 버린다 (코드가 최종 판단)
- 실패(키 없음·시간 초과·쿼터 초과·형식 오류)는 예외로 올리지 않고 status = FAILED로 돌려준다 → 화면은 칩 고르기로 대체
- AI 테스트 키트(ai_test_v2/gemini_client.py)의 구조를 서비스용으로 옮긴 것. 웹 요청 안에서 부르므로 오래 기다리지 않는다
"""
import time

import config
import tag_rules

try:  # 학교망처럼 SSL 검사 인증서가 끼는 네트워크용 (서버에는 없어도 됨, verify=False는 쓰지 않음)
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

MOOD_TIMEOUT_MS = 20000  # 사용자가 기다리는 요청이라 20초를 넘기지 않는다
_client = None


def _get_client():
    """키가 없으면 None. 클라이언트는 한 번만 만든다"""
    global _client
    if _client is None:
        key = config.GEMINI_API_KEY
        if not key or not key.isascii():
            return None
        from google import genai
        from google.genai import types
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=MOOD_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
    return _client


def _usage(resp):
    u = getattr(resp, "usage_metadata", None)
    if not u:
        return None, None
    return (getattr(u, "prompt_token_count", None),
            getattr(u, "candidates_token_count", None) or getattr(u, "response_token_count", None))


def _call_mood_model(text):
    """Gemini에 무드 문장 해석을 요청. 반환: dict(raw, seconds, in_tok, out_tok, error). 테스트에서는 이 함수를 바꿔 끼운다"""
    t0 = time.time()
    client = _get_client()
    if client is None:
        return {"raw": "", "seconds": 0.0, "in_tok": None, "out_tok": None, "error": "GEMINI_API_KEY 없음"}
    from google.genai import errors, types
    cfg = types.GenerateContentConfig(
        temperature=0.0,  # 같은 문장이면 같은 해석 (11/3 검증에서 정한 값)
        response_mime_type="application/json",
        response_json_schema=tag_rules.mood_query_schema(),
    )
    for attempt in (1, 2):  # 503(서버 과부하)만 2초 쉬고 한 번 더. 429·키 오류·시간 초과는 바로 실패
        try:
            resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=tag_rules.mood_prompt(text),
                                                  config=cfg)
            i, o = _usage(resp)
            return {"raw": resp.text or "", "seconds": round(time.time() - t0, 1), "in_tok": i, "out_tok": o, "error": ""}
        except errors.ServerError as e:
            if attempt == 2:
                return _failed(t0, e)
            time.sleep(2)
        except Exception as e:  # noqa: BLE001 — 어떤 오류든 화면을 깨지 않고 FAILED로
            return _failed(t0, e)


def _failed(t0, err):
    return {"raw": "", "seconds": round(time.time() - t0, 1), "in_tok": None, "out_tok": None,
            "error": f"{type(err).__name__}: {str(err)[:200]}"}


def interpret_mood(text):
    """반환: (status, tags, call). status = DONE / EMPTY / FAILED
    tags = {moods, situations, times, instruments, subgenres, energy, genre} (목록 안의 값만)"""
    call = _call_mood_model(text)
    if call["error"]:
        return "FAILED", {}, call
    status, tags, _notes = tag_rules.validate_mood_query(call["raw"])
    return status, tags, call


# ---------------- 곡 등록: 하이라이트 오디오 분석 (설계 4.7, 8.2) ----------------
AUDIO_TIMEOUT_MS = config.GEMINI_TIMEOUT_MS  # 영상 구간을 듣는 호출이라 무드 해석보다 오래 걸린다
_audio_client = None


def _get_audio_client():
    global _audio_client
    if _audio_client is None:
        key = config.GEMINI_API_KEY
        if not key or not key.isascii():
            return None
        from google import genai
        from google.genai import types
        _audio_client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=AUDIO_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
    return _audio_client


def _call_audio_model(video_id, start_sec):
    """YouTube 영상의 하이라이트 60초만 잘라 듣게 한다 (fps 0.2·저해상도로 화면 정보는 최소). 반환 형식은 _call_mood_model과 같음"""
    t0 = time.time()
    client = _get_audio_client()
    if client is None:
        return {"raw": "", "seconds": 0.0, "in_tok": None, "out_tok": None, "error": "GEMINI_API_KEY 없음"}
    from google.genai import errors, types
    part = types.Part(
        file_data=types.FileData(file_uri=f"https://www.youtube.com/watch?v={video_id}"),
        video_metadata=types.VideoMetadata(start_offset=f"{start_sec}s",
                                           end_offset=f"{start_sec + config.HIGHLIGHT_SEC}s", fps=0.2),
    )
    cfg = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_json_schema=tag_rules.audio_schema(),
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
    )
    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=[part, tag_rules.AUDIO_PROMPT],
                                                  config=cfg)
            i, o = _usage(resp)
            return {"raw": resp.text or "", "seconds": round(time.time() - t0, 1), "in_tok": i, "out_tok": o, "error": ""}
        except errors.ServerError as e:
            if attempt == 2:
                return _failed(t0, e)
            time.sleep(3)
        except Exception as e:  # noqa: BLE001
            return _failed(t0, e)


def analyze_audio(video_id, start_sec):
    """반환: (status, tags, call). status = DONE / FAILED / NOT_MUSIC (tag_rules.validate_audio)"""
    call = _call_audio_model(video_id, start_sec)
    if call["error"]:
        return "FAILED", {}, call
    status, tags, _notes = tag_rules.validate_audio(call["raw"])
    return status, tags, call
