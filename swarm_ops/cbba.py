"""Consensus-Based Bundle Algorithm (CBBA) for one aircraft, plus local sub-task derivation.

Choi, Brunet, How, "Consensus-Based Decentralized Auctions for Robust Task Allocation", IEEE T-RO 2009;
asynchronous handling in the spirit of ACBBA (Johnson, Ponda, Choi, How 2010/2011): every message is processed
on arrival, information is time-stamped, and an agent rebroadcasts when its state changed (plus a heartbeat).

One ``CBBAAgent`` per drone. It holds ONLY its own view:

* ``bundle`` (tasks in the order they were added) and ``path`` (execution order),
* ``y[j]`` the winning bid it believes for task j, ``z[j]`` the winner it believes, ``s[k]`` the time of the
  latest information it has about agent k (direct or relayed),
* the task, mission and done/closed knowledge it has heard (grow-only sets merged on receipt).

Nothing here sees other agents' memory: ``receive`` takes a message, ``build_bundle`` takes the agent's own
scoring function. No component computes a global assignment.

Phase 1 (bundle construction): while the bundle is shorter than ``max_bundle``, compute for every open task the
best marginal score of inserting it anywhere in the path (after any task already being executed); bid on the
task with the largest marginal score that beats the current winning bid (ties: lower agent id).

Phase 2 (consensus): the CBBA update/reset/leave rule table (Choi et al. 2009, Table 1), see ``_rule``; then
every task in the bundle after the first one this agent was outbid on is released (later tasks' bids were
computed on top of it).

Practical extensions (documented in docs/DECENTRALIZED.md):

* a task being executed is *locked*: its bid becomes ``LOCK + bid`` so it is not taken away mid-execution,
  except by another locked claim with a higher bid (the two sides of a healed partition reconcile this way);
* ``peer_timeout``: if nothing (direct or relayed) has been heard from agent m for that long, its tasks are
  reset so others can take them over (crash/lost drone); a later, fresher timestamp of m revives it;
* ``outbid_margin``: hysteresis when bidding against an existing winner, max(margin, 5 % of the winning bid)
  (damps oscillation when scores are not diminishing-marginal-gain);
* commit delay (``committed``): the owner only starts flying to a claim it has held for a short time, so
  tentative bids that consensus overturns within a few message exchanges do not launch aircraft.
"""
from __future__ import annotations

import math

LOCK = 1_000_000.0
NEG = float("-inf")

KIND_PRIORITY = {"assess": 100.0, "supply": 80.0, "relay": 75.0, "handover": 60.0, "overwatch": 50.0, "search": 40.0}
KIND_DURATION = {"assess": 15.0, "supply": 4.0, "relay": 90.0, "handover": 45.0, "overwatch": 90.0, "search": 12.0}
SUBTASK_KINDS = tuple(KIND_PRIORITY)
SUPPLY_ITEMS = ("first_aid", "medical", "food", "water")
SEARCH_CATEGORIES = ("flood", "trapped", "person_stranded", "structural_damage", "fire", "evacuation")


