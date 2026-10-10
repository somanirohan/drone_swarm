"""Civilian traffic for visual realism: cars, autos, bikes and buses driving the road graph.

Deterministic (own seeded RNG), cheap (one random-walk edge at a time, O(vehicles) per step),
and purely visual: responders are never blocked by it. Vehicles avoid closed (flooded/damaged)
edges, slow down near open incidents and stop close to them.

Snapshot rows: {id, kind, position[x,y,0], heading (radians, atan2(dy,dx), x east / y north), speed_mps}.
"""
from __future__ import annotations

import math
import random

KINDS = (("car", 0.5, 11.0), ("auto", 0.22, 8.0), ("bike", 0.2, 10.0), ("bus", 0.08, 8.5))  # kind, share, cruise m/s
SLOW_RADIUS_M = 120.0
STOP_RADIUS_M = 35.0


class Traffic:
    def __init__(self, sim, count=60, seed=0):
        self.sim = sim
        self.rng = random.Random(f"traffic:{seed}")
        self.vehicles = []
        graph = sim._road_graph
        nodes = sorted(n for n in graph if self._open_neighbors(n))
        if not nodes or count <= 0:
            return
        for i in range(min(int(count), 80)):
            roll, acc = self.rng.random(), 0.0
            for kind, share, cruise in KINDS:
                acc += share
                if roll <= acc:
                    break
            start = self.rng.choice(nodes)
            nxt = self.rng.choice(self._open_neighbors(start))
            self.vehicles.append({"id": f"V{i + 1:02d}", "kind": kind, "cruise": cruise * (0.85 + 0.3 * self.rng.random()),
                                  "a": start, "b": nxt, "s": self.rng.random() * graph[start][nxt], "speed": 0.0, "heading": 0.0})

    def _open_neighbors(self, node):
        blocked = self.sim._blocked_edges
        return sorted(n for n in self.sim._road_graph.get(node, {}) if (node, n) not in blocked and (n, node) not in blocked)

    def step(self, dt):
        if not self.vehicles:
            return
        hazards = [i["position"] for i in self.sim.incidents if i["status"] != "resolved"]
        graph = self.sim._road_graph
        for v in self.vehicles:
            a, b = v["a"], v["b"]
            length = graph.get(a, {}).get(b)
            if length is None or (a, b) in self.sim._blocked_edges or (b, a) in self.sim._blocked_edges:
                options = self._open_neighbors(a) or self._open_neighbors(b)
                if not options:
                    v["speed"] = 0.0
                    continue
                v["b"] = options[0] if options[0] != a else options[-1]; v["s"] = 0.0
                continue
            x, y = self._xy(v, length)
            near = min((math.hypot(x - p[0], y - p[1]) for p in hazards), default=1e9)
            target = 0.0 if near < STOP_RADIUS_M else v["cruise"] * (0.35 if near < SLOW_RADIUS_M else 1.0)
            v["speed"] += max(-4.0 * dt, min(2.0 * dt, target - v["speed"]))
            v["s"] += v["speed"] * dt
            while v["s"] >= length:
                v["s"] -= length
                options = [n for n in self._open_neighbors(b) if n != a] or self._open_neighbors(b)
                if not options:
                    v["s"] = length; v["speed"] = 0.0
                    break
                # Prefer going straight on (smallest turn), with some random turning.
                heading = math.atan2(b[1] - a[1], b[0] - a[0])
                options.sort(key=lambda n: (abs((math.atan2(n[1] - b[1], n[0] - b[0]) - heading + math.pi) % math.tau - math.pi), n))
                pick = options[0] if self.rng.random() < 0.6 or len(options) == 1 else self.rng.choice(options[1:])
                a, b = b, pick
                v["a"], v["b"] = a, b
                length = graph[a][b]
            v["heading"] = math.atan2(b[1] - a[1], b[0] - a[0])

    @staticmethod
    def _xy(v, length):
        a, b = v["a"], v["b"]
        f = min(1.0, v["s"] / length) if length else 0.0
        return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f

    def snapshot(self):
        graph = self.sim._road_graph
        out = []
        for v in self.vehicles:
            length = graph.get(v["a"], {}).get(v["b"]) or 1.0
            x, y = self._xy(v, length)
            out.append({"id": v["id"], "kind": v["kind"], "position": [round(x, 2), round(y, 2), 0.0],
                        "heading": round(v["heading"], 3), "speed_mps": round(v["speed"], 2)})
        return out
