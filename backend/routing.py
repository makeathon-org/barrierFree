"""배리어프리 보행 경로 라우팅 로직.

osmnx 에 의존하지 않고 순수 networkx 만으로 동작하므로 단위 테스트가 쉽다.
간선(edge)에는 다음 속성이 있다고 가정한다.
  - "length"   : 미터 단위 길이 (float)
  - "features" : 해당 간선이 가진 배리어 특성 집합 (set/frozenset of str)
                 예) {"stairs"}, {"steep", "rough"}, set()
"""

from __future__ import annotations

from typing import Callable, Iterable

import networkx as nx

# ---------------------------------------------------------------------------
# 사용자 유형별 프로파일
# ---------------------------------------------------------------------------
# dist_weight : 거리 자체에 곱하는 가중치 (이동 부담)
# exclude     : 해당 특성을 가진 간선은 그래프에서 사실상 제거(하드 배제)
# penalty     : 특성별 추가 비용 가중치
PROFILES: dict[str, dict] = {
    "wheelchair": {  # 휠체어
        "dist_weight": 1.2,
        "exclude": {"stairs", "wheelchair_no"},
        "penalty": {"steep": 20, "kerb": 10, "rough": 6, "narrow": 4},
    },
    "elderly": {  # 어르신 · 노약자
        "dist_weight": 1.5,
        "exclude": set(),
        "penalty": {"stairs": 8, "steep": 5, "kerb": 2, "rough": 3},
    },
    "leg_injury": {  # 다리 부상
        "dist_weight": 2.0,
        "exclude": set(),
        "penalty": {"stairs": 6, "steep": 3, "kerb": 2, "rough": 2},
    },
    "heavy_luggage": {  # 무거운 짐
        "dist_weight": 1.3,
        "exclude": set(),
        "penalty": {"stairs": 10, "steep": 6, "kerb": 4, "rough": 8, "narrow": 3},
    },
    "general": {  # 일반 보행자 (특별한 제약 없음, 최단·편의 위주)
        "dist_weight": 1.0,
        "exclude": set(),
        "penalty": {"stairs": 1, "steep": 1, "rough": 1},
    },
}

# 전략별 페널티 반영 강도
STRATEGY_SCALE: dict[str, float] = {"safe": 1.0, "balanced": 0.5, "fast": 0.1}

STRATEGIES = ("safe", "balanced", "fast")

# 보행 속도 (m/min)
WALK_SPEED = 72.0

# 하드 배제가 풀렸을 때(우회 불가) 위험 간선에 곱하는 큰 페널티
EXCLUDE_FALLBACK_PENALTY = 1000.0

DEFAULT_FEATURES: frozenset = frozenset()


def _feature_penalty(features: Iterable[str], penalty: dict[str, float]) -> float:
    """간선 특성 집합에 대한 페널티 합."""
    return sum(penalty.get(f, 0) for f in features)


def build_weight(
    profile: dict,
    strategy_scale: float,
    multigraph: bool,
    hard_exclude: bool = True,
) -> Callable:
    """networkx shortest_path 에 넘길 weight(u, v, data) 콜백 생성.

    cost = length * dist_weight * (1 + strategy_scale * Σ penalty[feature])

    hard_exclude=True 이면 배제 특성을 가진 간선은 None 을 반환해 그래프에서
    제외시킨다(networkx 는 None 을 반환하는 간선을 무시한다).
    hard_exclude=False 이면 배제 대신 EXCLUDE_FALLBACK_PENALTY 를 곱한다.
    """
    dist_weight = profile["dist_weight"]
    penalty = profile["penalty"]
    exclude = profile["exclude"]

    def edge_cost(features, length) -> float | None:
        features = features or DEFAULT_FEATURES
        excluded = bool(set(features) & exclude)
        if excluded and hard_exclude:
            return None
        mult = 1.0 + strategy_scale * _feature_penalty(features, penalty)
        if excluded and not hard_exclude:
            mult *= EXCLUDE_FALLBACK_PENALTY
        return length * dist_weight * mult

    if multigraph:
        # data 는 평행 간선들의 keydict: {key: attr_dict, ...}
        def weight(u, v, data):
            best = None
            for attr in data.values():
                c = edge_cost(attr.get("features"), attr.get("length", 1.0))
                if c is None:
                    continue
                if best is None or c < best:
                    best = c
            return best  # 모두 배제되면 None → 간선 제거
    else:
        def weight(u, v, data):
            return edge_cost(data.get("features"), data.get("length", 1.0))

    return weight