class CBBAAgent:
    def __init__(self, agent_id, max_bundle=2, peer_timeout=10.0, outbid_margin=0.5, eps=1e-6):
        self.id = str(agent_id)
        self.max_bundle = int(max_bundle)
        self.peer_timeout = float(peer_timeout)
        self.outbid_margin = float(outbid_margin)
        self.eps = eps
        self.tasks = {}        # task id -> definition (grow-only)
        self.done = {}         # task id -> time done (grow-only)
        self.missions = {}     # mission id -> definition (grow-only)
        self.closed = {}       # mission id -> time closed (grow-only)
        self.bundle, self.path = [], []
        self.y, self.z, self.s = {}, {}, {}
        self.lost = {}         # agent id -> time it was declared lost
        self.locked = set()
        self.blocked = {}      # task id -> time until which this agent will not bid (it had to abandon it)
        self.claimed_at = {}   # task id -> time this agent added it to its bundle (commit delay before flying)
        self.changed = True
        self.rounds = 0        # messages processed
        self.events = []       # local trace drained by the owner: (kind, task, detail)
        self.last_consensus_t = None

    # ------------------------------------------------------------------ knowledge
    def open_tasks(self):
        return [j for j in sorted(self.tasks) if j not in self.done and self.tasks[j].get("mission_id") not in self.closed]

    def add_mission(self, mission, t):
        mid = mission["id"]
        if mid in self.missions or mid in self.closed:
            return False
        self.missions[mid] = dict(mission)
        self.changed = True
        return True

    def add_tasks(self, defs):
        new = []
        for d in defs:
            if d["id"] not in self.tasks and d.get("mission_id") not in self.closed:
                self.tasks[d["id"]] = dict(d)
                new.append(d["id"])
        if new:
            self.changed = True
        return new

    def close_mission(self, mid, t):
        if mid in self.closed:
            return False
        self.closed[mid] = t
        for j in [j for j in self.bundle if self.tasks.get(j, {}).get("mission_id") == mid]:
            self._drop(j)
        self.changed = True
        return True

    def complete(self, j, t):
        if j not in self.done:
            self.done[j] = t
            self.changed = True
        if j in self.bundle:
            self._drop(j)

    def committed(self, j, t, delay):
        """True once this agent has held its claim on j for ``delay`` seconds (or is executing it)."""
        return j in self.locked or (j in self.bundle and t - self.claimed_at.get(j, t) >= delay)

    def _drop(self, j):
        self.claimed_at.pop(j, None)
        if j in self.bundle:
            self.bundle.remove(j)
        if j in self.path:
            self.path.remove(j)
        self.locked.discard(j)
        self.changed = True

    def _reset(self, j):
        if self.z.get(j) is not None or self.y.get(j, 0.0):
            self.changed = True
        self.y[j], self.z[j] = 0.0, None

    def abandon(self, j, t, cooldown=30.0):
        """Give a task up (no route, energy, payload used): reset it so a peer can take it."""
        self._drop(j)
        if self.z.get(j) == self.id:
            self._reset(j)
        self.blocked[j] = t + cooldown
        self.events.append(("abandon", j, ""))

    def lock(self, j):
        if j in self.bundle and j not in self.locked:
            self.locked.add(j)
            if self.y.get(j, 0.0) < LOCK:
                self.y[j] = LOCK + self.y.get(j, 0.0)
            # executing tasks stay at the head of the path
            self.path.remove(j)
            self.path.insert(len([p for p in self.path if p in self.locked]), j)
            self.changed = True

    # ------------------------------------------------------------------ phase 1: bundle construction
    def build_bundle(self, score_path, t):
        """``score_path(list of task defs) -> float | None`` is the agent's own score of executing that path
        in order (None if infeasible, e.g. energy). Returns the tasks added."""
        added = []
        self.path = [j for j in self.path if j in self.bundle]
        while self.path:   # the current path must stay feasible (energy changes as the aircraft flies)
            if score_path([self.tasks[j] for j in self.path]) is not None:
                break
            victim = next((j for j in reversed(self.path) if j not in self.locked), None)
            if victim is None:
                break
            self.abandon(victim, t, cooldown=10.0)
        base = score_path([self.tasks[j] for j in self.path]) if self.path else 0.0
        base = base or 0.0
        while len(self.bundle) < self.max_bundle:
            first = len([j for j in self.path if j in self.locked])
            best = None
            for j in self.open_tasks():
                if j in self.bundle or self.blocked.get(j, NEG) > t:
                    continue
                cj, pos = NEG, None
                for n in range(first, len(self.path) + 1):
                    cand = self.path[:n] + [j] + self.path[n:]
                    value = score_path([self.tasks[x] for x in cand])
                    if value is None:
                        continue
                    if value - base > cj:
                        cj, pos = value - base, n
                if pos is None or cj <= self.eps:
                    continue
                yj, zj = self.y.get(j, 0.0), self.z.get(j)
                margin = max(self.outbid_margin, 0.05 * yj) if zj not in (None, self.id) else 0.0
                wins = cj > yj + margin + self.eps or (abs(cj - yj) <= self.eps and zj is not None and self.id < zj)
                if not wins:
                    continue
                if best is None or cj > best[0] + self.eps or (abs(cj - best[0]) <= self.eps and j < best[1]):
                    best = (cj, j, pos)
            if best is None:
                break
            cj, j, pos = best
            previous = self.z.get(j)
            self.bundle.append(j)
            self.path.insert(pos, j)
            self.y[j], self.z[j] = cj, self.id
            self.claimed_at[j] = t
            base += cj
            added.append(j)
            self.changed = True
            self.events.append(("takeover" if previous is not None and previous in self.lost else "bid", j, f"{cj:.1f}"))
        return added

    # ------------------------------------------------------------------ phase 2: consensus
    def message(self, t):
        live = set(self.open_tasks())
        return {"from": self.id, "t": round(t, 3),
                "y": {j: round(self.y.get(j, 0.0), 4) for j in live}, "z": {j: self.z.get(j) for j in live},
                "s": {**{k: round(v, 3) for k, v in self.s.items()}, self.id: round(t, 3)},
                "tasks": {j: self.tasks[j] for j in live}, "done": {j: v for j, v in self.done.items() if self.tasks.get(j, {}).get("mission_id") not in self.closed},
                "missions": {m: v for m, v in self.missions.items() if m not in self.closed}, "closed": dict(self.closed),
                "bundle": list(self.bundle)}

    def _better(self, y1, a1, y2, a2):
        if y1 > y2 + self.eps:
            return True
        if abs(y1 - y2) <= self.eps:
            return a1 is not None and (a2 is None or a1 < a2)
        return False

    def _rule(self, k, yk, zk, sk, yi, zi):
        """CBBA conflict-resolution table. Returns 'update', 'reset' or 'leave' (receiver i, sender k)."""
        i = self.id
        newer = lambda m: sk.get(m, NEG) > self.s.get(m, NEG)   # noqa: E731
        if zk == k:
            if zi == i:
                return "update" if self._better(yk, k, yi, i) else "leave"
            if zi == k or zi is None:
                return "update"
            return "update" if newer(zi) or self._better(yk, k, yi, zi) else "leave"
        if zk == i:
            if zi == i or zi is None:
                return "leave"
            if zi == k:
                return "reset"
            return "reset" if newer(zi) else "leave"
        if zk is None:
            if zi == i or zi is None:
                return "leave"
            if zi == k:
                return "update"
            return "update" if newer(zi) else "leave"
        m = zk
        if zi == i:
            return "update" if newer(m) and self._better(yk, m, yi, i) else "leave"
        if zi == k:
            return "update" if newer(m) else "reset"
        if zi == m or zi is None:
            return "update" if newer(m) else "leave"
        n = zi
        if newer(m) and newer(n):
            return "update"
        if newer(m) and self._better(yk, m, yi, n):
            return "update"
        if newer(n) and self.s.get(m, NEG) > sk.get(m, NEG):
            return "reset"
        return "leave"

    def receive(self, msg, t):
        """Merge a neighbour's message. Returns True if this agent's state changed."""
        k = msg["from"]
        before = (tuple(self.bundle), tuple(sorted((j, self.z.get(j)) for j in self.z)))
        for mid, mdef in (msg.get("missions") or {}).items():
            self.add_mission(mdef, t)
        for mid, tc in (msg.get("closed") or {}).items():
            self.close_mission(mid, tc)
        self.add_tasks((msg.get("tasks") or {}).values())
        for j, td in (msg.get("done") or {}).items():
            if j not in self.done:
                self.done[j] = td
                if j in self.bundle:
                    self._drop(j)
                self.changed = True
        sk = msg.get("s") or {}
        self.s[k] = t
        self.lost.pop(k, None)
        for m, tm in sk.items():
            if m in self.lost and tm > self.lost[m]:
                self.lost.pop(m, None)
        for j in sorted(set(msg.get("z") or {}) | set(self.z)):
            if j in self.done or j not in self.tasks:
                continue
            if j not in (msg.get("z") or {}):
                continue                     # the sender has no information about this task
            yk, zk = float(msg["y"].get(j, 0.0)), msg["z"].get(j)
            if zk is not None and zk != k and zk in self.lost:
                yk, zk = 0.0, None           # stale claim of an agent we know is gone
            yi, zi = self.y.get(j, 0.0), self.z.get(j)
            action = self._rule(k, yk, zk, sk, yi, zi)
            if action == "update" and (yi, zi) != (yk, zk):
                self.y[j], self.z[j] = yk, zk
            elif action == "reset":
                self._reset(j)
        for m, tm in sk.items():
            if m not in (self.id, k) and tm > self.s.get(m, NEG):
                self.s[m] = tm
        self._release_outbid()
        self.rounds += 1
        self.last_consensus_t = t
        after = (tuple(self.bundle), tuple(sorted((j, self.z.get(j)) for j in self.z)))
        if after != before:
            self.changed = True
        return after != before

    def _release_outbid(self):
        for n, j in enumerate(self.bundle):
            if self.z.get(j) != self.id:
                for later in self.bundle[n + 1:]:
                    if self.z.get(later) == self.id:
                        self._reset(later)
                released = self.bundle[n:]
                self.bundle = self.bundle[:n]
                self.path = [p for p in self.path if p in self.bundle]
                for r in released:
                    self.locked.discard(r)
                    self.claimed_at.pop(r, None)
                self.events.append(("outbid", j, str(self.z.get(j))))
                self.changed = True
                return released
        return []

    def check_peers(self, t):
        """Declare silent peers lost and reset the tasks they held. Returns {peer: [tasks]}."""
        out = {}
        for m, tm in list(self.s.items()):
            if m == self.id or m in self.lost or t - tm <= self.peer_timeout:
                continue
            self.lost[m] = t
            live = set(self.open_tasks())
            held = [j for j, w in self.z.items() if w == m and j in live]
            for j in held:
                self._reset(j)
            out[m] = held
            if held:
                self.changed = True
        return out


