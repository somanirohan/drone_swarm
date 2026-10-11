"""Simulated drone mesh radio (simulation only).

Every active aircraft carries one radio. Two radios hear each other when they are within ``radio_range_m``;
the link quality is 1 up to 80 % of the range and fades linearly to 0.5 at the edge. A transmission reaches each neighbour with
probability ``quality * (1 - packet_loss)`` after ``latency_s`` (plus a small jitter). Partitions are not
scripted: they emerge from geometry (who is within range of whom).

The ground side (operations base, drone docks) is the *command link*. A drone within ``gateway_range_m`` of
any ground station is a **gateway**. A gateway is not a coordinator: it only bridges radio traffic between the
command link and the mesh (mission orders in, reports out). Any drone can be a gateway and gateways change as
aircraft move.

Two delivery services:

* ``broadcast(src, payload, ttl=1)``: one-hop broadcast to the current neighbours. With ``ttl > 1`` the radio
  layer floods the message (each node forwards an unseen message once with ``ttl - 1``), like a simple mesh
  routing protocol.
* ``from_command(payload)``: the command link transmits to every gateway (same loss model).
  ``to_command(src, payload)`` floods a message that is delivered to the command side as soon as it reaches a
  gateway (multi-hop report path).

The mesh holds no mission state and makes no decisions: it is the radio physics the agents talk through.
"""
from __future__ import annotations

import math
import random
from collections import deque

DISTRICT_RANGE_M = 600.0
REGION_RANGE_M = 1500.0


def default_range(bounds):
    """600 m for a district-sized map, 1.5 km for a region."""
    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1]) if bounds else 1200
    return DISTRICT_RANGE_M if span <= 3000 else REGION_RANGE_M


