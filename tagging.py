"""
추천 칩 병합과 출처 판정 (설계 보고서 4.7)
- 추천: 장르 대분류는 Discogs가 일치하면 Discogs 값, 아니면 AI 값을 켠다. 둘이 다르면 다른 쪽은 꺼진 칩으로.
        세부 장르는 Discogs 변환 값 + AI 값을 합쳐 앞의 2개를 켠다. 나머지 축은 AI 값을 켠다.
- 출처: 서버가 세션에 저장한 추천과 제출값을 비교해 정한다 (브라우저가 보낸 출처는 받지 않는다)
        AI와 Discogs 둘 다 추천 → BOTH, 한쪽만 → AI / DISCOGS, 추천에 없던 값 → USER(관리자는 ADMIN)
        등록자가 끈 추천도 accepted = 0으로 남긴다 (AI 채택률·프롬프트 개선용)
"""
from tag_rules import AUDIO_LIMITS, ENERGIES, GENRES

LIST_AXES = ("moods", "situations", "times", "instruments", "subgenres")


def recommend(ai, discogs):
    """반환: (selected, suggested). selected = 처음에 켜 둘 값, suggested = {axis: {값: 출처}} (꺼진 추천 포함)"""
    ai = ai or {}
    dg = discogs if discogs and discogs.get("status") == "FOUND" else {}
    suggested = {axis: {} for axis in LIST_AXES + ("genre", "energy")}
    selected = {axis: list(ai.get(axis) or []) for axis in LIST_AXES}

    def mark(axis, name, src):
        prev = suggested[axis].get(name)
        suggested[axis][name] = "BOTH" if prev and prev != src else src

    for axis in LIST_AXES:
        for name in ai.get(axis) or []:
            mark(axis, name, "AI")
    for name in dg.get("subgenres") or []:
        mark("subgenres", name, "DISCOGS")
    subs = list(dg.get("subgenres") or [])
    subs += [x for x in ai.get("subgenres") or [] if x not in subs]
    selected["subgenres"] = subs[:AUDIO_LIMITS["subgenres"][2]]

    if ai.get("genre"):
        mark("genre", ai["genre"], "AI")
    if dg.get("genre"):
        mark("genre", dg["genre"], "DISCOGS")
    selected["genre"] = dg.get("genre") or ai.get("genre")
    if ai.get("energy"):
        mark("energy", ai["energy"], "AI")
    selected["energy"] = ai.get("energy")
    return selected, suggested


def clean_final(tags):
    """제출된 태그를 목록·개수 규칙으로 검사. 반환: (정리된 tags, 오류 문장 목록)"""
    tags = tags if isinstance(tags, dict) else {}
    out, errors = {}, []
    names = {"moods": "분위기", "situations": "상황", "times": "시간·날씨", "instruments": "악기", "subgenres": "세부 장르"}
    for axis, (enum, mn, mx) in AUDIO_LIMITS.items():
        vals = tags.get(axis) or []
        if not isinstance(vals, list) or any(v not in enum for v in vals) or len(set(vals)) != len(vals):
            errors.append(f"{names[axis]} 값이 올바르지 않아요.")
            continue
        if not mn <= len(vals) <= mx:
            errors.append(f"{names[axis]}는 {mn}~{mx}개 골라 주세요." if mn else f"{names[axis]}는 {mx}개까지 고를 수 있어요.")
        out[axis] = vals
    genre, energy = tags.get("genre") or None, tags.get("energy") or None
    if genre is not None and genre not in GENRES:
        errors.append("장르 값이 올바르지 않아요.")
    if energy is not None and energy not in ENERGIES:
        errors.append("에너지 값이 올바르지 않아요.")
    if genre is None:
        errors.append("장르 대분류를 하나 골라 주세요.")
    out["genre"], out["energy"] = genre, energy
    return out, errors


def origins(final, suggested, user_origin="USER"):
    """반환: (origin_of {(axis, 값): 출처}, rejected [(axis, 값, 출처)])"""
    origin_of, rejected = {}, []
    for axis, recs in suggested.items():
        chosen = final.get(axis)
        chosen = [chosen] if isinstance(chosen, str) else (chosen or [])
        for name in chosen:
            origin_of[(axis, name)] = recs.get(name, user_origin)
        for name, src in recs.items():
            if name not in chosen:
                rejected.append((axis, name, src))
    for axis in LIST_AXES + ("genre", "energy"):  # 추천이 아예 없던 축
        chosen = final.get(axis)
        for name in ([chosen] if isinstance(chosen, str) else (chosen or [])):
            origin_of.setdefault((axis, name), user_origin)
    return origin_of, rejected
