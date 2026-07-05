"""배리어프리 보행 경로 추천 API (FastAPI).

엔드포인트
  GET  /route    : 출발/도착/유형으로 safe·balanced·fast 3경로 반환
  GET  /reports  : 위험 제보 목록(메모리)
  POST /reports  : 위험 제보 추가
  GET  /weather  : 날씨 스텁(표시 전용)
  GET  /health   : 헬스체크
"""

from __future__ import annotations

import itertools
from contextlib import asynccontextmanager

import networkx as nx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from dotenv import load_dotenv

    load_dotenv()  # backend/.env 의 GEMINI_API_KEY 등 로드
except Exception:
    pass

import ai as ai_mod
import graph as graph_mod
import routing


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 시작 시 그래프(고도/경사 포함)를 미리 적재해 첫 요청을 빠르게.
    graph_mod.get_graph()
    yield


app = FastAPI(title="배리어프리 보행 경로 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

USER_TYPES = ("wheelchair", "elderly", "leg_injury", "heavy_luggage", "general")


# ---------------------------------------------------------------------------
# 위험 제보 (메모리)
# ---------------------------------------------------------------------------
class ReportIn(BaseModel):
    lat: float
    lng: float
    type: str = "기타"
    desc: str = ""


_id_counter = itertools.count(1)
REPORTS: list[dict] = [
    {
        "id": next(_id_counter),
        "lat": 37.5931,
        "lng": 127.0172,
        "type": "공사중",
        "desc": "보도 공사로 통행 불편 (계단 우회 필요)",
    },
    {
        "id": next(_id_counter),
        "lat": 37.5917,
        "lng": 127.0151,
        "type": "급경사",
        "desc": "경사가 급해 휠체어·유모차 주의",
    },
]


# ---------------------------------------------------------------------------
# 경로
# ---------------------------------------------------------------------------
def _path_to_coords(G, path: list) -> list[list[float]]:
    coords = []
    for n in path:
        d = G.nodes[n]
        coords.append([d["y"], d["x"]])  # [lat, lng]
    return coords


_FEATURE_KEYS = ("stairs", "steep", "rough", "narrow", "wheelchair_no")


def _feature_counts(G, path: list, multigraph: bool) -> dict:
    """경로가 지나는 간선들의 특성별 개수."""
    counts = {k: 0 for k in _FEATURE_KEYS}
    for u, v in zip(path[:-1], path[1:]):
        data = G[u][v]
        if multigraph:
            best = min(data.values(), key=lambda d: d.get("length", float("inf")))
            feats = best.get("features", ())
        else:
            feats = data.get("features", ())
        for f in feats:
            if f in counts:
                counts[f] += 1
    return counts


@app.get("/route")
def route(
    orig_lat: float = Query(...),
    orig_lng: float = Query(...),
    dest_lat: float = Query(...),
    dest_lng: float = Query(...),
    user_type: str = Query("wheelchair"),
):
    if user_type not in USER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"user_type 은 {USER_TYPES} 중 하나여야 합니다.",
        )

    G = graph_mod.get_graph()
    multigraph = G.is_multigraph()

    orig_node = graph_mod.nearest_node(G, orig_lat, orig_lng)
    dest_node = graph_mod.nearest_node(G, dest_lat, dest_lng)
    if orig_node is None or dest_node is None:
        raise HTTPException(status_code=500, detail="그래프에서 노드를 찾지 못했습니다.")

    routes = {}
    for strategy in routing.STRATEGIES:
        try:
            r = routing.find_route(
                G, orig_node, dest_node, user_type, strategy, multigraph
            )
            routes[strategy] = {
                "coords": _path_to_coords(G, r["path"]),
                "segment_grades": r["segment_grades"],
                "distance_m": r["distance_m"],
                "minutes": r["minutes"],
                "warning": r["warning"],
                "features": _feature_counts(G, r["path"], multigraph),
            }
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            routes[strategy] = {
                "coords": [],
                "segment_grades": [],
                "distance_m": 0.0,
                "minutes": 0.0,
                "warning": "출발지와 도착지를 잇는 경로를 찾지 못했습니다.",
                "features": {k: 0 for k in _FEATURE_KEYS},
            }

    return {"user_type": user_type, "routes": routes}


# ---------------------------------------------------------------------------
# AI 추천 (Gemini 하이브리드)
# ---------------------------------------------------------------------------
def _route_summary(G, multigraph, r: dict) -> dict:
    """find_route 결과를 AI 입력용 요약(거리/시간/특성개수/최대경사)으로 변환."""
    path = r["path"]
    grades = r.get("segment_grades") or [0]
    return {
        "valid": len(path) >= 2,
        "distance_m": r["distance_m"],
        "minutes": r["minutes"],
        "warning": r["warning"],
        "max_grade_pct": round(max(grades) * 100, 1),
        "features": _feature_counts(G, path, multigraph),
    }


