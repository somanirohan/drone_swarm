"""Any-angle flight path planning for the operations swarm (horizontal plan + altitude profile).

Why this exists: the previous planner ran 8-connected A* on a fixed 35 m grid and stitched
``[drone xy] + grid cells + [target xy]``. The first grid cell is the drone's *rounded* cell (up to
~25 m behind it), the last is the target's rounded cell (up to ~25 m past it), and the cells in
between staircase in 45-degree steps. Drones therefore snapped back at the start, zig-zagged, and
overshot then turned back round the target. Altitude used the tallest building on the whole map.

This planner:

1. flies straight when the segment is clear of every no-fly polygon (line of sight);
2. otherwise searches a visibility graph whose nodes are the convex corners of the no-fly
   polygons pushed outward by a margin; the result is the shortest any-angle path (what
   grid A* + string pulling approximates); a coarse-grid A* + string pulling is the fallback;
3. starts at the drone's current position (no snap-back) and ends exactly at the requested
   point, or at a stand-off hover point on the side of the target facing the approach;
4. picks transit altitude from the obstacles along *this* path (corridor) rather than the
   world maximum, and an observation altitude from obstacles round the hover point;
5. caches horizontal plans (LRU) and indexes buildings/polygons spatially, so it scales to
   ~14 km regions.

Buildings are overflown (altitude), never routed around; no-fly zones are routed around.
"""
from __future__ import annotations

import heapq
import math
from collections import OrderedDict

