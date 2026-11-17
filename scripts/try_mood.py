"""
무드 문장 해석을 실제 Gemini로 한 번 시험 (DB·캐시는 건드리지 않음)
  python scripts/try_mood.py "비 오는 밤에 혼자 걷는 느낌"
배포 서버에서 키·모델·네트워크가 맞는지, 해석이 목록 안의 값으로 나오는지 확인하는 용도
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import gemini_service  # noqa: E402
from mood import labels  # noqa: E402

if __name__ == "__main__":
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        sys.exit('사용법: python scripts/try_mood.py "듣고 싶은 느낌 문장"')
    status, tags, call = gemini_service.interpret_mood(text)
    print(f"모델: {config.GEMINI_MODEL}  상태: {status}  {call['seconds']}초  토큰 입력 {call['in_tok']} / 출력 {call['out_tok']}")
    if call["error"]:
        print("오류:", call["error"])
    else:
        print("해석:", " · ".join(x["text"] for x in labels(tags)) or "(없음)")
        print("원본 응답:", call["raw"])
