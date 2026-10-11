"""COMMAND CENTER agent: plays DisruptionOps' role against the swarm over the real API.

The swarm is an agency. This process is the orchestrator that talks to it:

    scenario script --agency_request--> swarm  (accepts or declines on its own)
    swarm --events/snapshot (accepted, launched, on_scene, briefing, supply_delivered,
            handed_over, completed)--> command center (tracks each task)
    swarm --situation_report / alert--> command center --external_resource_update--> swarm
        (orchestrator mode: this process IS the orchestrator: it plans from the situation reports with the
         stand-in planner (swarm_ops.nodes.orchestrator.AllocationPlanner), commits ground units and drives them
         to the scene; it first tells the swarm to disable its built-in orchestrator)
        (standalone mode: nothing is committed; the engine's local mock answers)

It uses only the public WebSocket protocol (disruptionops.swarm.v2) with the integration token
(env OPS_INTEGRATION_TOKEN, the same variable the server reads). No in-process shortcuts.

Run:   python -m swarm_ops.command_center --url ws://127.0.0.1:8861/ops/ws --scenario pccoe_pawana_night
A JSON timeline is written at the end (--timeline). Simulation only.

REAL mode (RealModeAdapter below) maps the same calls to DisruptionOps REST endpoints. It is a
skeleton: dry-run by default, never exercised against a live backend.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import random
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

GROUND_SPEED = 9.0  # m/s, matches the engine's ground-unit speed
TERMINAL = ("completed", "declined")
# Order used when two statuses race: later wins for the "current" label.
STATUS_RANK = {"requested": 0, "accepted": 1, "launched": 2, "on_scene": 3, "briefing": 4, "resource_request": 5, "dispatch_ordered": 5,
               "resource_assigned": 6, "supply_delivered": 6, "ground_arrived": 7, "retasked": 7, "recalled": 8,
               "handed_over": 9, "completed": 10, "declined": 10}


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])



# ----------------------------------------------------------------------------- DisruptionOps-derived data
CATALOG_PATH = Path(__file__).resolve().parents[1] / "assets" / "operations" / "agency-catalog.json"
# Unit kind aliases: DisruptionOps catalog kind -> kinds the swarm may ask for (engine CATALOG_KINDS and scenarios.pcmc.UNIT_KINDS).
KIND_ALIASES = {"rescue_team": {"rescue_team", "rescue"}, "boat": {"boat", "rescue"}, "jcb": {"jcb", "engineering"}}
_HAZARD_WORDS = (("water", "flood"), ("pawana", "river_overflow"), ("river", "river_overflow"), ("gas", "toxic_gas"), ("smoke", "smoke"), ("fire", "fire"), ("wire", "live_wire"),
                 ("spark", "live_wire"), ("wall", "structural_collapse"), ("collapsed", "structural_collapse"), ("tree", "road_blocked"), ("underpass", "flooded_road"))
_VULNERABLE_WORDS = (("elderly", "elderly"), ("old ", "elderly"), ("child", "children"), ("kid", "children"), ("baby", "infants"), ("pregnan", "pregnant"), ("disabled", "disabled"))


def load_catalog(path=CATALOG_PATH):
    """DisruptionOps migration 029 units and lifelines (assets/operations/agency-catalog.json); empty if missing."""
    try:
        return json.loads(Path(path).read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {"resources": [], "lifelines": [], "wards": []}


def situation_for(task, incident=None, catalog=None, report_text=None):
    """Rich situation block for an agency_request, taken from the DisruptionOps-derived data on the incident
    (its report text, DO category, ward, caller count) and the catalog (nearest lifelines). Facts the source
    does not state are left out, never invented: vulnerable groups and hazards are read from the report text."""
    catalog = catalog if catalog is not None else load_catalog()
    incident = incident or {}
    text = report_text or incident.get("title") or task.get("title") or ""
    low = text.lower() + " "
    position = task["position"]
    lifelines = sorted(({"id": l["source_id"], "kind": l["kind"], "name": l["name"], "distance_m": round(_dist(position, l["local_xy"]))}
                        for l in catalog.get("lifelines", []) if l.get("local_xy")), key=lambda l: l["distance_m"])[:3]
    situation = {"report_text": text[:240], "disruptionops_category": incident.get("disruptionops_category"), "category": task.get("category"),
                 "people": task.get("people"), "crowd_size": task.get("people"), "reporters": incident.get("callers"),
                 "ward": {"id": incident.get("ward_id"), "name": incident.get("named_ward"), "place": incident.get("place")} if incident.get("ward_id") else None,
                 "vulnerable": sorted({label for word, label in _VULNERABLE_WORDS if word in low}),
                 "injured_reported": any(w in low for w in ("injur", "trapped", "coughing", "unconscious")),
                 "hazards": sorted({label for word, label in _HAZARD_WORDS if word in low}),
                 "nearby_lifelines": lifelines, "severity": task.get("priority"), "needs": task.get("needs"),
                 "source": "disruptionops:" + ("scenarios.pccoe_pawana_night" if incident.get("hotspot_id") else "catalog+engine template")}
    return {k: v for k, v in situation.items() if v not in (None, [], "")}

# ----------------------------------------------------------------------------- scenarios
def load_scenario(name_or_path, *, seed=7, count=5):
    """Return a list of task dicts. Sources, in order: JSON file, engine scenario incidents, random points."""
    path = Path(str(name_or_path))
    if path.suffix == ".json" and path.exists():
        tasks = json.loads(path.read_text(encoding="utf8"))
        return [_normalise(t, i) for i, t in enumerate(tasks)]
    try:
        from swarm_ops.engine import OperationsSimulation
        try:
            import swarm_ops.scenarios  # noqa: F401 - registers the PCMC region scenarios when present
        except ImportError:
            pass
        sim = OperationsSimulation(seed=seed, scenario=str(name_or_path))
        catalog = load_catalog()
        tasks = []
        for i in sim.incidents:
            task = {"kind": "sos", "position": i["position"], "category": i["category"], "people": i["people"],
                    "needs": i["needs"], "priority": i["severity"], "title": i["title"]}
            task["situation"] = situation_for(task, i, catalog)
            tasks.append(task)
        if tasks:
            return [_normalise(t, n, delay=3.0 * n) for n, t in enumerate(tasks)]
    except Exception:  # noqa: BLE001 - scenario not available in this checkout: fall back to random points
        pass
    return random_scenario(seed=seed, count=count)


def random_scenario(*, seed=7, count=5):
    from swarm_ops.engine import OperationsSimulation
    sim = OperationsSimulation(seed=seed)
    catalog = load_catalog()
    r = random.Random(f"command-center:{seed}")
    kinds = ["sos", "sos", "sos", "assess", "supply", "sos", "overwatch"]
    cats = ["medical", "flood", "fire", "trapped", "medical"]
    tasks = []
    for n in range(count):
        category = cats[n % len(cats)]
        position, _ = sim._random_site(category, r)
        if position is None:
            continue
        title, severity, (low, high), needs, _required = sim.INCIDENT_TEMPLATES[category]
        task = {"kind": kinds[n % len(kinds)], "position": position, "category": category,
                "people": r.randint(low, high), "needs": needs, "priority": severity, "title": title}
        task["situation"] = situation_for(task, None, catalog)
        tasks.append(_normalise(task, n, delay=2.5 * n))
    return tasks


def _normalise(task, index, delay=None):
    t = dict(task)
    t.setdefault("kind", "sos")
    t.setdefault("delay_s", delay if delay is not None else 3.0 * index)
    t.setdefault("slot", index)
    return t


# ----------------------------------------------------------------------------- the agent
class CommandCenter:
    def __init__(self, url, token, tasks, *, mode="orchestrator", timeline_path=None, timeout_s=300.0,
                 scenario="custom", log=print, min_gap_s=0.15, tick_s=1.0):
        if mode not in ("orchestrator", "standalone", "orchestrated"):
            raise ValueError("mode must be orchestrator, standalone or orchestrated")
        self.url, self.token, self.mode = url, token, mode
        self.tasks_script = tasks
        self.scenario, self.timeline_path, self.timeout_s = scenario, timeline_path, timeout_s
        self.log, self.min_gap_s, self.tick_s = log, min_gap_s, tick_s
        self.run_prefix = uuid.uuid4().hex[:6]
        self.tasks = {}            # request_id -> task record
        self.by_incident = {}      # incident_id -> [request_id]
        self.resource_requests = {}  # swarm RR id -> record
        self.units = {}            # unit id -> committed ground unit (orchestrator mode)
        self.depots = []           # available ground resources seen before the first external update
        self.messages, self._seen_messages = [], set()
        self.situation_reports, self.alerts, self.intake, self.planner = {}, [], {}, None
        self.events, self._seen_events = [], set()
        self.state = {}
        self.sim_t, self._last_sim_t = 0.0, None
        self.run_id = None
        self._acks, self._last_send, self._seq = {}, 0.0, 0
        self._wall0 = time.monotonic()
        self._external = False
        self.failed = []
        self._closing = set()
        self._planned = set()

    # -- transport
    async def send(self, action, payload, command_id=None, timeout=5.0):
        self._seq += 1
        command_id = command_id or f"cc-{self.run_prefix}-{self._seq}"
        wait = self.min_gap_s - (time.monotonic() - self._last_send)  # server closes the socket above 10 commands/s
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send = time.monotonic()
        future = asyncio.get_running_loop().create_future()
        self._acks[command_id] = future
        await self.ws.send(json.dumps({"type": "command", "id": command_id, "action": action, "payload": payload,
                                       "expected_run_id": self.run_id}))
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._acks.pop(command_id, None)

    async def _reader(self):
        async for raw in self.ws:
            msg = json.loads(raw)
            if msg.get("type") == "ack":
                future = self._acks.get(msg.get("id"))
                if future and not future.done():
                    future.set_result(msg)
            elif msg.get("type") == "snapshot":
                self._on_state(msg["state"])

    # -- state handling
    def _stamp(self):
        return f"[{time.monotonic() - self._wall0:6.1f}s wall | sim {self.sim_t:7.1f}s]"

    def say(self, text):
        self.log(f"{self._stamp()} {text}")

    def _on_state(self, state):
        if "world" not in state and self.state.get("world"):
            state = {**state, "world": self.state["world"]}
        if state.get("run_id") != self.run_id and self.run_id is not None and self.tasks:
            self.say("run changed on the server; tracking stops for earlier tasks")
        if self.run_id is None:
            self.depots = [dict(r) for r in state.get("resources", []) if r.get("state") == "available"]
        self.run_id = state.get("run_id")
        self.state = state
        self.sim_t = state.get("t", self.sim_t)
        self._ingest_events(state.get("events", []))
        self._ingest_messages(state.get("messages", []))
        self._ready.set()

    def _link(self, incident_id, request_id):
        ids = self.by_incident.setdefault(incident_id, [])
        if request_id not in ids:
            ids.append(request_id)

    def _record(self, request_id, status, text, event=None):
        task = self.tasks[request_id]
        entry = {"sim_t": self.sim_t if event is None else event.get("time", self.sim_t), "wall": round(time.monotonic() - self._wall0, 2),
                 "status": status, "text": text}
        if not task["history"] or task["history"][-1]["status"] != status or task["history"][-1]["text"] != text:
            task["history"].append(entry)
            if STATUS_RANK.get(status, 0) >= STATUS_RANK.get(task["status"], 0):
                task["status"] = status
            self.say(f"{request_id:<16} {status:<17} {text}")

    def _ingest_events(self, events):
        for event in events:
            event_id = event.get("id")
            if event_id in self._seen_events:
                continue
            status = event.get("status")
            data = event.get("data") or {}
            ids = []
            if event.get("kind") in ("request_accepted", "request_declined") and data.get("request_id"):
                ids = [data["request_id"]]
            elif event.get("incident_id") in self.by_incident:
                ids = list(self.by_incident[event["incident_id"]])
            if status and not ids and event.get("incident_id") not in self.by_incident:
                if event.get("kind") not in ("request_accepted", "request_declined"):
                    continue  # not (yet) ours; retried while it stays in the snapshot window
            self._seen_events.add(event_id)
            self.events.append(event)
            if event.get("kind") == "dispatch_order":
                continue  # the built-in orchestrator's order; this client records its own
            for request_id in ids:
                if request_id in self.tasks and status:
                    self._record(request_id, status, event.get("message", ""), event)

    def _ingest_messages(self, messages):
        for message in messages or []:
            key = message.get("id")
            if key in self._seen_messages:
                continue
            self._seen_messages.add(key)
            self.messages.append(message)
            body = message.get("body") or {}
            if message.get("kind") == "situation_report":
                self.situation_reports[message.get("incident_id")] = message
            elif message.get("kind") == "alert":
                self.alerts.append(message)
            elif message.get("kind") == "report" and body.get("intake"):
                self.intake[message.get("incident_id")] = body
            self.say(f"msg {message.get('from', '?')}->{message.get('to', '?')} [{message.get('kind', '?')}] {message.get('text', '')}"[:200])

    # -- the commander's own decisions
    async def issue(self, slot, task):
        request_id = f"cc-{self.scenario}-{self.run_prefix}-{slot + 1:02d}"
        payload = {"request_id": request_id, "kind": task["kind"], "position": task["position"], "category": task.get("category", "medical"),
                   "people": task.get("people", 1), "needs": task.get("needs") or None, "priority": task.get("priority", 3),
                   "requested_by": "command-center", "sim_run_id": self.run_id, "situation": task.get("situation")}
        payload = {k: v for k, v in payload.items() if v is not None}
        record = self.tasks.setdefault(request_id, {"request_id": request_id, "task": task, "status": "requested", "incident_id": None,
                                                    "accepted": None, "history": [], "attempts": 0})
        action = "agency_request"
        if self.mode == "orchestrated":
            action = "orchestrate_incident"
            payload = {k: v for k, v in payload.items() if k not in ("kind", "requested_by", "sim_run_id", "needs")}
            payload.update(external_id=f"DO-{request_id}", requested_by="DisruptionOps command center", assignments=self.plan_assignments(task))
        for attempt in range(1, 4):
            record["attempts"] = attempt
            ack = await self.send(action, payload, command_id=f"{request_id}#{attempt}" if attempt > 1 else request_id)
            if not ack.get("ok"):
                self._record(request_id, "declined", f"command rejected: {ack.get('error')}")
                return record
            result = ack["result"]
            record["accepted"], record["incident_id"] = result.get("accepted"), result.get("incident_id")
            record["reply_kind"] = result.get("reply_kind")
            record["swarm_answer"] = {k: result.get(k) for k in ("reason", "reply_kind", "units", "eta_s", "assigned_drones", "plan", "merged", "duplicate")}
            if result.get("accepted"):
                self._link(result["incident_id"], request_id)
                self._record(request_id, "accepted", f"[{result.get('reply_kind')}] {result.get('reason')}; eta {result.get('eta_s')}s; drones {result.get('assigned_drones')}")
                return record
            if "retry later" in str(result.get("reason")):
                self.say(f"{request_id} swarm busy ({result['reason']}); retrying in 5 s")
                await asyncio.sleep(5)
                continue
            self._record(request_id, "declined", str(result.get("reason")))
            return record
        self._record(request_id, "declined", "swarm stayed busy after 3 attempts")
        return record

    def plan_assignments(self, task):
        """ORCHESTRATED mode: the command center decides who goes (the swarm only executes): one assessing drone plus the
        nearest free catalog unit for each ground kind the category requires."""
        if "assignments" in task:
            return task["assignments"]
        required = task.get("required")
        if required is None:
            try:
                from swarm_ops.engine import OperationsSimulation
                required = OperationsSimulation.INCIDENT_TEMPLATES[task.get("category", "medical")][4]
            except Exception:  # noqa: BLE001
                required = []
        if not hasattr(self, "_taken_ids"):
            self._taken_ids = set()
        resources = []
        for kind in required:
            rr = {"needed_kind": kind, "position": task["position"]}
            pool = sorted((p for p in self._unit_pool() if kind in p["kinds"] and p["id"] not in self._taken_ids),
                          key=lambda p: (_dist(p["position"], task["position"]), p["id"]))
            if pool:
                self._taken_ids.add(pool[0]["id"])
                resources.append({"id": pool[0]["id"], "kind": kind, "agency": pool[0].get("operator") or "DisruptionOps"})
        return {"drones": [{"count": 1, "role": "assess"}], "resources": resources}

    # -- thin wrappers over the integration commands (usable from scripts and tests after open())
    async def open(self):
        from websockets.asyncio.client import connect
        self._ready = asyncio.Event()
        url = self.url + ("&" if "?" in self.url else "?") + "token=" + urllib.parse.quote(self.token)
        self.ws = await connect(url, max_size=None, open_timeout=10)
        self._reader_task = asyncio.create_task(self._reader())
        await asyncio.wait_for(self._ready.wait(), 10)
        return self

    async def close(self):
        task = getattr(self, "_reader_task", None)
        if task:
            task.cancel()
        if getattr(self, "ws", None):
            await self.ws.close()

    async def orchestrate(self, request_id, position, category, assignments, **extra):
        payload = {"request_id": request_id, "position": position, "category": category, "assignments": assignments, **extra}
        return await self.send("orchestrate_incident", {k: v for k, v in payload.items() if v is not None})

    async def assign(self, incident_id, drones=(), resources=()):
        return await self.send("assign", {"incident_id": incident_id, "drones": list(drones), "resources": list(resources)})

    async def unassign(self, incident_id, drone_ids=(), resource_ids=()):
        return await self.send("unassign", {"incident_id": incident_id, "drone_ids": list(drone_ids), "resource_ids": list(resource_ids)})

    async def set_coordination(self, mode, incident_id=None, **params):
        """central | decentralized | orchestrated, globally or for one incident (see docs/DECENTRALIZED.md)."""
        return await self.send("set_coordination", {"mode": mode, **({"incident_id": incident_id} if incident_id else {}), **params})

    async def recall_drone(self, drone_id, reason="recalled by command center"):
        return await self.send("recall_drone", {"drone_id": drone_id, "reason": reason})

    async def update_incident(self, incident_id, **fields):
        return await self.send("update_incident", {"incident_id": incident_id, **fields})

    async def cancel_incident(self, incident_id, reason="cancelled"):
        return await self.send("cancel_incident", {"incident_id": incident_id, "reason": reason})

    async def close_incident(self, incident_id, reason="closed"):
        return await self.send("close_incident", {"incident_id": incident_id, "reason": reason})

    async def control(self, autonomy, reason=None):
        return await self.send("control", {"autonomy": autonomy, "reason": reason})

    def _answer_requests(self):
        """Orchestrator mode: plan from the swarm's situation reports (stand-in for DisruptionOps' planner) and commit ground units."""
        if self.mode != "orchestrator":
            return []
        from swarm_ops.nodes.orchestrator import AllocationPlanner
        self.planner = self.planner or AllocationPlanner()
        pending = []
        for iid, message in list(self.situation_reports.items()):
            report = message["body"]
            key = (iid, report.get("revision"))
            if key in self._planned or report.get("controlled_by") == "disruptionops" or (report.get("scene") or {}).get("status") == "resolved":
                continue
            self._planned.add(key)
            intake = self.intake.get(iid, {})
            held = {}
            for u in self.units.values():
                if u["incident_id"] == iid and u["state"] not in ("released", "service_done"):
                    held[u["kind"]] = held.get(u["kind"], 0) + 1
            situation = dict(report["scene"])
            pool = self._unit_pool()
            resources = [{"id": p["id"], "kind": {"rescue": "rescue_team", "engineering": "jcb"}.get(sorted(p["kinds"])[0], sorted(p["kinds"])[0]), "state": "available", "capacity": p.get("capacity"),
                          "name": p.get("name"), "position": p["position"][:2], "assigned_to": None} for p in pool if p["id"] not in {u["depot_id"] for u in self.units.values()}]
            known = {k for p in pool for k in p["kinds"]}
            plan, source, error, problems, latency = self.planner.plan(situation, {"resources": resources, "concurrent": []}, hint=intake.get("required_hint") or [],
                                                                       owned=bool(intake.get("plan_owned", True)), known_kinds=known)
            wanted = {q["kind"]: q["qty"] for q in plan["resource_requests"]}
            if not wanted and intake.get("required_hint") and not held:
                wanted = {k: 1 for k in intake["required_hint"]}
            for kind, qty in wanted.items():
                missing = qty - held.get(kind, 0)
                if missing <= 0:
                    continue
                rr = {"needed_kind": kind, "request_id": f"{iid}-DO{len(self.resource_requests) + 1}", "position": situation.get("position") or [0, 0, 0]}
                units = [self._commit_unit(rr, {"id": iid, "position": rr["position"]}) for _ in range(missing)]
                rid = f"{iid}-DO{len(self.resource_requests) + 1}"
                self.resource_requests[rid] = {"request_id": rid, "incident_id": iid, "kind": kind, "qty": missing, "stage": f"plan:{source}", "committed_sim_t": self.sim_t,
                                               "units": [u["id"] for u in units]}
                for request_id in self.by_incident.get(iid, []):
                    self._record(request_id, "dispatch_ordered", f"orchestrator plan ({source}) from situation r{report.get('revision')}: {missing}x {kind}; committing {[u['id'] for u in units]}")
                pending.extend(units)
        return pending

    def _unit_pool(self):
        """Candidate ground units: the DisruptionOps catalog (positions from the swarm's own snapshot when it places them),
        plus any other available resource the swarm lists."""
        if not hasattr(self, "_catalog_units"):
            self._catalog_units = load_catalog().get("resources", [])
        live = {d["id"]: d for d in self.depots}
        pool, seen = [], set()
        for c in self._catalog_units:
            depot = live.get(c["source_id"])
            position = (depot or {}).get("position") or c.get("local_xy")
            if position:
                pool.append({"id": c["source_id"], "kinds": {c["kind"]} | KIND_ALIASES.get(c["kind"], set()) | ({depot["kind"]} if depot else set()),
                             "position": list(position)[:2] + [0], "name": c.get("name"), "operator": c.get("operator"), "capacity": c.get("capacity")})
                seen.add(c["source_id"])
        pool += [{"id": d["id"], "kinds": {d["kind"]}, "position": list(d["position"])[:2] + [0], "name": d.get("name"), "operator": d.get("agency"),
                  "capacity": d.get("capacity")} for d in self.depots if d["id"] not in seen]
        return pool

    def _commit_unit(self, rr, incident):
        """Nearest free catalog unit whose kind matches the request; a labelled synthetic unit only if none is free."""
        kind = rr["needed_kind"]
        taken = {u["depot_id"] for u in self.units.values() if u["state"] not in ("released", "service_done")}
        candidates = sorted((p for p in self._unit_pool() if kind in p["kinds"] and p["id"] not in taken),
                            key=lambda p: (_dist(p["position"], rr["position"]), p["id"]))
        if candidates:
            pick = candidates[0]
            unit = {"id": pick["id"], "depot_id": pick["id"], "position": pick["position"], "name": pick["name"], "operator": pick["operator"],
                    "from_catalog": True, "distance_at_commit_m": round(_dist(pick["position"], rr["position"])), "capacity": pick["capacity"] or 1}
        else:
            n = sum(u["kind"] == kind for u in self.units.values()) + 1
            unit = {"id": f"DO-{kind}-{n}", "depot_id": f"synthetic-{kind}-{n}", "position": [0.0, 0.0, 0.0], "from_catalog": False, "capacity": 1}
        unit.update(kind=kind, incident_id=incident["id"], target=list(incident["position"]), state="en_route", rr=rr["request_id"],
                    dispatched_sim_t=self.sim_t, handoff_sent=False)
        self.units[unit["id"]] = unit
        return unit

    def _advance_units(self):
        dt = 0.0 if self._last_sim_t is None else max(0.0, self.sim_t - self._last_sim_t)
        self._last_sim_t = self.sim_t
        changed = []
        snapshot_resources = {r["id"]: r for r in self.state.get("resources", [])}
        incidents = {i["id"]: i for i in self.state.get("incidents", [])}
        for unit in self.units.values():
            live = snapshot_resources.get(unit["id"])
            if unit["state"] == "released":
                continue
            if live and unit["state"] in ("on_site", "on_scene", "evacuating") and live.get("state") == "available" and live.get("incident_id") is None:
                unit["state"] = "released"
                self.say(f"unit {unit['id']} released by the swarm after service at {unit['incident_id']}")
                continue
            if live and live.get("state") == "on_scene" and unit["state"] in ("on_site", "on_scene"):
                unit["state"] = "on_scene"
                unit.setdefault("on_scene_t", self.sim_t)
            if unit["state"] == "on_scene":
                people = incidents.get(unit["incident_id"], {}).get("people", 1)
                if self.sim_t - unit["on_scene_t"] >= 10 + 3 * people:  # the orchestrator decides when ground service is done
                    unit["state"] = "service_done"
                    changed.append(unit)
                    continue
            if unit["state"] == "en_route":
                step = GROUND_SPEED * dt
                remaining = _dist(unit["position"], unit["target"])
                if remaining <= max(step, 3.0):
                    unit["position"] = list(unit["target"])[:2] + [0]
                    unit["state"] = "on_site"
                    for request_id in self.by_incident.get(unit["incident_id"], []):
                        self._record(request_id, "ground_arrived", f"{unit['id']} ({unit['kind']}) on site; offering handoff")
                elif step > 0:
                    f = step / remaining
                    unit["position"] = [unit["position"][0] + (unit["target"][0] - unit["position"][0]) * f, unit["position"][1] + (unit["target"][1] - unit["position"][1]) * f, 0]
                changed.append(unit)
            elif unit["state"] == "on_site":
                changed.append(unit)  # keep offering the handoff until the swarm confirms (briefing revisions move)
            if unit["state"] in ("on_site", "on_scene") and incidents.get(unit["incident_id"], {}).get("status") == "resolved":
                unit["state"] = "released"
        return changed, incidents

    def _resolvable(self, incidents):
        """Incidents whose required agency kinds all finished service: the orchestrator closes them (external ground is authoritative)."""
        done = []
        for incident_id, incident in incidents.items():
            if incident.get("status") == "resolved" or incident_id in self._closing:
                continue
            mine = [u for u in self.units.values() if u["incident_id"] == incident_id]
            if mine and all(u["state"] in ("service_done", "released") for u in mine) and incident.get("handed_over"):
                done.append(incident_id)
        return done

    def _note_completed(self, incidents):
        for incident_id, incident in incidents.items():
            if incident.get("status") == "resolved":
                for request_id in self.by_incident.get(incident_id, []):
                    self._record(request_id, "completed", f"incident {incident_id} resolved at sim {incident.get('resolved_at')}")

    async def _push_units(self, units, incidents, resolve=()):
        if not units and not resolve:
            return
        resources, handoffs = [], []
        for unit in units:
            remaining = _dist(unit["position"], unit["target"])
            if unit["state"] == "service_done":
                resources.append({"id": unit["id"], "kind": unit["kind"], "state": "available", "position": [round(v, 2) for v in unit["position"]],
                                  "incident_id": None, "eta": None, "agency": "DisruptionOps command center", "capacity": unit["capacity"]})
                continue
            resources.append({"id": unit["id"], "kind": unit["kind"], "state": unit["state"], "position": [round(v, 2) for v in unit["position"]],
                              "incident_id": unit["incident_id"], "eta": round(remaining / GROUND_SPEED, 1), "agency": "DisruptionOps command center",
                              "capacity": unit["capacity"]})
            if unit["state"] == "on_site":
                revision = (incidents.get(unit["incident_id"], {}).get("briefing") or {}).get("revision")
                handoffs.append({"incident_id": unit["incident_id"], "resource_id": unit["id"], "accepted": True, "briefing_revision": revision})
        payload = {"source": "disruptionops", "run_id": self.run_id, "resources": resources, "handoffs": handoffs,
                   "incidents": [{"local_id": i, "status": "resolved"} for i in resolve]}
        ack = await self.send("external_resource_update", payload)
        if not ack.get("ok"):
            self.failed.append(ack.get("error"))
            self.say(f"external_resource_update rejected: {ack.get('error')}")
        else:
            self._external = True
            self._closing.update(resolve)
            for incident_id in resolve:
                self.say(f"orchestrator closes {incident_id}: all required agencies finished service")

    def _all_terminal(self):
        return len(self.tasks) >= len(self.tasks_script) and all(t["status"] in TERMINAL for t in self.tasks.values())

    # -- main loop
    async def run(self):
        from websockets.asyncio.client import connect
        self._ready = asyncio.Event()
        url = self.url + ("&" if "?" in self.url else "?") + "token=" + urllib.parse.quote(self.token)
        async with connect(url, max_size=None, open_timeout=10) as self.ws:
            reader = asyncio.create_task(self._reader())
            try:
                await asyncio.wait_for(self._ready.wait(), 10)
                self.say(f"connected; run {self.run_id[:8]}, mode {self.mode}, {len(self.tasks_script)} task(s), scenario {self.scenario}")
                if self.mode in ("orchestrator", "orchestrated"):
                    await self.send("control", {"orchestrator": "external"})   # we are the orchestrator now
                start = time.monotonic()
                queue = sorted(self.tasks_script, key=lambda t: t["delay_s"])
                while time.monotonic() - self._wall0 < self.timeout_s:
                    if queue and time.monotonic() - start >= queue[0]["delay_s"]:
                        task = queue.pop(0)
                        await self.issue(task["slot"], task)
                    new_units = self._answer_requests()
                    changed, incidents = self._advance_units()
                    resolve = self._resolvable(incidents) if self.mode == "orchestrator" else []
                    await self._push_units(changed or new_units, incidents, resolve)
                    self._note_completed({i["id"]: i for i in self.state.get("incidents", [])})
                    if not queue and self._all_terminal():
                        break
                    await asyncio.sleep(self.tick_s)
                    if reader.done():
                        raise ConnectionError("server closed the connection")
            finally:
                reader.cancel()
        summary = self.summary()
        self.say("done: " + json.dumps(summary["totals"]))
        if self.timeline_path:
            Path(self.timeline_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.timeline_path).write_text(json.dumps(summary, indent=1, default=str), encoding="utf8")
            self.say(f"timeline written to {self.timeline_path}")
        return summary

    def summary(self):
        tasks = list(self.tasks.values())
        return {"scenario": self.scenario, "mode": self.mode, "run_id": self.run_id, "sim_t_end": self.sim_t,
                "totals": {"tasks": len(tasks), "accepted": sum(bool(t["accepted"]) for t in tasks), "declined": sum(t["accepted"] is False for t in tasks),
                           "completed": sum(t["status"] == "completed" for t in tasks), "resource_requests": len(self.resource_requests),
                           "units_committed": len(self.units), "messages": len(self.messages), "errors": len(self.failed)},
                "tasks": self.tasks, "resource_requests": self.resource_requests, "units": self.units, "messages": self.messages,
                "events_seen": len(self.events), "errors": self.failed}


# ----------------------------------------------------------------------------- REAL-mode adapter (skeleton)
class RealModeAdapter:
    """Maps command-center calls onto DisruptionOps REST endpoints (backend/app/api/v1, prefix /api/v1).

    SKELETON. Verified against the route and model definitions in commit 1697407 only; never run against a
    live backend. Dry-run is the default: requests are returned/logged, not sent, unless dry_run=False.
    Auth is a staff bearer token (DISRUPTIONOPS_TOKEN). ward_id must come from DisruptionOps' own taxonomy.
    Category mapping is deliberately small: anything unknown goes through as an unclassified report.
    """
    CATEGORY = {"flood": "person_stranded", "trapped": "person_stranded", "medical": "unknown_report",
                "fire": "unknown_report", "blocked_route": "unknown_report"}
    CAPABILITY = {"ambulance": "ambulance", "fire_engine": "fire_engine", "rescue": "rescue_team", "engineering": "jcb"}  # assumed ids

    def __init__(self, base_url, token="", *, anchor=(18.6526632, 73.7624378), ward_id="pcmc-pawana", dry_run=True, city_id="pune"):
        self.base, self.token, self.anchor, self.ward_id, self.dry_run, self.city_id = base_url.rstrip("/"), token, anchor, ward_id, dry_run, city_id
        self.sent = []

    def _lnglat(self, position):
        from swarm_ops.integrations.disruptionops_bridge import geo_position
        return geo_position(position, self.anchor)

    # task -> POST /reports (the same door a citizen/agency uses; trust, dedup, severity and the planner take it from there)
    def report_for_task(self, task, sim_run_id=None):
        body = {"wardId": self.ward_id, "category": self.CATEGORY.get(task.get("category"), "unknown_report"),
                "location": self._lnglat(task["position"]), "note": f"[{task['kind']}] {task.get('title', '')} people={task.get('people', 1)} needs={task.get('needs')}",
                "source": "agency", "cityId": self.city_id, "simRunId": sim_run_id}
        return "POST", "/api/v1/reports", body

    # swarm resource_request -> POST /agency-requests (to_agency/capability ids come from the taxonomy; assumed here)
    def agency_request_for(self, rr, to_agency="pcmc-fire"):
        return "POST", "/api/v1/agency-requests", {"incidentId": rr.get("incident_id"), "wardId": self.ward_id, "toAgency": to_agency,
                                                   "capabilityId": self.CAPABILITY.get(rr["needed_kind"], rr["needed_kind"]), "quantity": rr.get("qty", 1),
                                                   "note": rr.get("rationale", "")}

    # the orchestrator's answer: read committed operations and map them to external_resource_update entries
    def operations_request(self):
        return "GET", f"/api/v1/ops?city_id={self.city_id}", None

    def events_request(self, after=None):
        return "GET", "/api/v1/events" + (f"?after={after}" if after else ""), None

    def to_external_update(self, ops_payload, run_id):
        """DisruptionOps /ops rows -> swarm external_resource_update (field names of the response are assumed, not verified live)."""
        resources = []
        for op in (ops_payload or {}).get("operations", []):
            location = op.get("location") or op.get("position")
            if not location:
                continue
            from swarm_ops.integrations.disruptionops_bridge import local_position
            resources.append({"id": str(op.get("resourceId") or op.get("id")), "kind": op.get("kind", "rescue"), "state": "en_route" if op.get("status") != "on_site" else "on_site",
                              "position": local_position(location, self.anchor), "incident_id": op.get("incidentId"), "eta": op.get("etaSeconds")})
        return {"source": "disruptionops", "run_id": run_id, "resources": resources, "handoffs": []}

    def call(self, method, path, body=None):
        self.sent.append((method, path, body))
        if self.dry_run:
            return {"dry_run": True, "method": method, "path": path, "body": body}
        request = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - operator-configured URL
            return json.loads(response.read() or b"{}")


# ----------------------------------------------------------------------------- CLI
def main(argv=None):
    parser = argparse.ArgumentParser(description="Command center agent for the swarm (simulation only).")
    parser.add_argument("--url", default="ws://127.0.0.1:8787/ops/ws")
    parser.add_argument("--scenario", default="pccoe_pawana_night", help="engine scenario id, a JSON task file, or 'random'")
    parser.add_argument("--mode", choices=("orchestrator", "standalone", "orchestrated"), default="orchestrator")
    parser.add_argument("--timeline", default=str(Path(__file__).resolve().parents[1] / ".cache" / "command-center" / "timeline.json"))
    parser.add_argument("--timeout", type=float, default=600.0, help="wall-clock seconds before giving up")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--count", type=int, default=5, help="tasks when falling back to random points")
    parser.add_argument("--real-dry-run", action="store_true", help="print the DisruptionOps REST calls the tasks would map to, then exit")
    args = parser.parse_args(argv)
    tasks = random_scenario(seed=args.seed, count=args.count) if args.scenario == "random" else load_scenario(args.scenario, seed=args.seed, count=args.count)
    if args.real_dry_run:
        adapter = RealModeAdapter(os.environ.get("DISRUPTIONOPS_URL", "http://localhost:8000"), os.environ.get("DISRUPTIONOPS_TOKEN", ""))
        for task in tasks:
            print(json.dumps(adapter.call(*adapter.report_for_task(task)), default=str))
        return 0
    token = os.environ.get("OPS_INTEGRATION_TOKEN", "")
    if not token:
        print("OPS_INTEGRATION_TOKEN is not set; the server and this process must share it.")
        return 2
    center = CommandCenter(args.url, token, tasks, mode=args.mode, timeline_path=args.timeline, timeout_s=args.timeout, scenario=args.scenario)
    try:
        asyncio.run(center.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