CLEARANCE_M = 12.0      # vertical clearance above obstacles along the path
CORRIDOR_M = 15.0       # half-width of the corridor whose buildings set transit altitude
MARGIN_M = 12.0         # horizontal clearance of visibility-graph corners from no-fly polygons
OBSERVE_ABOVE_M = 30.0  # observation height above the target point
CELL_M = 100.0          # spatial-hash cell for buildings


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_intersect(p1, p2, q1, q2):
    d1 = _cross(q1, q2, p1); d2 = _cross(q1, q2, p2); d3 = _cross(p1, p2, q1); d4 = _cross(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 and d2 and d3 and d4:
        return True
    def on(a, b, c):  # c collinear with a-b and within its box
        return min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9 and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9
    return (d1 == 0 and on(q1, q2, p1)) or (d2 == 0 and on(q1, q2, p2)) or (d3 == 0 and on(p1, p2, q1)) or (d4 == 0 and on(p1, p2, q2))


def point_in_polygon(p, polygon):
    inside = False
    j = len(polygon) - 1
    for i, q in enumerate(polygon):
        r = polygon[j]
        if (q[1] > p[1]) != (r[1] > p[1]) and p[0] < (r[0] - q[0]) * (p[1] - q[1]) / (r[1] - q[1]) + q[0]:
            inside = not inside
        j = i
    return inside


def _seg_point_distance(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(a[0] + t * dx - p[0], a[1] + t * dy - p[1])


def _seg_box_distance(a, b, box):
    """Distance between segment a-b and an axis-aligned box (0 when they touch)."""
    x0, y0, x1, y1 = box
    if x0 <= a[0] <= x1 and y0 <= a[1] <= y1 or x0 <= b[0] <= x1 and y0 <= b[1] <= y1:
        return 0.0
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    for i in range(4):
        if _segments_intersect(a, b, corners[i], corners[(i + 1) % 4]):
            return 0.0
    return min(min(_seg_point_distance(a, b, c) for c in corners),
               min(_seg_point_distance(corners[i], corners[(i + 1) % 4], q) for i in range(4) for q in (a, b)))


def path_length(points):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


class _Polygon:
    __slots__ = ("points", "box", "edges")

    def __init__(self, points):
        self.points = [(float(p[0]), float(p[1])) for p in points]
        xs = [p[0] for p in self.points]; ys = [p[1] for p in self.points]
        self.box = (min(xs), min(ys), max(xs), max(ys))
        self.edges = list(zip(self.points, self.points[1:] + self.points[:1]))

    def blocks(self, a, b):
        x0, y0, x1, y1 = self.box
        if max(a[0], b[0]) < x0 or min(a[0], b[0]) > x1 or max(a[1], b[1]) < y0 or min(a[1], b[1]) > y1:
            return False
        if point_in_polygon(a, self.points) or point_in_polygon(b, self.points):
            return True
        if point_in_polygon(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), self.points):
            return True
        return any(_segments_intersect(a, b, p, q) for p, q in self.edges)

    def contains(self, p):
        x0, y0, x1, y1 = self.box
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1 and point_in_polygon(p, self.points)

    def corners(self, margin):
        """Convex corners pushed outward along the bisector (reflex corners never lie on shortest paths)."""
        pts = self.points
        n = len(pts)
        area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
        orientation = 1.0 if area > 0 else -1.0  # +1 counter-clockwise
        out = []
        for i in range(n):
            prev, cur, nxt = pts[i - 1], pts[i], pts[(i + 1) % n]
            turn = _cross(prev, cur, nxt) * orientation
            if turn <= 0:  # reflex or collinear
                continue
            e1 = (cur[0] - prev[0], cur[1] - prev[1]); e2 = (nxt[0] - cur[0], nxt[1] - cur[1])
            l1 = math.hypot(*e1) or 1.0; l2 = math.hypot(*e2) or 1.0
            # Outward normals of both edges (right-hand for CCW polygons).
            n1 = (e1[1] / l1 * orientation, -e1[0] / l1 * orientation); n2 = (e2[1] / l2 * orientation, -e2[0] / l2 * orientation)
            bx, by = n1[0] + n2[0], n1[1] + n2[1]
            bl = math.hypot(bx, by) or 1.0
            cos_half = max(0.25, (bx * n1[0] + by * n1[1]) / bl)
            d = margin / cos_half
            out.append((cur[0] + bx / bl * d, cur[1] + by / bl * d))
        return out


def no_fly_polygons(world):
    """No-fly zones of a world as polygons (circles become 24-gons of radius + 8 m)."""
    polygons = []
    for zone in world.get("no_fly", world.get("no_fly_zones", [])) or []:
        if isinstance(zone, dict):
            polygon = zone.get("polygon", zone.get("footprint", []))
            if not polygon and "center" in zone:
                c = zone["center"]; r = float(zone.get("radius", 30)) + 8
                polygon = [[c[0] + r * math.cos(i * math.tau / 24), c[1] + r * math.sin(i * math.tau / 24)] for i in range(24)]
        else:
            polygon = zone
        if polygon and len(polygon) > 2:
            if list(polygon[0]) == list(polygon[-1]):
                polygon = polygon[:-1]
            polygons.append(polygon)
    return polygons


class FlightPlanner:
    """World-static planner. ``horizontal(a, b)`` -> xy waypoints a..b avoiding no-fly polygons, or None."""

    def __init__(self, world, cache_size=4096):
        self.bounds = list(world.get("bounds", [-600, -600, 600, 600]))
        self.polygons = [_Polygon(p) for p in no_fly_polygons(world)]
        self._buildings = {}
        self.max_building = 0.0
        for building in world.get("buildings", []) or []:
            fp = building.get("footprint", [])
            if len(fp) < 3:
                continue
            xs = [q[0] for q in fp]; ys = [q[1] for q in fp]
            box = (min(xs), min(ys), max(xs), max(ys)); height = float(building.get("height", 15))
            self.max_building = max(self.max_building, height)
            for ix in range(int(math.floor(box[0] / CELL_M)), int(math.floor(box[2] / CELL_M)) + 1):
                for iy in range(int(math.floor(box[1] / CELL_M)), int(math.floor(box[3] / CELL_M)) + 1):
                    self._buildings.setdefault((ix, iy), []).append((box, height))
        self._nodes = None
        self._node_edges = None
        self._cache = OrderedDict()
        self._cache_size = cache_size
        self.stats = {"queries": 0, "cache_hits": 0, "direct": 0, "visibility": 0, "grid": 0, "failed": 0}

    # ---- geometry queries ----
    def blocked(self, a, b):
        return any(polygon.blocks(a, b) for polygon in self.polygons)

    def restricted(self, p):
        return any(polygon.contains(p) for polygon in self.polygons)

    def in_bounds(self, p):
        b = self.bounds
        return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]

    def obstacle_height(self, a, b, corridor=CORRIDOR_M):
        """Tallest building whose footprint box comes within ``corridor`` of segment a-b."""
        if not self._buildings:
            return 0.0
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, int(length / (CELL_M / 2)))
        seen = set(); best = 0.0
        for i in range(steps + 1):
            x = a[0] + (b[0] - a[0]) * i / steps; y = a[1] + (b[1] - a[1]) * i / steps
            for ix in range(int(math.floor((x - corridor) / CELL_M)), int(math.floor((x + corridor) / CELL_M)) + 1):
                for iy in range(int(math.floor((y - corridor) / CELL_M)), int(math.floor((y + corridor) / CELL_M)) + 1):
                    if (ix, iy) in seen:
                        continue
                    seen.add((ix, iy))
                    for box, height in self._buildings.get((ix, iy), ()):
                        if height > best and _seg_box_distance(a, b, box) <= corridor:
                            best = height
        return best

    def path_obstacle_height(self, points, corridor=CORRIDOR_M):
        return max((self.obstacle_height(a, b, corridor) for a, b in zip(points, points[1:])), default=0.0)

    # ---- horizontal search ----
    def _graph(self):
        if self._nodes is None:
            nodes = []
            for polygon in self.polygons:
                for corner in polygon.corners(MARGIN_M):
                    if self.in_bounds(corner) and not self.restricted(corner):
                        nodes.append(corner)
            self._nodes = nodes
            self._node_edges = [dict() for _ in nodes]
            for i in range(len(nodes)):
                for j in range(i + 1, len(nodes)):
                    if not self.blocked(nodes[i], nodes[j]):
                        d = math.hypot(nodes[i][0] - nodes[j][0], nodes[i][1] - nodes[j][1])
                        self._node_edges[i][j] = d; self._node_edges[j][i] = d
        return self._nodes, self._node_edges

    def _visibility_path(self, a, b):
        nodes, edges = self._graph()
        if not nodes:
            return None
        start_links = {i: math.hypot(n[0] - a[0], n[1] - a[1]) for i, n in enumerate(nodes) if not self.blocked(a, n)}
        goal_links = {i: math.hypot(n[0] - b[0], n[1] - b[1]) for i, n in enumerate(nodes) if not self.blocked(n, b)}
        if not start_links or not goal_links:
            return None
        h = lambda i: math.hypot(nodes[i][0] - b[0], nodes[i][1] - b[1])
        queue = [(d + h(i), d, i) for i, d in start_links.items()]
        heapq.heapify(queue)
        cost = dict(start_links); previous = {}; best = None
        while queue:
            f, g, i = heapq.heappop(queue)
            if g > cost.get(i, float("inf")) + 1e-9:
                continue
            if best is not None and f >= best[0]:
                break
            if i in goal_links and (best is None or g + goal_links[i] < best[0]):
                best = (g + goal_links[i], i)
            for j, d in edges[i].items():
                value = g + d
                if value < cost.get(j, float("inf")) - 1e-9:
                    cost[j] = value; previous[j] = i
                    heapq.heappush(queue, (value + h(j), value, j))
        if best is None:
            return None
        chain = [best[1]]
        while chain[-1] in previous:
            chain.append(previous[chain[-1]])
        chain.reverse()
        return [tuple(a)] + [nodes[i] for i in chain] + [tuple(b)]

    def _grid_path(self, a, b):
        """Fallback: 8-connected A* on a coarse grid from a to b, then string-pulled with line of sight."""
        bx0, by0, bx1, by1 = self.bounds
        spacing = max(35.0, max(bx1 - bx0, by1 - by0) / 160.0)
        nx = int((bx1 - bx0) / spacing); ny = int((by1 - by0) / spacing)
        point = lambda c: (bx0 + c[0] * spacing, by0 + c[1] * spacing)
        cell = lambda p: (max(0, min(nx, round((p[0] - bx0) / spacing))), max(0, min(ny, round((p[1] - by0) / spacing))))
        free = {}
        def ok(c):
            if c not in free:
                free[c] = not self.restricted(point(c))
            return free[c]
        source, dest = cell(a), cell(b)
        queue = [(0.0, 0.0, source)]; cost = {source: 0.0}; previous = {}
        found = False; expansions = 0
        while queue and expansions < 60000:
            _, g, c = heapq.heappop(queue); expansions += 1
            if g > cost[c] + 1e-9:
                continue
            if c == dest:
                found = True; break
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                n = (c[0] + dx, c[1] + dy)
                if not (0 <= n[0] <= nx and 0 <= n[1] <= ny) or not ok(n):
                    continue
                value = g + math.hypot(dx, dy) * spacing
                if value < cost.get(n, float("inf")):
                    cost[n] = value; previous[n] = c
                    p = point(n)
                    heapq.heappush(queue, (value + math.hypot(p[0] - b[0], p[1] - b[1]), value, n))
        if not found:
            return None
        cells = [dest]
        while cells[-1] in previous:
            cells.append(previous[cells[-1]])
        cells.reverse()
        raw = [tuple(a)] + [point(c) for c in cells[1:-1]] + [tuple(b)]
        return self.string_pull(raw)

    def string_pull(self, points):
        """Drop every waypoint that the previous kept waypoint can see past (any-angle smoothing)."""
        if len(points) <= 2:
            return list(points)
        out = [points[0]]; i = 0
        while i < len(points) - 1:
            j = len(points) - 1
            while j > i + 1 and self.blocked(points[i], points[j]):
                j -= 1
            if self.blocked(points[i], points[j]):
                return None  # even adjacent points are blocked
            out.append(points[j]); i = j
        return out

    def horizontal(self, a, b):
        """Shortest any-angle xy route a..b avoiding no-fly polygons (list of tuples), or None."""
        self.stats["queries"] += 1
        a = (round(float(a[0]), 2), round(float(a[1]), 2)); b = (round(float(b[0]), 2), round(float(b[1]), 2))
        key = (a, b)
        if key in self._cache:
            self._cache.move_to_end(key); self.stats["cache_hits"] += 1
            result = self._cache[key]
            return None if result is None else list(result)
        if self.restricted(a) or self.restricted(b):
            result = None
        elif not self.blocked(a, b):
            result = [a, b]; self.stats["direct"] += 1
        else:
            result = self._visibility_path(a, b)
            if result is not None:
                self.stats["visibility"] += 1
            else:
                result = self._grid_path(a, b)
                self.stats["grid" if result else "failed"] += 1
        self._cache[key] = None if result is None else tuple(result)
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return None if result is None else list(result)

    # ---- full 3-D route ----
    def route(self, start, goal, cruise, lane=0.0, standoff=0.0, observe=False):
        """3-D waypoints (excluding the current position) from ``start`` [x,y,z] to ``goal`` [x,y,z].

        Transit altitude = max(cruise, obstacles along this path + clearance, goal z + clearance) + lane.
        With ``standoff`` > 0 the route ends at a hover point that distance from the goal, on the side
        facing the final approach leg (no overflight of the target, no loop round it). With ``observe``
        the final approach descends to an observation altitude derived from obstacles near the hover
        point and the goal height. Returns (waypoints, info) or (None, reason).
        """
        sx, sy, sz = float(start[0]), float(start[1]), float(start[2]) if len(start) > 2 else 0.0
        gx, gy = float(goal[0]), float(goal[1]); gz = float(goal[2]) if len(goal) > 2 else 0.0
        if not self.in_bounds((gx, gy)):
            return None, "target outside operating area"
        path = self.horizontal((sx, sy), (gx, gy))
        if path is None:
            return None, "no route clear of no-fly zones"
        end = (gx, gy)
        if standoff > 0:
            last = path[-2]
            dx, dy = last[0] - gx, last[1] - gy
            distance = math.hypot(dx, dy)
            if distance > 1e-6:
                offset = min(standoff, distance * 0.5)
                candidate = (gx + dx / distance * offset, gy + dy / distance * offset)
                if not self.restricted(candidate):
                    end = (round(candidate[0], 2), round(candidate[1], 2))
            path = path[:-1] + [end]
            if len(path) >= 2 and math.hypot(path[-1][0] - path[-2][0], path[-1][1] - path[-2][1]) < 1e-6:
                path = path[:-1] if len(path) > 2 else path
        obstacles = self.path_obstacle_height(path)
        transit = max(float(cruise), obstacles + CLEARANCE_M, gz + CLEARANCE_M) + float(lane)
        waypoints = []
        if sz < 5.0:
            waypoints.append([sx, sy, transit])  # vertical take-off at the current position
        for p in path[1:-1]:
            waypoints.append([p[0], p[1], transit])
        final_alt = transit
        if observe:
            near = self.obstacle_height(end, (gx, gy), corridor=25.0)
            final_alt = min(transit, max(gz + OBSERVE_ABOVE_M, near + CLEARANCE_M + 8.0, 35.0) + float(lane) * 0.5)
            leg_start = path[-2] if len(path) >= 2 else (sx, sy)
            leg = math.hypot(end[0] - leg_start[0], end[1] - leg_start[1])
            drop = transit - final_alt
            # Begin a gentle (1:3) descent this far before the hover point, staying at transit before it.
            descent = min(leg, drop * 3.0)
            if drop > 1.0 and leg - descent > 5.0:
                f = (leg - descent) / leg
                waypoints.append([leg_start[0] + (end[0] - leg_start[0]) * f, leg_start[1] + (end[1] - leg_start[1]) * f, transit])
        waypoints.append([end[0], end[1], final_alt])
        info = {"length_m": path_length([(sx, sy)] + [w[:2] for w in waypoints]), "direct_m": math.hypot(gx - sx, gy - sy),
                "transit_alt_m": round(transit, 1), "obstacle_m": round(obstacles, 1), "hover": list(end), "legs": len(path) - 1}
        return waypoints, info
