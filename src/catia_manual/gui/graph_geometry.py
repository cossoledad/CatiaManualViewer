"""Deterministic viewer layouts and obstacle-aware routes, without Qt dependencies."""

import heapq
import math
from bisect import bisect_left
from collections import defaultdict

WIDTH, HEIGHT, COLUMN, ROW = 236, 84, 352, 116


def valid_position(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) in (int, float) and math.isfinite(n) and abs(n) < 1000000 for n in value)
    )


def layout_nodes(snapshot, saved=None):
    """Tidy discovery trees; saved cards stay put when a branch is expanded."""
    nodes = {n["id"]: n for n in snapshot.nodes}
    parents, children = {}, defaultdict(list)
    for edge in snapshot.edges:
        a = edge.get("layout_source", edge["source"])
        b = edge.get("layout_target", edge["target"])
        if edge.get("layout", True) and a in nodes and b in nodes and b != snapshot.root:
            if b not in parents:
                parents[b] = a
                children[a].append(b)
    relation = snapshot.view == "page-relations"
    if relation:
        for group in children.values():
            group.sort(key=lambda key: (nodes[key]["kind"], nodes[key].get("role", "")))
    spans, visiting = {}, set()

    def span(key):
        if key in visiting:
            return ROW
        if key not in spans:
            visiting.add(key)
            spans[key] = max(ROW, sum(span(child) for child in children[key]))
            visiting.remove(key)
        return spans[key]

    automatic = {snapshot.root: [0, 0]}

    def place(key, group, sign):
        top = automatic[key][1] - sum(span(child) for child in group) / 2
        for child in group:
            if child in automatic:
                continue
            automatic[child] = [automatic[key][0] + sign * COLUMN, top + span(child) / 2]
            top += span(child)
            place(child, children[child], sign)

    if relation:
        left = [k for k in children[snapshot.root] if nodes[k]["kind"] in {"command", "sample"}]
        right = [k for k in children[snapshot.root] if k not in left]
        place(snapshot.root, left, -1)
        place(snapshot.root, right, 1)
    else:
        place(snapshot.root, children[snapshot.root], 1)
    positions = {k: list(v) for k, v in (saved or {}).items() if valid_position(v)}
    occupied = [v for k, v in positions.items() if k in nodes]
    for key, candidate in automatic.items():
        if key in positions:
            continue
        x, y = candidate
        parent = parents.get(key)
        if parent in positions and parent in automatic:
            x += positions[parent][0] - automatic[parent][0]
            y += positions[parent][1] - automatic[parent][1]
        base, step = y, 0
        while any(abs(x - p[0]) < WIDTH + 24 and abs(y - p[1]) < HEIGHT + 24 for p in occupied):
            step += 1
            y = base + ((step + 1) // 2) * ROW * (1 if step % 2 else -1)
        positions[key] = [x, y]
        occupied.append([x, y])
    visible = {k: v for k, v in positions.items() if k in nodes}
    hidden = [(k, v) for k, v in positions.items() if k not in nodes]
    return {**visible, **dict(hidden[: max(0, 200 - len(visible))])}


def compact(points):
    result = []
    for point in points:
        if result and point == result[-1]:
            continue
        while len(result) > 1 and (
            result[-2][0] == result[-1][0] == point[0] or result[-2][1] == result[-1][1] == point[1]
        ):
            result.pop()
        result.append(point)
    return result


class EdgeRouter:
    """Orthogonal ports, separate reciprocal lanes, and a bounded grid-search fallback."""

    def __init__(self, rectangles):
        self.rectangles = rectangles
        self.obstacles = [
            (x - 8, y - 8, x + w + 8, y + h + 8) for x, y, w, h in rectangles.values()
        ]
        self.occupied = []
        self.search_remaining = 8192

    def clear(self, a, b):
        if a[1] == b[1]:
            lo, hi = sorted((a[0], b[0]))
            return not any(
                top < a[1] < bottom and lo < right and hi > left
                for left, top, right, bottom in self.obstacles
            )
        lo, hi = sorted((a[1], b[1]))
        return not any(
            left < a[0] < right and lo < bottom and hi > top
            for left, top, right, bottom in self.obstacles
        )

    def crossings(self, points):
        cost = 0
        for a, b in zip(points, points[1:], strict=False):
            for c, d in self.occupied[-240:]:
                if a[1] == b[1] and c[0] == d[0]:
                    cost += min(a[0], b[0]) < c[0] < max(a[0], b[0]) and min(c[1], d[1]) < a[
                        1
                    ] < max(c[1], d[1])
                elif a[0] == b[0] and c[1] == d[1]:
                    cost += min(c[0], d[0]) < a[0] < max(c[0], d[0]) and min(a[1], b[1]) < c[
                        1
                    ] < max(a[1], b[1])
        return cost

    def route(self, start, end):
        ax, ay = start
        bx, by = end
        candidates = []
        for x in ((ax + bx) / 2, ax, bx, min(ax, bx) - 24, max(ax, bx) + 24):
            candidates.append(compact([start, (x, ay), (x, by), end]))
        for y in ((ay + by) / 2, ay, by, min(ay, by) - 24, max(ay, by) + 24):
            candidates.append(compact([start, (ax, y), (bx, y), end]))
        viable = [
            p for p in candidates if all(self.clear(a, b) for a, b in zip(p, p[1:], strict=False))
        ]
        if viable:

            def score(points):
                length = sum(
                    abs(a[0] - b[0]) + abs(a[1] - b[1])
                    for a, b in zip(points, points[1:], strict=False)
                )
                return length + 14 * len(points) + 36 * self.crossings(points)

            return min(viable, key=score)
        return self.search(start, end) or candidates[0]

    def search(self, start, end):
        xs = sorted({start[0], end[0]} | {v for r in self.obstacles for v in (r[0], r[2])})
        ys = sorted({start[1], end[1]} | {v for r in self.obstacles for v in (r[1], r[3])})
        origin = (bisect_left(xs, start[0]), bisect_left(ys, start[1]), 0)
        goal = (bisect_left(xs, end[0]), bisect_left(ys, end[1]))
        heap, costs, previous = [(0, origin)], {origin: 0}, {}
        closed, visibility = set(), {}
        # A single work budget covers the whole projection, including heavily
        # crossed or manually overlapping cards. A drag release must stay bounded.
        while heap and self.search_remaining:
            _, current = heapq.heappop(heap)
            if current in closed:
                continue
            closed.add(current)
            self.search_remaining -= 1
            x, y, direction = current
            if (x, y) == goal:
                result = []
                while current in previous:
                    result.append((xs[current[0]], ys[current[1]]))
                    current = previous[current]
                return compact([start] + result[::-1])
            for dx, dy, axis in ((-1, 0, 1), (1, 0, 1), (0, -1, 2), (0, 1, 2)):
                nx, ny = x + dx, y + dy
                if not 0 <= nx < len(xs) or not 0 <= ny < len(ys):
                    continue
                a, b = (xs[x], ys[y]), (xs[nx], ys[ny])
                segment = tuple(sorted((a, b)))
                if segment not in visibility:
                    visibility[segment] = self.clear(a, b)
                if not visibility[segment]:
                    continue
                next_key = (nx, ny, axis)
                cost = (
                    costs[current]
                    + abs(a[0] - b[0])
                    + abs(a[1] - b[1])
                    + (20 if direction and direction != axis else 0)
                )
                if cost >= costs.get(next_key, float("inf")):
                    continue
                costs[next_key], previous[next_key] = cost, current
                estimate = cost + abs(b[0] - end[0]) + abs(b[1] - end[1])
                heapq.heappush(heap, (estimate, next_key))
        return None

    def routes(self, edges):
        ports, groups = {}, defaultdict(list)
        for i, edge in enumerate(edges):
            for source, target in (("source", "target"), ("target", "source")):
                key, other = edge[source], edge[target]
                if key not in self.rectangles or other not in self.rectangles:
                    continue
                x, y, w, h = self.rectangles[key]
                ox, oy, ow, oh = self.rectangles[other]
                dx, dy = ox + ow / 2 - x - w / 2, oy + oh / 2 - y - h / 2
                side = (
                    ("right" if dx >= 0 else "left")
                    if abs(dx) >= w
                    else ("bottom" if dy >= 0 else "top")
                )
                groups[key, side].append(
                    (oy + oh / 2 if side in {"left", "right"} else ox + ow / 2, i, source)
                )
        for (key, side), entries in groups.items():
            x, y, w, h = self.rectangles[key]
            for rank, (_, i, endpoint) in enumerate(sorted(entries)):
                fraction = (rank + 1) / (len(entries) + 1)
                if side in {"left", "right"}:
                    port = (x if side == "left" else x + w, y + 14 + (h - 28) * fraction)
                    outside = (port[0] + (-16 if side == "left" else 16), port[1])
                else:
                    port = (x + 20 + (w - 40) * fraction, y if side == "top" else y + h)
                    outside = (port[0], port[1] + (-16 if side == "top" else 16))
                ports[i, endpoint] = (port, outside)
        result = []
        for i, _ in enumerate(edges):
            if (i, "source") not in ports or (i, "target") not in ports:
                result.append([])
                continue
            start, a = ports[i, "source"]
            end, b = ports[i, "target"]
            points = compact([start] + self.route(a, b) + [end])
            self.occupied.extend(zip(points[1:-2], points[2:-1], strict=False))
            result.append(points)
        return result