# ---------------------------------------------------------------------- local sub-task derivation
def _task(mid, suffix, kind, position, by, t, severity=3, **extra):
    priority = KIND_PRIORITY[kind] * (0.6 + 0.1 * max(1, min(5, int(severity))))
    return {"id": f"{mid}/{suffix}", "mission_id": mid, "kind": kind, "position": [round(float(position[0]), 1), round(float(position[1]), 1)],
            "priority": round(priority, 2), "duration": KIND_DURATION[kind], "created_by": by, "created_t": round(t, 2), **extra}


def relay_chain(mission, stations, radio_range, gateway_range, by, t, bounds=None):
    """Relay points on the line scene -> nearest ground station so that every hop fits in radio range."""
    if not stations:
        return []
    sx, sy = mission["position"][:2]
    station = min(stations, key=lambda s: math.hypot(s[0] - sx, s[1] - sy))
    distance = math.hypot(station[0] - sx, station[1] - sy)
    hop = 0.85 * min(radio_range, gateway_range)
    if distance <= hop:
        return []
    count = int(math.ceil(distance / hop)) - 1
    out = []
    for k in range(1, count + 1):
        f = k / (count + 1)
        out.append(_task(mission["id"], f"relay-{k}", "relay", (sx + (station[0] - sx) * f, sy + (station[1] - sy) * f), by, t, mission.get("severity", 3)))
    return out


