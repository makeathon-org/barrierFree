"""Gemini 기반 경로 추천 (하이브리드).

그래프가 계산한 safe/balanced/fast 후보 경로의 데이터 요약을 Gemini 에 주고,
사용자 유형에 맞춰 어떤 경로를 왜 추천하는지 받아온다.
GEMINI_API_KEY 가 없거나 호출 실패 시에는 규칙 기반 폴백 추천을 사용한다.
"""

from __future__ import annotations

import json
import os

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

# 사용자 유형 한국어 라벨 + 핵심 회피 요소(프롬프트 설명용)
USER_TYPE_KO = {
    "wheelchair": "휠체어 이용자 (계단·턱 통행 불가, 급경사·좁은길에 매우 취약)",
    "elderly": "어르신·노약자 (계단·급경사에 부담, 체력 소모 주의)",
    "leg_injury": "다리 부상자 (계단·경사가 통증 유발, 거리보다 편의 우선)",
    "heavy_luggage": "무거운 짐 운반 (계단·거친 노면·급경사에서 이동 매우 힘듦)",
    "general": "일반 보행자 (특별한 제약 없음, 최단·편의 위주)",
}

STRATEGY_KO = {"safe": "안전", "balanced": "균형", "fast": "빠름"}


def _client():
    """API 키가 있으면 genai 클라이언트, 없으면 None."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    try:
        from google import genai

        return genai.Client(api_key=key)
    except Exception as exc:  # 패키지 부재 등
        print(f"[ai] genai 클라이언트 생성 실패: {exc}")
        return None


def _build_prompt(user_type: str, summaries: dict) -> str:
    user_desc = USER_TYPE_KO.get(user_type, user_type)
    lines = [
        "당신은 배리어프리(무장애) 보행 경로 추천 도우미입니다.",
        f"사용자 유형: {user_desc}",
        "",
        "아래는 동일 출발지→도착지에 대해 그래프 알고리즘이 계산한 3가지 후보 경로의 데이터입니다.",
        "각 수치는 실제 보행로 기반이며, 계단/급경사(8%↑)/거친노면/좁은길 구간 '개수'와",
        "거리(m)·예상시간(분)·최대경사(%)·경고를 포함합니다.",
        "",
    ]
    for strat in ("safe", "balanced", "fast"):
        s = summaries.get(strat)
        if not s or not s.get("valid"):
            lines.append(f"- {STRATEGY_KO[strat]}({strat}): 경로 없음")
            continue
        f = s["features"]
        lines.append(
            f"- {STRATEGY_KO[strat]}({strat}): 거리 {s['distance_m']}m, "
            f"약 {s['minutes']}분, 계단 {f['stairs']}, 급경사 {f['steep']}, "
            f"거친노면 {f['rough']}, 좁은길 {f['narrow']}, "
            f"최대경사 {s['max_grade_pct']}%"
            + (f", 경고: {s['warning']}" if s.get("warning") else "")
        )
    lines += [
        "",
        "위 데이터에 근거해 이 사용자에게 가장 적합한 경로 하나를 추천하세요.",
        "거리만이 아니라 사용자 유형의 취약 요소(계단·경사 등)를 우선 고려하세요.",
        "반드시 아래 JSON 형식으로만, 한국어로 답하세요:",
        '{"recommended":"safe|balanced|fast",'
        '"reason":"추천 이유 1~2문장(데이터 수치를 근거로)",'
        '"notes":{"safe":"한 줄 평","balanced":"한 줄 평","fast":"한 줄 평"}}',
    ]
    return "\n".join(lines)


def _fallback(user_type: str, summaries: dict) -> dict:
    """키 없음/실패 시 규칙 기반 추천: 유효 경로 중 (유형 가중 장애물 → 거리) 최소."""
    weights = {
        "wheelchair": {"stairs": 100, "steep": 8, "rough": 3, "narrow": 2},
        "elderly": {"stairs": 8, "steep": 5, "rough": 3, "narrow": 1},
        "leg_injury": {"stairs": 6, "steep": 3, "rough": 2, "narrow": 1},
        "heavy_luggage": {"stairs": 10, "steep": 6, "rough": 8, "narrow": 3},
    }.get(user_type, {"stairs": 8, "steep": 5, "rough": 3, "narrow": 1})

    best, best_score = None, None
    for strat in ("safe", "balanced", "fast"):
        s = summaries.get(strat)
        if not s or not s.get("valid"):
            continue
        f = s["features"]
        score = sum(weights.get(k, 0) * f.get(k, 0) for k in weights) + s["distance_m"] / 100.0
        if best_score is None or score < best_score:
            best, best_score = strat, score
    best = best or "balanced"
    return {
        "recommended": best,
        "reason": "AI 키가 설정되지 않아 규칙 기반으로 추천했습니다. 유형별 장애물 가중치와 거리를 함께 고려한 결과입니다.",
        "notes": {
            "safe": "장애물을 최대한 회피한 경로",
            "balanced": "안전과 시간의 절충",
            "fast": "최단 시간 우선(장애물 포함 가능)",
        },
        "source": "fallback",
    }


def recommend(user_type: str, summaries: dict) -> dict:
    """Gemini 추천. 실패 시 규칙 기반 폴백. 항상 dict 반환."""
    client = _client()
    if client is None:
        return _fallback(user_type, summaries)
    try:
        from google.genai import types

        resp = client.models.generate_content(
            model=MODEL,
            contents=_build_prompt(user_type, summaries),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
            ),
        )
        data = json.loads(resp.text)
        rec = data.get("recommended")
        if rec not in ("safe", "balanced", "fast"):
            raise ValueError(f"잘못된 recommended: {rec}")
        # 추천된 경로가 실제로 유효한지 확인, 아니면 폴백
        if not summaries.get(rec, {}).get("valid"):
            raise ValueError("추천 경로가 유효하지 않음")
        data["source"] = "gemini"
        data.setdefault("notes", {})
        return data
    except Exception as exc:
        print(f"[ai] Gemini 호출/파싱 실패 → 폴백: {exc}")
        fb = _fallback(user_type, summaries)
        fb["source"] = "fallback-error"
        return fb
