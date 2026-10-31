from types import SimpleNamespace

from catia_manual.gui.graph_geometry import EdgeRouter, layout_nodes


def snapshot(edges, kinds=None, view="function-map"):
    keys = list(dict.fromkeys(k for edge in edges for k in (edge["source"], edge["target"])))
    return SimpleNamespace(
        nodes=[{"id": k, "kind": (kinds or {}).get(k, "article"), "role": ""} for k in keys],
        edges=edges,
        root=keys[0],
        view=view,
    )


def test_tidy_subtrees_do_not_interleave_and_saved_cards_survive_expansion():
    edges = [
        {"source": "root", "target": "a"},
        {"source": "root", "target": "b"},
        {"source": "a", "target": "a1"},
        {"source": "a", "target": "a2"},
        {"source": "b", "target": "b1"},
        {"source": "b", "target": "b2"},
    ]
    graph = snapshot(edges)
    positions = layout_nodes(graph)
    assert max(positions[k][1] for k in ("a1", "a2")) < min(positions[k][1] for k in ("b1", "b2"))
    positions["a"] = [-100, 370]
    graph.nodes.append({"id": "a3", "kind": "article", "role": ""})
    graph.edges.append({"source": "a", "target": "a3"})
    expanded = layout_nodes(graph, positions)
    assert all(expanded[k] == v for k, v in positions.items())
    assert expanded["a3"][0] > expanded["a"][0]
    assert all(
        abs(expanded["a3"][0] - p[0]) >= 260 or abs(expanded["a3"][1] - p[1]) >= 108
        for k, p in expanded.items()
        if k != "a3"
    )


def test_relationship_neighborhoods_balance_around_the_center():
    graph = snapshot(
        [{"source": "root", "target": str(i)} for i in range(6)],
        {"0": "command", "1": "sample", "2": "image"},
        "page-relations",
    )
    positions = layout_nodes(graph)
    assert positions["root"] == [0, 0]
    assert positions["0"][0] < 0 and positions["1"][0] < 0
    assert all(positions[k][0] > 0 for k in ("2", "3", "4", "5"))
    assert positions["0"][1] + positions["1"][1] == 0
    assert min(positions[k][1] for k in ("2", "3", "4", "5")) < 0


def test_reciprocal_links_have_distinct_ports_and_keep_the_original_direction():
    rectangles = {"a": (0, 0, 236, 84), "b": (352, 0, 236, 84)}
    routes = EdgeRouter(rectangles).routes(
        [{"source": "a", "target": "b"}, {"source": "b", "target": "a"}]
    )
    assert routes[0][0][0] == 236 and routes[0][-1][0] == 352
    assert routes[1][0][0] == 352 and routes[1][-1][0] == 236
    assert routes[0] != routes[1][::-1]
    assert routes[0][0] != routes[1][-1]


def test_routes_avoid_intervening_cards_including_a_staggered_barrier():
    rectangles = {
        "a": (0, 0, 236, 84),
        "b": (1000, 0, 236, 84),
        "barrier1": (300, -100, 160, 200),
        "barrier2": (550, -20, 160, 200),
        "barrier3": (780, -100, 100, 210),
    }
    router = EdgeRouter(rectangles)
    points = router.routes([{"source": "a", "target": "b"}])[0]
    # Exit/entry segments cross only their own clearance margins.
    assert all(router.clear(a, b) for a, b in zip(points[1:-2], points[2:-1], strict=True))
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:], strict=False))
    assert min(p[1] for p in points) < -100 or max(p[1] for p in points) > 180


def test_cyclic_cross_references_are_not_used_to_layout_the_discovery_tree():
    graph = snapshot(
        [
            {"source": "a", "target": "b", "layout": True},
            {"source": "b", "target": "a", "layout": False},
            {"source": "b", "target": "c", "layout": True},
        ],
        view="page-relations",
    )
    positions = layout_nodes(graph)
    assert len(positions) == 3
    assert positions["a"][0] < positions["b"][0] < positions["c"][0]
