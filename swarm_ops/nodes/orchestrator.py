"""OrchestratorNode: standalone STAND-IN for DisruptionOps' planner and dispatcher.

In production DisruptionOps is the orchestrator: it reads the swarm's ``situation_report`` / ``alert`` messages,
decides which agency units are needed and sends the dispatches (through the integration commands). This node
plays that role when no DisruptionOps is connected, so the whole system still runs end to end. It is disabled
as soon as DisruptionOps connects (``control {orchestrator: "external"}``, an ``orchestrate_incident`` /
``assign`` command or ``external_resource_update``).

How it decides (the reasoning package, moved here from the swarm):

    situation_report --> situation (belief inputs + units it knows from agency ``unit_status`` messages)
        --> swarm_ops.reasoning (needs.py taxonomy demands -> optimizer.py allocation -> planner.py)
        --> labelled fallback (DisruptionOps taxonomy data, then the category template) when it is unavailable
        --> validated plan --> ``dispatch`` / ``cancel`` messages to the owning agency nodes.

It never touches an agency's units: it only sends messages and learns from ``unit_status`` replies.
``AllocationPlanner`` is the pure planning part (also used by the command-center client script).
"""
from __future__ import annotations

import json
import math
import time

from swarm_ops.agents import CATEGORY_TO_DO, DO_TO_SIM, DRONE_ROLES, SUPPLIES, TAXONOMY_PATH, URGENCIES

from .base import Node, node_for_kind

ACTIVE = ("enroute", "awaiting_handoff", "on_scene", "evacuating")
GROUND_SPEED = 9.0
TRANSPORT_KINDS = ("ambulance", "rescue", "boat", "bus")
KIND_SUBSTITUTES = {"rescue": ("boat",), "boat": ("rescue",), "supply_truck": ("water_tanker",), "water_tanker": ("supply_truck",)}


def serves(unit_kind, kind):
    return unit_kind == kind or unit_kind in KIND_SUBSTITUTES.get(kind, ())