def _edge_length(G, u, v, multigraph: bool) -> float:
    data = G[u][v]
    if multigraph:
        return min(d.get("length", 0.0) for d in data.values())
    return data.get("length", 0.0)


def path_length(G, path: list, multigraph: bool | None = None) -> float:
    """경로(노드 리스트)의 실제 길이 합(미터)."""
    if multigraph is None:
        multigraph = G.is_multigraph()
    total = 0.0
    for u, v in zip(path[:-1], path[1:]):
        total += _edge_length(G, u, v, multigraph)
    return total


def _edge_grade(G, u, v, multigraph: bool) -> float:
    """간선의 경사 절댓값(0~1). 평행 간선이면 실제 통행에 쓰인(최단 길이) 간선 기준.
    grade_abs 가 없으면 0.0."""
    data = G[u][v]
    if multigraph:
        best = min(data.values(), key=lambda d: d.get("length", float("inf")))
        return float(best.get("grade_abs") or 0.0)
    return float(data.get("grade_abs") or 0.0)


def segment_grades(G, path: list, multigraph: bool | None = None) -> list:
    """경로(노드 리스트)의 각 구간 경사 절댓값 목록.
    coords 가 N개면 segment_grades 는 N-1개(각 인접 구간의 기울기)."""
    if multigraph is None:
        multigraph = G.is_multigraph()
    return [
        round(_edge_grade(G, u, v, multigraph), 4)
        for u, v in zip(path[:-1], path[1:])
    ]


def find_route(
    G,
    orig_node,
    dest_node,
    user_type: str,
    strategy: str,
    multigraph: bool | None = None,
) -> dict:
    """단일 전략 경로 계산.

    반환: {"path": [...nodes], "distance_m": float, "minutes": float,
           "warning": str | None}
    경로 자체가 없으면 nx.NetworkXNoPath 를 그대로 올린다.
    """
    if user_type not in PROFILES:
        raise ValueError(f"알 수 없는 user_type: {user_type}")
    if strategy not in STRATEGY_SCALE:
        raise ValueError(f"알 수 없는 strategy: {strategy}")

    if multigraph is None:
        multigraph = G.is_multigraph()

    profile = PROFILES[user_type]
    scale = STRATEGY_SCALE[strategy]
    warning = None

    weight = build_weight(profile, scale, multigraph, hard_exclude=True)
    try:
        path = nx.shortest_path(G, orig_node, dest_node, weight=weight)
    except nx.NetworkXNoPath:
        # 하드 배제 때문에 길이 끊긴 경우 → 배제를 큰 페널티로 완화해 재탐색
        weight = build_weight(profile, scale, multigraph, hard_exclude=False)
        path = nx.shortest_path(G, orig_node, dest_node, weight=weight)
        warning = "배제 조건(예: 계단)을 피할 수 없어 위험 구간을 포함한 우회로를 안내합니다."

    distance_m = path_length(G, path, multigraph)
    minutes = distance_m / WALK_SPEED
    return {
        "path": path,
        "distance_m": round(distance_m, 1),
        "minutes": round(minutes, 1),
        "warning": warning,
        "segment_grades": segment_grades(G, path, multigraph),
    }


def find_routes(G, orig_node, dest_node, user_type: str, multigraph: bool | None = None) -> dict:
    """safe / balanced / fast 세 전략 경로를 모두 계산해 반환."""
    if multigraph is None:
        multigraph = G.is_multigraph()
    out = {}
    for strategy in STRATEGIES:
        out[strategy] = find_route(
            G, orig_node, dest_node, user_type, strategy, multigraph
        )
    return out