class Mesh:
    def __init__(self, seed=0, radio_range_m=DISTRICT_RANGE_M, latency_s=0.1, packet_loss=0.0, gateway_range_m=None,
                 sample_size=40):
        self.rng = random.Random(f"{seed}:mesh")
        self.configure(radio_range_m=radio_range_m, latency_s=latency_s, packet_loss=packet_loss, gateway_range_m=gateway_range_m)
        self.positions = {}          # node id -> [x, y, z]
        self.stations = []           # ground station positions (command link)
        self.neighbours = {}         # node id -> {neighbour id: quality}
        self.gateways = set()
        self.components = []         # list of sets (radio partitions among drones)
        self.hops_to_gateway = {}    # node id -> hops (None if no path)
        self.queue = []              # (deliver_t, seq, dst, message)
        self.inboxes = {}
        self.command_inbox = []
        self._seen = {}              # node id -> set of flood ids already forwarded
        self._seq = 0
        self._msg_seq = 0
        self.stats = {"messages_sent": 0, "transmissions": 0, "delivered": 0, "dropped": 0, "by_kind": {}}
        self.sample = deque(maxlen=sample_size)
        self.topology_t = None

    def configure(self, radio_range_m=None, latency_s=None, packet_loss=None, gateway_range_m=None):
        if radio_range_m is not None:
            self.radio_range_m = float(max(50.0, min(20000.0, radio_range_m)))
        if latency_s is not None:
            self.latency_s = float(max(0.0, min(5.0, latency_s)))
        if packet_loss is not None:
            self.packet_loss = float(max(0.0, min(0.95, packet_loss)))
        if gateway_range_m is not None or not hasattr(self, "gateway_range_m"):
            self.gateway_range_m = float(gateway_range_m) if gateway_range_m else None

    @property
    def gateway_range(self):
        return self.gateway_range_m or self.radio_range_m

    # ------------------------------------------------------------------ topology (geometry -> links)
    def quality(self, a, b):
        d = math.hypot(a[0] - b[0], a[1] - b[1])
        if d > self.radio_range_m:
            return 0.0
        ratio = d / self.radio_range_m
        return 1.0 if ratio <= 0.8 else round(1.0 - 2.5 * (ratio - 0.8), 3)

    def update_topology(self, positions, stations, t=None):
        """``positions``: {id: [x,y,z]} of radios that are on. ``stations``: ground (command link) positions."""
        self.positions = {k: list(v) for k, v in positions.items()}
        self.stations = [list(s) for s in stations]
        ids = sorted(self.positions)
        self.neighbours = {i: {} for i in ids}
        r2 = self.radio_range_m ** 2
        for n, a in enumerate(ids):
            pa = self.positions[a]
            for b in ids[n + 1:]:
                pb = self.positions[b]
                dx, dy = pa[0] - pb[0], pa[1] - pb[1]
                if dx * dx + dy * dy <= r2:
                    q = self.quality(pa, pb)
                    self.neighbours[a][b] = q
                    self.neighbours[b][a] = q
        g2 = self.gateway_range ** 2
        self.gateways = {i for i in ids if any((self.positions[i][0] - s[0]) ** 2 + (self.positions[i][1] - s[1]) ** 2 <= g2 for s in self.stations)}
        # partitions (connected components) and hop distance to the nearest gateway (BFS from all gateways)
        seen, comps = set(), []
        for i in ids:
            if i in seen:
                continue
            comp, stack = set(), [i]
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(y for y in self.neighbours[x] if y not in comp)
            seen |= comp
            comps.append(comp)
        self.components = comps
        hops = {g: 0 for g in self.gateways}
        frontier = sorted(self.gateways)
        while frontier:
            nxt = []
            for x in frontier:
                for y in self.neighbours[x]:
                    if y not in hops:
                        hops[y] = hops[x] + 1
                        nxt.append(y)
            frontier = nxt
        self.hops_to_gateway = {i: hops.get(i) for i in ids}
        self.topology_t = t

    def connected_to_command(self, node):
        return self.hops_to_gateway.get(node) is not None

    # ------------------------------------------------------------------ transmission
    def _count(self, kind):
        self.stats["by_kind"][kind] = self.stats["by_kind"].get(kind, 0) + 1

    def _transmit(self, src, dst, quality, message, t):
        self.stats["transmissions"] += 1
        if self.rng.random() >= quality * (1.0 - self.packet_loss):
            self.stats["dropped"] += 1
            return False
        self._seq += 1
        jitter = self.latency_s * 0.5 * self.rng.random()
        self.queue.append((t + self.latency_s + jitter, self._seq, dst, message))
        return True

    def broadcast(self, src, payload, t, ttl=1, kind="consensus", to=None):
        """One-hop broadcast (ttl=1) or flood (ttl>1). ``to='command'`` marks a message for the command side."""
        if src not in self.neighbours:
            return None
        self._msg_seq += 1
        message = {"mid": f"{src}:{self._msg_seq}", "src": src, "hop_from": src, "t": round(t, 3), "kind": kind, "ttl": int(ttl),
                   "hops": 0, "to": to, "payload": payload}
        self.stats["messages_sent"] += 1
        self._count(kind)
        self._seen.setdefault(src, set()).add(message["mid"])
        if to == "command" and src in self.gateways:
            self._seq += 1
            self.queue.append((t + self.latency_s, self._seq, "command", message))
        for dst, q in sorted(self.neighbours[src].items()):
            self._transmit(src, dst, q, message, t)
        if len(self.sample) < self.sample.maxlen or self._msg_seq % 5 == 0:
            self.sample.append({"t": round(t, 2), "from": src, "kind": kind, "ttl": int(ttl), "to": to or "neighbours",
                                "neighbours": len(self.neighbours[src]), "summary": _summary(payload)})
        return message["mid"]

    def from_command(self, payload, t, kind="mission"):
        """The command link transmits to every gateway (each copy may be lost)."""
        self._msg_seq += 1
        message = {"mid": f"command:{self._msg_seq}", "src": "command", "hop_from": "command", "t": round(t, 3), "kind": kind, "ttl": 1,
                   "hops": 0, "to": None, "payload": payload}
        self.stats["messages_sent"] += 1
        self._count(kind)
        self.sample.append({"t": round(t, 2), "from": "command", "kind": kind, "ttl": 1, "to": "gateways",
                            "neighbours": len(self.gateways), "summary": _summary(payload)})
        delivered = 0
        for g in sorted(self.gateways):
            delivered += self._transmit("command", g, 1.0, message, t)
        return delivered

    def to_command(self, src, payload, t, kind="report", ttl=8):
        return self.broadcast(src, payload, t, ttl=ttl, kind=kind, to="command")

    def deliver(self, t):
        """Deliver every message due by ``t``; flood messages are forwarded once by each node. Returns the
        number of messages placed in inboxes."""
        due = [m for m in self.queue if m[0] <= t]
        if not due:
            return 0
        self.queue = [m for m in self.queue if m[0] > t]
        due.sort(key=lambda m: (m[0], m[1]))
        count = 0
        for _, _, dst, message in due:
            if dst == "command":
                if message["mid"] not in self._seen.setdefault("command", set()):
                    self._seen["command"].add(message["mid"])
                    self.command_inbox.append({**message, "delivered_t": round(t, 3)})
                    self.stats["delivered"] += 1
                continue
            if dst not in self.neighbours:
                self.stats["dropped"] += 1      # radio switched off while in flight
                continue
            seen = self._seen.setdefault(dst, set())
            if message["ttl"] > 1 or message.get("to") == "command":
                if message["mid"] in seen:
                    continue
                seen.add(message["mid"])
                if len(seen) > 4000:
                    seen.clear()
            self.inboxes.setdefault(dst, []).append(message)
            self.stats["delivered"] += 1
            count += 1
            if message.get("to") == "command" and dst in self.gateways:
                self._seq += 1
                self.queue.append((t + self.latency_s, self._seq, "command", {**message, "hops": message["hops"] + 1}))
            elif message["ttl"] > 1:
                forwarded = {**message, "ttl": message["ttl"] - 1, "hops": message["hops"] + 1, "hop_from": dst}
                for nb, q in sorted(self.neighbours[dst].items()):
                    if nb != message["hop_from"]:
                        self._transmit(dst, nb, q, forwarded, t)
        return count

    def drain(self, node):
        return self.inboxes.pop(node, [])

    def drain_command(self):
        out, self.command_inbox = self.command_inbox, []
        return out

    # ------------------------------------------------------------------ views
    def links(self):
        return [[a, b, q] for a in sorted(self.neighbours) for b, q in sorted(self.neighbours[a].items()) if a < b]

    def snapshot(self):
        return {"links": self.links(), "partitions": len(self.components), "gateways": sorted(self.gateways),
                "isolated_from_command": sorted(i for i, h in self.hops_to_gateway.items() if h is None),
                "messages_sent": self.stats["messages_sent"], "transmissions": self.stats["transmissions"],
                "delivered": self.stats["delivered"], "dropped": self.stats["dropped"], "by_kind": dict(self.stats["by_kind"]),
                "radio_range_m": self.radio_range_m, "gateway_range_m": self.gateway_range, "latency_s": self.latency_s,
                "packet_loss": self.packet_loss, "sample": list(self.sample)[-20:], "simulation_only": True}


def _summary(payload):
    if not isinstance(payload, dict):
        return str(payload)[:80]
    parts = []
    if payload.get("bundle") is not None:
        parts.append("bundle " + ",".join(payload["bundle"]) if payload["bundle"] else "bundle empty")
    if payload.get("mission"):
        parts.append(f"mission {payload['mission'].get('id')}")
    if payload.get("report"):
        parts.append(f"report {payload['report'].get('mission_id')}")
    if payload.get("close"):
        parts.append(f"close {payload['close']}")
    return "; ".join(parts)[:120]