def _load_taxonomy():
    """Taxonomy via the engine's reference data (DisruptionOps client: live or assets), else the asset file."""
    try:
        from swarm_ops.engine import reference_data
        taxonomy = reference_data().get("taxonomy")
        if taxonomy:
            return taxonomy
    except ImportError:
        pass
    try:
        return json.loads(TAXONOMY_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return None


def _reasoner():
    """swarm_ops.reasoning.plan_response if the reasoning package provides it, else None."""
    try:
        from swarm_ops.reasoning import plan_response
    except ImportError:
        return None
    if not callable(plan_response):
        return None
    try:
        # One reasoner (with its per-incident memory) per run: incident ids repeat across runs.
        from swarm_ops.reasoning import Reasoner
        instance = Reasoner()
        return lambda situation, context: plan_response(situation, context, reasoner=instance)
    except Exception:  # noqa: BLE001
        return plan_response


def _templates():
    from swarm_ops.engine import OperationsSimulation
    return OperationsSimulation.INCIDENT_TEMPLATES


def _resource_kinds():
    from swarm_ops.engine import RESOURCE_KINDS
    return RESOURCE_KINDS


def _catalog_kinds():
    from swarm_ops.engine import CATALOG_KINDS
    return set(CATALOG_KINDS.values())


class AllocationPlanner:
    """Reasoner + labelled fallback + validation. Pure: situation in, validated plan out."""

    def __init__(self):
        self.taxonomy = _load_taxonomy()
        self.reasoner = _reasoner()
        self.plans = 0
        self.fallbacks = 0

    def plan(self, situation, context, *, hint, owned, known_kinds):
        """-> (plan, source, error, problems, latency_ms). ``plan`` is validated and normalised."""
        plan, source, error = None, "reasoner", None
        started = time.perf_counter()
        if self.reasoner is not None:
            try:
                plan = self.reasoner(situation, context)
            except Exception as exc:  # noqa: BLE001 - the fallback must keep the system working
                error = f"{type(exc).__name__}: {exc}"[:160]
                plan = None
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if not isinstance(plan, dict):
            plan, source = self._fallback_plan(situation, hint, owned, known_kinds), "fallback"
        plan, problems = self._validate(plan, known_kinds)
        self.plans += 1
        if source == "fallback":
            self.fallbacks += 1
        return plan, source, error, problems, latency_ms

    def _fallback_plan(self, situation, hint, owned, known_kinds):
        """FALLBACK only (reasoner unavailable or failed): capability needs from the DisruptionOps taxonomy
        data -> best providing kind; else the category template."""
        if not owned:
            kinds, source = list(hint), "fallback:prescribed"
        else:
            kinds, source = self._taxonomy_kinds(situation, known_kinds), "fallback:disruptionops-taxonomy"
            if not kinds:
                kinds, source = list(hint) or list((_templates().get(situation.get("sim_category"), (None,) * 5)[4]) or []), "fallback:engine-template"
        return {"resource_requests": [{"kind": k, "qty": 1, "urgency": None, "why": source} for k in kinds],
                "drones": [{"role": "assess"}], "rationale": f"{source}: {', '.join(kinds) or 'no ground kind'}", "source": source}

    def _taxonomy_kinds(self, situation, known_kinds):
        tax = self.taxonomy
        if not tax:
            return []
        category = CATEGORY_TO_DO.get(situation.get("category"), situation.get("category"))
        entry = next((c for c in tax.get("incident_categories", []) if c.get("id") == category), None)
        if entry is None:
            return []
        needs = entry.get("needs") or {}
        if isinstance(needs, list):
            needs = {n.get("capability"): n.get("qty", 1) for n in needs}
        out = []
        for capability in needs:
            providers = (tax.get("kinds_providing") or {}).get(capability) or {}
            offers = sorted(((score, DO_TO_SIM.get(kind, kind)) for kind, score in providers.items() if DO_TO_SIM.get(kind, kind) in known_kinds),
                            key=lambda o: (-o[0], o[1]))
            if offers and offers[0][1] not in out:
                out.append(offers[0][1])
        return out

    def _validate(self, plan, known_kinds):
        """Normalise a plan; unknown kinds/roles/payloads are dropped and reported."""
        problems = []
        known = set(known_kinds) | set(_resource_kinds())
        requests = []
        raw = plan.get("agency_requests", plan.get("resource_requests", plan.get("requests", plan.get("required_kinds", []))))
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, str):
                item = {"kind": item}
            if not isinstance(item, dict):
                problems.append(f"request {item!r}")
                continue
            kind = item.get("sim_kind") or DO_TO_SIM.get(item.get("kind"), item.get("kind"))
            if kind not in known:
                problems.append(f"kind {kind!r}")
                continue
            qty = item.get("qty", 1)
            qty = max(1, min(10, int(qty))) if isinstance(qty, (int, float)) and not isinstance(qty, bool) and math.isfinite(qty) else 1
            urgency = item.get("urgency")
            urgency = urgency if urgency in URGENCIES else {"immediate": "critical", "soon": "high", "standby": "normal"}.get(urgency)
            entry = next((r for r in requests if r["kind"] == kind), None)
            if entry:
                entry["qty"] = max(entry["qty"], qty)
            else:
                requests.append({"kind": kind, "qty": qty, "urgency": urgency, "why": str(item.get("why") or item.get("rationale") or "")[:160]})
        drones = []
        for item in plan.get("drones", []) if isinstance(plan.get("drones"), list) else []:
            if not isinstance(item, dict) or item.get("role") not in DRONE_ROLES:
                problems.append(f"drone {item!r}")
                continue
            payload = None if item.get("payload") in (None, "none") else item.get("payload")
            if payload is not None and payload not in SUPPLIES:
                problems.append(f"payload {payload!r}")
                payload = None
            drones.append({"role": item["role"], "payload": payload, "count": max(1, min(3, int(item.get("count", 1) or 1)))})
        severity = plan.get("severity", (plan.get("assessment") or {}).get("severity"))
        severity = max(1, min(5, int(severity))) if isinstance(severity, (int, float)) and not isinstance(severity, bool) else None
        escalation = plan.get("escalation") if isinstance(plan.get("escalation"), str) else None
        deltas = None
        if isinstance(plan.get("requests_delta"), list):
            deltas = []
            for item in plan["requests_delta"]:
                kind = DO_TO_SIM.get((item or {}).get("sim_kind") or (item or {}).get("kind"), (item or {}).get("sim_kind") or (item or {}).get("kind"))
                delta = (item or {}).get("delta")
                if kind not in known or not isinstance(delta, int) or isinstance(delta, bool) or delta == 0:
                    problems.append(f"delta {item!r}"[:60])
                    continue
                why = next((s.get("why") for s in plan.get("stand_down") or [] if DO_TO_SIM.get(s.get("kind"), s.get("kind")) == kind), None) if delta < 0 else None
                deltas.append({"kind": kind, "delta": max(-10, min(10, delta)), "why": str(why or "")[:160]})
        return {"resource_requests": requests, "requests_delta": deltas, "drones": drones, "severity": severity, "escalation": escalation,
                "rationale": str(plan.get("rationale") or "")[:240], "source": str(plan.get("source") or "")[:60] or None}, problems


