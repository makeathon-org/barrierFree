"""routing.py 단위 테스트 — osmnx 없이 순수 networkx 만으로 동작 검증.

  실행:  .venv/bin/python -m pytest test_routing.py   (pytest 있으면)
  또는:  .venv/bin/python test_routing.py             (직접 실행)
"""

import networkx as nx

import routing


def _line_graph():
    """A -계단- B -평지- C  와  A -평지- D -평지- C  두 갈래.

    계단 경로(A-B-C)가 더 짧지만, 휠체어는 계단을 배제하므로 우회해야 한다.
    """
    G = nx.MultiDiGraph()
    coords = {
        "A": (37.5925, 127.0163),
        "B": (37.5928, 127.0167),
        "C": (37.5931, 127.0171),
        "D": (37.5920, 127.0170),
    }
    for n, (lat, lng) in coords.items():
        G.add_node(n, x=lng, y=lat)

    def edge(u, v, length, features=frozenset()):
        G.add_edge(u, v, length=length, features=frozenset(features))
        G.add_edge(v, u, length=length, features=frozenset(features))

    edge("A", "B", 100, {"stairs"})      # 짧은 계단길
    edge("B", "C", 100)
    edge("A", "D", 180)                   # 긴 평지 우회
    edge("D", "C", 180)
    return G


def test_wheelchair_avoids_stairs():
    G = _line_graph()
    r = routing.find_route(G, "A", "C", "wheelchair", "safe")
    assert "B" not in r["path"], "휠체어는 계단(B)을 지나면 안 됨"
    assert r["path"] == ["A", "D", "C"]
    assert r["warning"] is None


def test_elderly_can_use_stairs_but_penalized():
    G = _line_graph()
    # 어르신은 계단 배제는 아니지만 페널티가 큼 → safe 전략에선 우회 선호
    safe = routing.find_route(G, "A", "C", "elderly", "safe")
    fast = routing.find_route(G, "A", "C", "elderly", "fast")
    # fast 는 페널티 약하게 → 짧은 계단길 선택
    assert "B" in fast["path"]
    # safe 는 페널티 강하게 → 우회 선택
    assert "B" not in safe["path"]


def test_no_path_fallback_warns():
    """모든 경로에 계단만 있으면 휠체어는 우회 불가 → 페널티 우회 + 경고."""
    G = nx.MultiDiGraph()
    for n, (lat, lng) in {"A": (37.59, 127.01), "B": (37.591, 127.011)}.items():
        G.add_node(n, x=lng, y=lat)
    G.add_edge("A", "B", length=50, features=frozenset({"stairs"}))
    G.add_edge("B", "A", length=50, features=frozenset({"stairs"}))
    r = routing.find_route(G, "A", "B", "wheelchair", "safe")
    assert r["path"] == ["A", "B"]
    assert r["warning"] is not None


def test_three_strategies_distinct_costs():
    G = _line_graph()
    routes = routing.find_routes(G, "A", "C", "heavy_luggage")
    assert set(routes) == {"safe", "balanced", "fast"}
    # 무거운 짐: safe 는 계단 회피, fast 는 짧은 길
    assert "B" not in routes["safe"]["path"]
    assert "B" in routes["fast"]["path"]


def test_plain_graph_supported():
    """MultiDiGraph 가 아닌 단순 Graph 도 동작해야 함."""
    G = nx.Graph()
    for n, (lat, lng) in {"A": (37.59, 127.01), "B": (37.591, 127.011),
                          "C": (37.592, 127.012)}.items():
        G.add_node(n, x=lng, y=lat)
    G.add_edge("A", "B", length=100, features=frozenset())
    G.add_edge("B", "C", length=100, features=frozenset())
    r = routing.find_route(G, "A", "C", "elderly", "balanced")
    assert r["path"] == ["A", "B", "C"]
    assert r["distance_m"] == 200.0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        print(f"  ok  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} 테스트 통과")
