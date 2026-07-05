"""OSM 보행 그래프 적재 + 배리어 특성 분류 + 수동 태깅(HAND_TAGS).

osmnx 로 성신여대입구역 일대 보행 네트워크를 받아 graphml 로 캐싱하고,
로드할 때마다 간선 특성을 재분류한 뒤 데모용 수동 태그를 입힌다.

osmnx 를 쓸 수 없거나(설치 실패) OSM 다운로드가 실패하면, 데모가 멈추지
않도록 동일한 좌표계의 합성 격자 그래프로 대체한다.
"""

from __future__ import annotations

import ast
import json
import math
import os
import threading

import networkx as nx

# ---------------------------------------------------------------------------
# 데모 구역 설정
# ---------------------------------------------------------------------------
CENTER_LAT = 37.5925
CENTER_LNG = 127.0163
RADIUS_M = 800
NETWORK_TYPE = "walk"

_HERE = os.path.dirname(os.path.abspath(__file__))
GRAPHML_PATH = os.path.join(_HERE, "sungshin_walk.graphml")

# 서울시 고도(표고) 데이터 — 성신여대 일대 점군 [{lat,lng,HEIGHT}, ...]
# 이 점군으로 각 그래프 노드의 고도를 보간 → 간선 경사(grade)를 계산해
# 경사도(steep)를 실제 지형 기반으로 반영한다.
HEIGHT_JSON_PATH = os.path.join(_HERE, "data", "sungshin_height.json")

# OSM 다운로드 시 보존할 태그 (반드시 그래프 생성 전에 등록해야 함!
# 안 하면 surface/incline/width/wheelchair 가 사라져 특성이 전부 빈 집합이 됨)
EXTRA_WAY_TAGS = ["surface", "incline", "width", "est_width", "wheelchair"]

# 거친 노면으로 간주할 surface 값
ROUGH_SURFACES = {
    "unpaved", "gravel", "ground", "dirt", "sand",
    "cobblestone", "pebblestone", "grass",
}

# 좁은 길 기준 (m)
NARROW_WIDTH_M = 1.2
# 급경사 기준 (비율). 8%
STEEP_GRADE = 0.08

# 데모용 수동 태깅 — 듬성한 OSM 을 보강하고 우회를 확실히 보여준다.
# 각 좌표에서 가장 가까운 노드에 연결된 간선들에 특성을 강제로 부여한다.
HAND_TAGS = [
    {"lat": 37.5930, "lng": 127.0170, "features": {"stairs"}},
    {"lat": 37.5918, "lng": 127.0150, "features": {"steep"}},
    {"lat": 37.5912, "lng": 127.0182, "features": {"rough"}},
]


# ---------------------------------------------------------------------------
# osmnx (선택적)
# ---------------------------------------------------------------------------
try:
    import osmnx as ox  # type: ignore

    # 그래프 생성 전에 유용한 태그 등록
    try:
        existing = list(ox.settings.useful_tags_way)
        for tag in EXTRA_WAY_TAGS:
            if tag not in existing:
                existing.append(tag)
        ox.settings.useful_tags_way = existing
    except Exception:  # pragma: no cover - osmnx 버전차 방어
        pass

    _HAS_OSMNX = True
except Exception:  # pragma: no cover
    ox = None
    _HAS_OSMNX = False