class OrchestratorNode(Node):
    id = "command_center"
    label = "OrchestratorNode (stand-in for DisruptionOps)"
    cadence = 2.0

    def __init__(self, runtime):
        super().__init__(runtime)
        self.enabled = True
        self.planner = AllocationPlanner()
        self.units = {}        # unit id -> last known unit_status body (+ "t")
        self.ledger = {}       # incident id -> orders and what is known about the response
        self.scenes = {}       # incident id -> last situation_report
        self.situations = {}   # incident id -> last situation given to the planner (for joint planning)
        self.alerts = []
        self.known_kinds_extra = set()

    # reasoner is exposed so tests can inject one
    @property
    def reasoner(self):
        return self.planner.reasoner

    @reasoner.setter
    def reasoner(self, value):
        self.planner.reasoner = value

    def set_enabled(self, enabled, reason=""):
        if bool(enabled) == self.enabled:
            return
        self.enabled = bool(enabled)
        self.sim.decisions.record("orchestrator", "orchestrator_" + ("enabled" if enabled else "disabled"), reason or ("internal orchestrator on" if enabled else "DisruptionOps is the orchestrator"))

    # ------------------------------------------------------------------ inbox
    def handle(self, message):
        body = message.get("body") or {}
        kind = message["kind"]
        if kind == "report" and body.get("intake"):
            self.on_intake(message, body)
        elif kind == "situation_report":
            self.on_report(message, body)
        elif kind in ("unit_status", "handoff"):
            self.on_unit_status(message, body)
        elif kind == "alert":
            self.alerts.append({k: message.get(k) for k in ("t", "incident_id", "text")} | {"alert": body.get("alert")})
            self.alerts = self.alerts[-50:]
        elif kind == "decline" and body.get("dispatch_id"):
            self.sim.decisions.record("orchestrator", "dispatch_waiting", message["text"], incident_id=message.get("incident_id"), decision_source="rule")

    def _ledger(self, iid):
        return self.ledger.setdefault(iid, {"required": [], "completed": [], "requests": [], "owned": False, "hint": [], "assessed": False, "seq": 0})

    def on_intake(self, message, body):
        iid = message["incident_id"]
        led = self._ledger(iid)
        led.update(owned=bool(body.get("plan_owned")), hint=list(body.get("required_hint") or []), category=body.get("category"),
                   position=body.get("position"), severity=body.get("severity"), people=body.get("people"))
        if not self.enabled or body.get("controlled_by") == "disruptionops":
            return
        if not led["owned"]:
            # The intake classified the report with its standard response: dispatch those kinds at once (before any aircraft looks).
            for kind in led["hint"]:
                self.raise_kind(iid, kind, 1, f"initial report: {body.get('category')}, {body.get('people')} people", "report")

    def on_unit_status(self, message, body):
        t = self.sim.t
        for entry in body.get("roster") or [body]:
            unit_id = entry.get("unit_id")
            if not unit_id:
                continue
            record = {**self.units.get(unit_id, {}), **{k: v for k, v in entry.items() if v is not None or k in ("incident_id", "eta_s")}, "t": t}
            self.units[unit_id] = record
            iid = entry.get("incident_id")
            led = self.ledger.get(iid)
            if led is not None:
                request = next((r for r in led["requests"] if r["request_id"] == entry.get("dispatch_id")), None)
                if request is not None and unit_id not in request["fulfilled_by"] and entry.get("phys_state") == "enroute":
                    request["fulfilled_by"].append(unit_id)
                    if len(request["fulfilled_by"]) >= request["qty"]:
                        request["status"] = "fulfilled"
            released = entry.get("released_from")
            if entry.get("outcome") == "completed" and released in self.ledger:
                led = self.ledger[released]
                served = [k for k in led["required"] if serves(entry.get("kind"), k) and k not in led["completed"]][:1]
                for kind in served or ([entry.get("kind")] if entry.get("kind") not in led["completed"] else []):
                    led["completed"].append(kind)

    # ------------------------------------------------------------------ planning
    def on_report(self, message, report):
        iid = message["incident_id"]
        led = self._ledger(iid)
        self.scenes[iid] = report
        if not self.enabled or report.get("controlled_by") == "disruptionops":
            return
        scene = report["scene"]
        if scene.get("status") == "resolved":
            self.situations.pop(iid, None)
            return
        led.setdefault("position", scene.get("position"))
        situation = self._situation(report, led)
        self.situations[iid] = situation
        known = self.known_kinds()
        plan, source, error, problems, latency = self.planner.plan(situation, self._context(iid), hint=led["hint"], owned=led["owned"], known_kinds=known)
        reason = f"situation r{report.get('revision')} ({report.get('reason')})"
        if (led["owned"] and not plan["resource_requests"] and not led["required"] and led["hint"] and plan.get("escalation") not in (None, "none", "monitor")
                and plan.get("requests_delta") in (None, [])):
            # The planner found no unit free right now; queue the standard response for the category so the first unit to free up goes.
            plan["resource_requests"] = [{"kind": k, "qty": 1, "urgency": None, "why": "standard response queued: no unit free for the planner"} for k in led["hint"]]
            plan["requests_delta"] = None
        applied = self.apply_plan(iid, plan, source, reason, report)
        applied += self.check_capacity(iid, report, led)
        why = plan.get("rationale") or ", ".join(f"{q['qty']}x {q['kind']}" for q in plan["resource_requests"]) or "no ground unit needed"
        if error:
            why = f"reasoner error ({error}); {why}"
        elif source == "fallback" and self.planner.reasoner is None:
            why = f"reasoner unavailable; {why}"
        if problems:
            why += f"; dropped invalid plan items: {'; '.join(problems[:3])}"
        self.sim.decisions.record("orchestrator", "plan_response", f"{reason}: {why}",
                                  [{"id": q["kind"], "score": q["qty"]} for q in plan["resource_requests"]], fallback=source == "fallback",
                                  incident_id=iid, decision_source="reasoner" if source == "reasoner" else "fallback",
                                  plan_source=plan.get("source") or source, applied=applied, latency_ms=latency,
                                  inputs={"people": report["people"]["estimate"], "injured": report["injured"]["estimate"], "trend": report["trend"],
                                          "briefing_revision": scene.get("briefing_revision"), "severity": scene.get("severity")},
                                  factors={"hazards": [h["kind"] for h in report["hazards"]], "escalation": plan.get("escalation")})
        led["plan"] = {"source": plan.get("source") or source, "at": round(self.sim.t, 2), "reason": reason, "rationale": plan["rationale"],
                       "resource_requests": plan["resource_requests"], "escalation": plan["escalation"]}
        if applied:
            self.send("swarm", "ack", f"Plan for {iid}: " + ", ".join(applied), iid, report.get("request_id"), {"plan": led["plan"], "applied": applied})

    def known_kinds(self):
        kinds = {u.get("kind") for u in self.units.values() if u.get("kind")}
        return kinds | _catalog_kinds() | self.known_kinds_extra

    def _units_for(self, report, led):
        iid = report["incident_id"]
        pos = (report.get("scene") or {}).get("position") or led.get("position") or [0, 0]
        out = []
        for u in self.units.values():
            state = u.get("phys_state")
            assigned = u.get("incident_id") == iid
            if state not in ("available", *ACTIVE) or (state != "available" and not assigned):
                continue
            speed = 7.0 if u.get("transport") == "boat" else GROUND_SPEED
            if state in ("awaiting_handoff", "on_scene"):
                eta = 0.0
            elif assigned and u.get("eta_s") is not None:
                eta = max(0.0, u["eta_s"] - (self.sim.t - u["t"]))
            else:
                p = u.get("position") or [0, 0]
                eta = (math.hypot(p[0] - pos[0], p[1] - pos[1]) * 1.3 + (u.get("approach_m") or 0)) / speed
            out.append({"id": u["unit_id"], "kind": u["kind"], "state": state, "eta_s": round(eta, 1), "capacity": u.get("capacity"), "assigned": assigned})
        return sorted(out, key=lambda u: (not u["assigned"], u["eta_s"], u["id"]))[:12]

    def _situation(self, report, led):
        sit = dict(report["scene"])
        sit.update(units=self._units_for(report, led), required_kinds=list(led["required"]), completed_kinds=list(led["completed"]),
                   open_requests=[{k: q[k] for k in ("request_id", "qty", "status")} | {"needed_kind": q["kind"]} for q in led["requests"] if q["status"] == "open"],
                   drones_available=[], drones_assigned=list(report.get("drones_on_scene") or []))
        return sit

    def _context(self, iid):
        to_do = {"rescue": "rescue_team", "engineering": "jcb"}
        resources = []
        for u in self.units.values():
            if not u.get("kind"):
                continue
            speed = 7.0 if u.get("transport") == "boat" else GROUND_SPEED
            resources.append({"id": u["unit_id"], "kind": to_do.get(u["kind"], u["kind"]), "state": u.get("phys_state", "available"), "capacity": u.get("capacity"),
                              "name": u.get("name") or u["unit_id"], "position": [round(float(v), 1) for v in (u.get("position") or [0, 0])[:2]],
                              "approach_s": round((u.get("approach_m") or 0) / speed, 1), "assigned_to": u.get("incident_id") if u.get("phys_state") in ACTIVE else None})
        # DisruptionOps' own unit catalog: mutual-aid units of a kind no registered agency unit has yet.
        have = {u.get("kind") for u in self.units.values()}
        try:
            from swarm_ops.engine import CATALOG_KINDS, _agency_catalog
            for entry in _agency_catalog().get("resources", []):
                kind = CATALOG_KINDS.get(entry.get("kind"))
                if kind and kind not in have and entry.get("local_xy") and entry.get("source_id") not in self.units:
                    resources.append({"id": entry["source_id"], "kind": to_do.get(kind, kind), "state": "available", "capacity": entry.get("capacity"),
                                      "name": entry.get("name"), "position": [round(float(v), 1) for v in entry["local_xy"][:2]], "approach_s": 0.0, "assigned_to": None})
        except Exception:  # noqa: BLE001
            pass
        fleet = {}
        for r in resources:
            entry = fleet.setdefault(r["kind"], {"available": 0, "total": 0})
            entry["total"] += 1
            entry["available"] += r["state"] == "available"
        concurrent = [s for i, s in self.situations.items() if i != iid and (self.scenes.get(i, {}).get("scene") or {}).get("status") != "resolved"]
        return {"t_s": round(self.sim.t, 2), "resources": resources, "concurrent": concurrent, "fleet": fleet,
                "mutual_aid_kinds": sorted(_catalog_kinds()), "world_id": getattr(self.sim, "world_id", None)}

    # -- ledger operations (the orchestrator's own record of what it ordered)
    def pending(self, led, kind):
        return sum(max(0, r["qty"] - len(r["fulfilled_by"])) for r in led["requests"] if r["kind"] == kind and r["status"] == "open")

    def committed(self, iid, kind):
        return sum(1 for u in self.units.values() if u.get("incident_id") == iid and u.get("phys_state") in ACTIVE and serves(u.get("kind"), kind))

    def raise_kind(self, iid, kind, qty, why, stage):
        led = self._ledger(iid)
        scene = (self.scenes.get(iid) or {}).get("scene") or {}
        severity = scene.get("severity") or led.get("severity") or 3
        led["seq"] += 1
        request = {"request_id": f"{iid}-RR{led['seq']}", "kind": kind, "qty": qty, "urgency": "critical" if severity >= 5 else "high" if severity >= 4 else "normal",
                   "stage": stage, "status": "open", "fulfilled_by": [], "raised_at": round(self.sim.t, 2), "rationale": why}
        led["requests"].append(request)
        if kind not in led["required"]:
            led["required"].append(kind)
        node = node_for_kind(kind)
        position = scene.get("position") or led.get("position")
        self.send(node, "dispatch", f"Dispatch {qty}x {kind} to {iid}: {why}.", iid, request["request_id"],
                  {"dispatch_id": request["request_id"], "kind": kind, "qty": qty, "urgency": request["urgency"], "stage": stage, "rationale": why,
                   "position": position, "briefing_revision": scene.get("briefing_revision"), "category": led.get("category"),
                   "required_kinds": list(led["required"]), "plan": (led.get("plan") or {}).get("source")})
        return request

    def stand_down(self, iid, kind, qty, why):
        led = self._ledger(iid)
        changes = []
        for r in reversed(led["requests"]):
            if qty <= 0:
                break
            if r["kind"] != kind or r["status"] != "open":
                continue
            spare = r["qty"] - len(r["fulfilled_by"])
            cut = min(spare, qty)
            qty -= cut
            r["qty"] -= cut
            if r["qty"] <= len(r["fulfilled_by"]):
                r["status"] = "cancelled" if not r["fulfilled_by"] else "fulfilled"
            self.send(node_for_kind(kind), "cancel", f"Stand down {cut}x {kind} on {r['request_id']}: {why}.", iid, r["request_id"],
                      {"dispatch_id": r["request_id"], "kind": kind, "qty": cut})
            changes.append(f"-{cut}x {kind}")
        if kind in led["required"] and kind not in led["completed"] and not self.committed(iid, kind) and not self.pending(led, kind):
            led["required"].remove(kind)
        return changes

    def apply_plan(self, iid, plan, source, reason, report):
        """Apply a validated plan to the ledger and send the dispatch / cancel messages (ported from the engine)."""
        led = self._ledger(iid)
        owned = led["owned"]
        changes = []
        wanted = {q["kind"]: q for q in plan["resource_requests"]}

        def raise_kind(kind, qty, why):
            first = not any(r["kind"] == kind for r in led["requests"])
            if qty > 0:
                self.raise_kind(iid, kind, qty, why, "report" if first else "plan")
                changes.append(f"+{qty}x {kind}")
            elif kind not in led["required"]:
                led["required"].append(kind)

        if plan.get("requests_delta") is not None:
            for item in plan["requests_delta"]:
                kind, delta = item["kind"], item["delta"]
                why = (wanted.get(kind) or {}).get("why") or item.get("why") or f"{source} plan"
                if delta > 0:
                    if kind not in led["required"] and not owned:
                        continue
                    already = self.committed(iid, kind) + self.pending(led, kind)
                    total = (wanted.get(kind) or {}).get("qty", already + delta)
                    if total > already:
                        raise_kind(kind, total - already, why)
                elif delta < 0:
                    changes += self.stand_down(iid, kind, -delta, why)
        elif owned and (wanted or source != "fallback"):
            for kind in [k for k in led["required"] if k not in wanted and k not in led["completed"]]:
                if self.committed(iid, kind):
                    continue
                changes += self.stand_down(iid, kind, self.pending(led, kind), f"no longer needed ({reason})")
                if kind in led["required"]:
                    led["required"].remove(kind)
            for kind, q in wanted.items():
                if q["qty"] > self.committed(iid, kind) + self.pending(led, kind) or kind not in led["required"]:
                    raise_kind(kind, max(1, q["qty"] - self.committed(iid, kind) - self.pending(led, kind)), q["why"] or f"{source} plan")
        elif not owned:
            for kind, q in wanted.items():
                if kind in led["required"] and q["qty"] > 1 and q["qty"] > self.committed(iid, kind) + self.pending(led, kind):
                    raise_kind(kind, q["qty"] - self.committed(iid, kind) - self.pending(led, kind), q["why"] or f"{source} plan (escalation)")
        return changes

    def check_capacity(self, iid, report, led):
        """First aircraft look: people exceed the seats on the way -> order one more of that kind."""
        if led["assessed"] or not report.get("drones_on_scene"):
            return []
        led["assessed"] = True
        changes = []
        people = report["people"]["estimate"]
        for kind in list(led["required"]):
            if kind not in TRANSPORT_KINDS:
                continue
            units = [u for u in self.units.values() if u.get("incident_id") == iid and u.get("phys_state") in ACTIVE and serves(u.get("kind"), kind)]
            capacity = sum(u.get("capacity") or 0 for u in units)
            if units and people > capacity and not self.pending(led, kind) and not any(r["kind"] == kind and r["stage"] == "assessment" for r in led["requests"]):
                rev = (report.get("scene") or {}).get("briefing_revision")
                self.raise_kind(iid, kind, 1, f"briefing r{rev}: {people} people exceed {capacity} seats on the way", "assessment")
                changes.append(f"+1x {kind}")
        return changes