def mission_subtasks(mission, stations, radio_range, gateway_range, by, t):
    """What a drone derives as soon as it hears a mission: assess the scene, plus comms relays when the scene is
    farther from the command link than one radio hop."""
    return [_task(mission["id"], "assess", "assess", mission["position"], by, t, mission.get("severity", 3))] + \
        relay_chain(mission, stations, radio_range, gateway_range, by, t)


def scene_subtasks(mission, observation, by, t, bounds=None):
    """What an aircraft on scene derives from what it sees. ``observation``: people, people_uncertain (bool),
    hazards (list), needs_unmet (list of supply items), ground_unit_coming (bool), severity, category."""
    mid, pos, sev = mission["id"], mission["position"], observation.get("severity", mission.get("severity", 3))
    out = []
    people = int(observation.get("people") or 0)
    if people >= 3 or observation.get("hazards") or sev >= 4:
        out.append(_task(mid, "overwatch", "overwatch", pos, by, t, sev))
    if observation.get("people_uncertain") or observation.get("category") in SEARCH_CATEGORIES:
        sectors = 4 if people >= 5 else 2
        for k in range(sectors):
            angle = 2 * math.pi * k / sectors + math.pi / 4
            x, y = pos[0] + 120 * math.cos(angle), pos[1] + 120 * math.sin(angle)
            if bounds:
                x = min(max(x, bounds[0] + 20), bounds[2] - 20)
                y = min(max(y, bounds[1] + 20), bounds[3] - 20)
            out.append(_task(mid, f"search-{k + 1}", "search", (x, y), by, t, sev))
    for item in observation.get("needs_unmet") or []:
        if item in SUPPLY_ITEMS:
            out.append(_task(mid, f"supply-{item}", "supply", pos, by, t, sev, payload=item))
    if observation.get("ground_unit_coming"):
        out.append(_task(mid, "handover", "handover", pos, by, t, sev))
    return out