# ---------------------------------------------------------------------------
# 값 파싱 헬퍼
# ---------------------------------------------------------------------------
def _first(value):
    """osmnx 속성은 list 일 수 있고, graphml 로드 시 "['a','b']" 문자열일 수도 있다.
    항상 첫 값(스칼라)만 돌려준다."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = ast.literal_eval(s)
                if isinstance(parsed, (list, tuple)) and parsed:
                    return parsed[0]
            except (ValueError, SyntaxError):
                pass
    return value


def _to_float(value):
    """'1.5', '1,5 m', '0.9' 등에서 첫 숫자를 float 로."""
    v = _first(value)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", ".")
    num = ""
    seen_dot = False
    for ch in s:
        if ch.isdigit():
            num += ch
        elif ch == "." and not seen_dot:
            num += ch
            seen_dot = True
        elif num:
            break
    try:
        return float(num) if num else None
    except ValueError:
        return None


def _parse_incline(value):
    """incline 태그를 경사 비율(절댓값)로. '10%'→0.10, 'up'/'steep'→0.30."""
    v = _first(value)
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"up", "down", "steep", "yes"}:
        return 0.30
    if s.endswith("%"):
        n = _to_float(s[:-1])
        return abs(n) / 100.0 if n is not None else None
    # '0.10' 또는 '10' 같은 값
    n = _to_float(s)
    if n is None:
        return None
    n = abs(n)
    return n if n <= 1.0 else n / 100.0


# ---------------------------------------------------------------------------
# 특성 분류
# ---------------------------------------------------------------------------
def classify_edge(data: dict) -> set:
    """간선 속성으로부터 배리어 특성 집합을 산출한다.
    kerb 는 OSM 에서 깔끔히 안 나오므로 여기서 다루지 않고 HAND_TAGS 로만 처리.
    """
    features: set = set()

    highway = _first(data.get("highway"))
    if highway == "steps":
        features.add("stairs")

    # 급경사: 고도(grade_abs) 가 있으면 우선, 없으면 incline 태그
    grade = _to_float(data.get("grade_abs"))
    if grade is not None and grade > STEEP_GRADE:
        features.add("steep")
    else:
        inc = _parse_incline(data.get("incline"))
        if inc is not None and inc > STEEP_GRADE:
            features.add("steep")

    surface = _first(data.get("surface"))
    if surface is not None and str(surface).lower() in ROUGH_SURFACES:
        features.add("rough")

    width = _to_float(data.get("width"))
    if width is None:
        width = _to_float(data.get("est_width"))
    if width is not None and width < NARROW_WIDTH_M:
        features.add("narrow")

    wc = _first(data.get("wheelchair"))
    if wc is not None and str(wc).lower() == "no":
        features.add("wheelchair_no")

    return features


def classify_features(G) -> None:
    """모든 간선에 data['features'] 를 (재)부여한다."""
    if G.is_multigraph():
        for _u, _v, _k, data in G.edges(keys=True, data=True):
            data["features"] = frozenset(classify_edge(data))
    else:
        for _u, _v, data in G.edges(data=True):
            data["features"] = frozenset(classify_edge(data))


# ---------------------------------------------------------------------------
# 지리 헬퍼
# ---------------------------------------------------------------------------
def haversine_m(lat1, lng1, lat2, lng2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def nearest_node(G, lat: float, lng: float):
    """그래프에서 (lat,lng) 와 가장 가까운 노드 id. 노드는 x=lng, y=lat 를 가진다."""
    best, best_d = None, float("inf")
    for n, d in G.nodes(data=True):
        ny, nx_ = d.get("y"), d.get("x")
        if ny is None or nx_ is None:
            continue
        dist = haversine_m(lat, lng, ny, nx_)
        if dist < best_d:
            best, best_d = n, dist
    return best


# ---------------------------------------------------------------------------
# 수동 태깅
# ---------------------------------------------------------------------------
def apply_hand_tags(G) -> None:
    """HAND_TAGS 좌표마다 가장 가까운 노드에 연결된 모든 간선에 특성을 추가한다."""
    is_multi = G.is_multigraph()
    for tag in HAND_TAGS:
        node = nearest_node(G, tag["lat"], tag["lng"])
        if node is None:
            continue
        extra = set(tag["features"])
        # 해당 노드에 연결된 간선(양방향) 모두에 적용
        incident = list(G.edges(node, keys=True, data=True)) if is_multi else list(
            G.edges(node, data=True)
        )
        # in-edges 까지 포함
        if G.is_directed():
            if is_multi:
                incident += list(G.in_edges(node, keys=True, data=True))
            else:
                incident += list(G.in_edges(node, data=True))
        for edge in incident:
            data = edge[-1]
            cur = set(data.get("features", frozenset()))
            data["features"] = frozenset(cur | extra)


# ---------------------------------------------------------------------------
# 합성 격자 그래프 (osmnx/네트워크 불가 시 폴백)
# ---------------------------------------------------------------------------
def build_synthetic_graph() -> nx.MultiDiGraph:
    """성신여대 일대를 덮는 격자형 보행 그래프. 데모가 항상 동작하도록.
    일부 간선에 결정적으로 배리어 특성을 심어 우회가 보이게 한다."""
    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    n = 13  # 13x13 격자
    span = RADIUS_M  # 한 변 약 800m
    # 위경도 1도당 거리 근사
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * math.cos(math.radians(CENTER_LAT))
    step_m = span / (n - 1)
    lat0 = CENTER_LAT - (span / 2) / m_per_deg_lat
    lng0 = CENTER_LNG - (span / 2) / m_per_deg_lng

    def nid(i, j):
        return i * n + j

    for i in range(n):
        for j in range(n):
            lat = lat0 + (i * step_m) / m_per_deg_lat
            lng = lng0 + (j * step_m) / m_per_deg_lng
            G.add_node(nid(i, j), x=lng, y=lat)

    def add(i1, j1, i2, j2):
        a, b = nid(i1, j1), nid(i2, j2)
        ay, ax = G.nodes[a]["y"], G.nodes[a]["x"]
        by, bx = G.nodes[b]["y"], G.nodes[b]["x"]
        length = haversine_m(ay, ax, by, bx)
        for u, v in ((a, b), (b, a)):
            G.add_edge(u, v, length=length, highway="footway")

    for i in range(n):
        for j in range(n):
            if j + 1 < n:
                add(i, j, i, j + 1)
            if i + 1 < n:
                add(i, j, i + 1, j)
    return G


# ---------------------------------------------------------------------------
# 고도(경사도) — 서울시 표고 점군 기반
# ---------------------------------------------------------------------------
_elev_index = None  # (tree|None, xy(ndarray), heights(ndarray), mx, my) | False


def _load_height_index():
    """표고 점군을 적재해 (KD-Tree, 좌표, 고도, 미터환산계수) 를 캐싱."""
    global _elev_index
    if _elev_index is not None:
        return _elev_index
    if not os.path.exists(HEIGHT_JSON_PATH):
        print(f"[graph] 표고 데이터 없음({HEIGHT_JSON_PATH}) → 경사도 보간 생략")
        _elev_index = False
        return False
    try:
        import numpy as np

        with open(HEIGHT_JSON_PATH, encoding="utf-8") as f:
            pts = json.load(f)
        lats = np.array([p["lat"] for p in pts], dtype=float)
        lngs = np.array([p["lng"] for p in pts], dtype=float)
        heights = np.array([p["HEIGHT"] for p in pts], dtype=float)
        lat0 = float(lats.mean())
        mx = 111320.0 * math.cos(math.radians(lat0))  # 경도 1도 ≈ m
        my = 111320.0                                   # 위도 1도 ≈ m
        xy = np.column_stack([lngs * mx, lats * my])
        try:
            from scipy.spatial import cKDTree

            tree = cKDTree(xy)
        except Exception:
            tree = None
        _elev_index = (tree, xy, heights, mx, my)
        print(f"[graph] 표고 점군 {len(heights)}개 적재 (KDTree={'O' if tree is not None else 'X'})")
    except Exception as exc:  # numpy 등 부재 시
        print(f"[graph] 표고 데이터 적재 실패 → 경사도 보간 생략: {exc}")
        _elev_index = False
    return _elev_index


def add_elevations(G, k: int = 3) -> bool:
    """각 노드 고도 = 가장 가까운 표고점 k개 평균. 이후 간선 경사(grade_abs)를 부여.

    grade_abs 가 채워지면 classify_edge() 가 STEEP_GRADE(8%) 초과 간선을
    'steep' 으로 분류한다. 반환: 성공 여부.
    """
    idx = _load_height_index()
    if not idx:
        return False
    import numpy as np

    tree, xy, heights, mx, my = idx
    for n, d in G.nodes(data=True):
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            continue
        px, py = x * mx, y * my
        if tree is not None:
            _dist, ii = tree.query([px, py], k=k)
            ii = np.atleast_1d(ii)
        else:
            dd = (xy[:, 0] - px) ** 2 + (xy[:, 1] - py) ** 2
            ii = np.argpartition(dd, min(k, len(dd) - 1))[:k]
        d["elevation"] = float(np.mean(heights[ii]))

    edges = G.edges(keys=True, data=True) if G.is_multigraph() else G.edges(data=True)
    for edge in edges:
        u, v, d = edge[0], edge[1], edge[-1]
        eu = G.nodes[u].get("elevation")
        ev = G.nodes[v].get("elevation")
        length = d.get("length", 0.0) or 0.0
        if eu is not None and ev is not None and length > 0:
            grade = (ev - eu) / length
            d["grade"] = grade
            d["grade_abs"] = abs(grade)
    return True


def ensure_edge_grades(G) -> None:
    """grade_abs 가 없는 간선은 OSM incline 태그로 경사 절댓값을 채운다."""
    edges = G.edges(keys=True, data=True) if G.is_multigraph() else G.edges(data=True)
    for edge in edges:
        d = edge[-1]
        if d.get("grade_abs") is None:
            inc = _parse_incline(d.get("incline"))
            if inc is not None:
                d["grade_abs"] = inc


# ---------------------------------------------------------------------------
# 적재 (싱글톤)
# ---------------------------------------------------------------------------
_graph = None
_graph_meta = {"source": None, "elevation": False}
_lock = threading.Lock()


def _load_raw_graph():
    """원본(특성 미분류) 그래프를 적재. osmnx → graphml 캐시 → 합성 순으로 시도."""
    if _HAS_OSMNX:
        try:
            if os.path.exists(GRAPHML_PATH):
                G = ox.load_graphml(GRAPHML_PATH)
                return G, "graphml-cache"
            G = ox.graph_from_point(
                (CENTER_LAT, CENTER_LNG),
                dist=RADIUS_M,
                network_type=NETWORK_TYPE,
            )
            ox.save_graphml(G, GRAPHML_PATH)
            return G, "osm-download"
        except Exception as exc:  # 네트워크/파싱 실패 → 폴백
            print(f"[graph] osmnx 적재 실패 → 합성 그래프 사용: {exc}")
    else:
        print("[graph] osmnx 미설치 → 합성 그래프 사용")
    return build_synthetic_graph(), "synthetic"


def get_graph():
    """특성 분류 + 수동 태깅이 끝난 그래프를 1회만 적재해 반환(싱글톤)."""
    global _graph
    if _graph is not None:
        return _graph
    with _lock:
        if _graph is not None:
            return _graph
        G, source = _load_raw_graph()
        elev_ok = add_elevations(G)  # 표고 점군으로 간선 경사 부여
        ensure_edge_grades(G)        # 고도 없는 간선은 incline 태그로 grade_abs 보강
        classify_features(G)         # grade_abs > 8% → steep
        apply_hand_tags(G)
        _graph_meta["source"] = source
        _graph_meta["elevation"] = elev_ok
        from collections import Counter

        fc: Counter = Counter()
        for *_, d in (G.edges(keys=True, data=True) if G.is_multigraph() else G.edges(data=True)):
            for f in d.get("features", ()):  # type: ignore[union-attr]
                fc[f] += 1
        print(f"[graph] source={source} elevation={elev_ok} "
              f"nodes={G.number_of_nodes()} edges={G.number_of_edges()} "
              f"features={dict(fc)}")
        _graph = G
        return _graph


def graph_source() -> str | None:
    return _graph_meta["source"]


def graph_meta() -> dict:
    return dict(_graph_meta)