@app.get("/recommend")
def recommend(
    orig_lat: float = Query(...),
    orig_lng: float = Query(...),
    dest_lat: float = Query(...),
    dest_lng: float = Query(...),
    user_type: str = Query("wheelchair"),
):
    """그래프가 만든 3경로를 요약해 Gemini 에 추천을 요청한다(키 없으면 규칙 폴백)."""
    if user_type not in USER_TYPES:
        raise HTTPException(status_code=400, detail="잘못된 user_type")

    G = graph_mod.get_graph()
    multigraph = G.is_multigraph()
    orig_node = graph_mod.nearest_node(G, orig_lat, orig_lng)
    dest_node = graph_mod.nearest_node(G, dest_lat, dest_lng)

    summaries: dict = {}
    for strategy in routing.STRATEGIES:
        try:
            r = routing.find_route(G, orig_node, dest_node, user_type, strategy, multigraph)
            summaries[strategy] = _route_summary(G, multigraph, r)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            summaries[strategy] = {"valid": False}

    rec = ai_mod.recommend(user_type, summaries)
    return {"user_type": user_type, "recommendation": rec, "summaries": summaries}


# ---------------------------------------------------------------------------
# 계단 레이어
# ---------------------------------------------------------------------------
@app.get("/stairs")
def stairs():
    """'stairs' 특성을 가진 간선들의 시작-끝 좌표 목록.
    반환: { stairs: [{coords:[[lat,lng],[lat,lng]], id}, ...] }
    (양방향 중복 간선은 한 번만 반환)
    """
    G = graph_mod.get_graph()
    multi = G.is_multigraph()
    edges = G.edges(keys=True, data=True) if multi else G.edges(data=True)
    seen: set = set()
    out: list[dict] = []
    for edge in edges:
        u, v, d = edge[0], edge[1], edge[-1]
        if "stairs" not in (d.get("features") or ()):
            continue
        key = (u, v) if u <= v else (v, u)
        if key in seen:
            continue
        seen.add(key)
        nu, nv = G.nodes[u], G.nodes[v]
        out.append(
            {
                "coords": [[nu["y"], nu["x"]], [nv["y"], nv["x"]]],
                "id": f"{key[0]}-{key[1]}",
            }
        )
    return {"stairs": out}


# ---------------------------------------------------------------------------
# 제보
# ---------------------------------------------------------------------------
@app.get("/reports")
def get_reports():
    return REPORTS


@app.post("/reports")
def add_report(report: ReportIn):
    item = {
        "id": next(_id_counter),
        "lat": report.lat,
        "lng": report.lng,
        "type": report.type,
        "desc": report.desc,
    }
    REPORTS.append(item)
    return item


# ---------------------------------------------------------------------------
# 날씨 (스텁) / 헬스
# ---------------------------------------------------------------------------
def _classify_weather(code: int, precip: float):
    """WMO weather_code → (condition, state, alert, note)."""
    if code in (71, 73, 75, 77, 85, 86):  # 눈
        return ("눈", "눈이 오는", True,
                "눈으로 빙판·미끄럼 위험이 있어요. 계단·경사 구간을 피하세요.")
    if code in (51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99) or precip > 0:  # 비
        return ("비", "비가 오는", True,
                "비가 와 노면이 미끄러울 수 있어요. 계단·경사 구간을 주의하세요.")
    if code in (45, 48):  # 안개
        return ("안개", "안개 낀", False, "안개로 시야가 좁아요. 천천히 이동하세요.")
    if code in (1, 2, 3):  # 흐림
        return ("흐림", "흐린", False, "보행하기 무난한 날씨예요.")
    return ("맑음", "맑은", False, "보행하기 좋은 날씨예요.")


@app.get("/weather")
def weather():
    """성신여대 일대 실시간 날씨 (Open-Meteo, API 키 불필요)."""
    import json as _json
    import urllib.request

    fallback = {
        "condition": "맑음", "temp": 20, "precip": 0.0,
        "note": "보행하기 좋은 날씨예요.", "alert": False,
        "state": "맑은", "source": "fallback",
    }
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={graph_mod.CENTER_LAT}&longitude={graph_mod.CENTER_LNG}"
            "&current=temperature_2m,precipitation,weather_code&timezone=Asia%2FSeoul"
        )
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _json.load(resp)
        cur = data["current"]
        code = int(cur.get("weather_code", 0))
        temp = cur.get("temperature_2m")
        precip = float(cur.get("precipitation", 0) or 0)
        condition, state, alert, note = _classify_weather(code, precip)
        return {
            "condition": condition, "temp": temp, "precip": precip,
            "note": note, "alert": alert, "state": state, "source": "open-meteo",
        }
    except Exception as exc:
        print(f"[weather] 실시간 조회 실패 → 폴백: {exc}")
        return fallback


@app.get("/health")
def health():
    return {"status": "ok", **graph_mod.graph_meta()}
