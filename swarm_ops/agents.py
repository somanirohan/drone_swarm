"""Agents of the operations swarm: drones, the swarm coordinator, and the bus between them.

Everything here is deterministic and offline (no LLM in the decision path).

* ``MessageBus``: every message between agents and to/from the command centre (orchestrator)
  is a record ``{id, t, from, to, kind, incident_id, request_id, text, body}``. Kinds:
  task | accept | decline | report | status | resource_request | ack | cancel | handoff.
* ``DecisionLog``: every decision is a trace ``{t, agent, decision, reason, options:[{id, score}]}``;
  ``fallback: true`` marks decisions taken by a fallback rule instead of the reasoner.
* ``DroneAgent``: perceive -> decide (priority behaviour tree) -> act, with memory of its zone,
  sweep, commitments and the briefing revisions it produced. It evaluates its own bid for a task
  (route, recovery route, energy) and accepts or declines an award.
* ``SwarmCoordinator``: the swarm-as-agency brain. It decides on orchestrator requests, runs the
  single-item auction over drone bids, and plans each incident's response through ONE interface,
  ``plan_response(incident)``: it builds a ``situation`` from the evidence (reports, drone and
  ground observations, units with ETAs, lifelines) and calls ``swarm_ops.reasoning.plan_response
  (situation, context)``. When that is unavailable or raises, a labelled fallback derives the
  needed unit kinds from the DisruptionOps taxonomy data (capability needs -> best providing kind),
  then from the engine's category templates. Plans are validated before they are applied.
"""
from __future__ import annotations

import json
import math
import time
from collections import deque
from pathlib import Path

MESSAGE_KINDS = ("task", "accept", "decline", "report", "status", "resource_request", "ack", "cancel", "handoff")
URGENCIES = ("critical", "high", "normal", "low")
DRONE_ROLES = ("assess", "supply", "relay", "overwatch", "search")
SUPPLIES = ("first_aid", "medical", "food", "water")
TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "assets" / "operations" / "disruptionops-taxonomy.json"
# DisruptionOps kind <-> simulator kind.
DO_TO_SIM = {"rescue_team": "rescue", "jcb": "engineering"}
SIM_TO_DO = {v: k for k, v in DO_TO_SIM.items()}
# Simulator categories that are not DisruptionOps ids (used only to look up taxonomy needs).
CATEGORY_TO_DO = {"flood": "person_stranded", "trapped": "structural_damage", "medical": "heat_casualty",
                  "blocked_route": "fallen_tree"}


def _distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _r(value, digits=2):
    return round(float(value), digits)


class MessageBus:
    """In-process bus. Keeps the last ``keep`` messages for snapshots, plus per-agent inboxes."""

    def __init__(self, clock, keep=200):
        self._clock = clock
        self.log = deque(maxlen=keep)
        self.inboxes = {}
        self._seq = 0

    def post(self, sender, recipient, kind, text, incident_id=None, request_id=None, body=None):
        if kind not in MESSAGE_KINDS:
            raise ValueError(f"unknown message kind {kind!r}")
        self._seq += 1
        message = {"id": self._seq, "t": _r(self._clock()), "from": str(sender), "to": str(recipient), "kind": kind,
                   "incident_id": incident_id, "request_id": request_id, "text": str(text)[:240], "body": body or {}}
        self.log.append(message)
        inbox = self.inboxes.setdefault(str(recipient), deque(maxlen=50))
        inbox.append(message)
        return message

    def drain(self, agent_id):
        inbox = self.inboxes.get(agent_id)
        if not inbox:
            return []
        out = list(inbox); inbox.clear()
        return out

    def recent(self, limit=200):
        return list(self.log)[-limit:]


class DecisionLog:
    def __init__(self, clock, keep=100):
        self._clock = clock
        self.log = deque(maxlen=keep)
        self.by_agent = {}
        self._seq = 0

    def record(self, agent, decision, reason, options=(), fallback=False, incident_id=None, **extra):
        self._seq += 1
        agent = str(agent)
        entry = {"id": f"dec-{self._seq}", "t": _r(self._clock()), "agent": agent, "decision": decision, "kind": decision,
                 "reason": str(reason)[:240], "summary": str(reason)[:240],
                 "options": [{"id": str(o["id"]), "score": None if o.get("score") is None else _r(o["score"], 3)} for o in options][:8],
                 "incident_id": incident_id, "drone_id": agent if agent.startswith("D") else extra.pop("drone_id", None),
                 "fallback": bool(fallback), "source": "fallback" if fallback else extra.pop("decision_source", "rule" if agent != "swarm" else "swarm")}
        entry.update(extra)
        self.log.append(entry)
        self.by_agent[str(agent)] = entry
        return entry

    def recent(self, limit=100):
        return list(self.log)[-limit:]


# ---------------------------------------------------------------- behaviour tree (tiny, deterministic)
class Sequence:
    def __init__(self, name, *children):
        self.name, self.children = name, children

    def tick(self, agent, sim, delta):
        for child in self.children:
            if not child.tick(agent, sim, delta):
                return False
        return True


class Selector:
    def __init__(self, name, *children):
        self.name, self.children = name, children

    def tick(self, agent, sim, delta):
        for child in self.children:
            if child.tick(agent, sim, delta):
                agent.memory["active_node"] = getattr(child, "name", "")
                return True
        return False


class Condition:
    def __init__(self, name, test):
        self.name, self.test = name, test

    def tick(self, agent, sim, delta):
        return bool(self.test(agent, sim))


class Action:
    def __init__(self, name, run):
        self.name, self.run = name, run

    def tick(self, agent, sim, delta):
        result = self.run(agent, sim, delta)
        return True if result is None else bool(result)


def _arrived(agent, sim):
    return agent.drone["state"] == "enroute" and not agent.drone["route"] and agent.incident(sim) is not None


DRONE_TREE = Selector(
    "root",
    Sequence("deliver_after_handoff", Condition("arrived_retasked", lambda a, s: _arrived(a, s) and a.drone.get("retasked") == "supply"),
             Action("drop_and_return", lambda a, s, d: a.drop_and_return(s))),
    Sequence("assess_on_arrival", Condition("arrived", _arrived), Action("observe", lambda a, s, d: a.start_observing(s))),
    Sequence("assess", Condition("observing", lambda a, s: a.drone["state"] == "observing" and a.incident(s) is not None),
             Action("hold_station", lambda a, s, d: a.hold_station(s))),
    Sequence("land", Condition("home", lambda a, s: a.drone["state"] == "returning" and not a.drone["route"]),
             Action("land_and_reload", lambda a, s, d: a.land(s))),
    Sequence("charge", Condition("charging", lambda a, s: a.drone["state"] == "charging"), Action("charge", lambda a, s, d: a.charge(s, d))),
    Sequence("search", Condition("sweep_done", lambda a, s: a.drone["state"] == "patrolling" and not a.drone["route"]),
             Action("next_sweep", lambda a, s, d: a.next_sweep(s))),
)


class DroneAgent:
    """One aircraft: perceives its own state and inbox, decides with DRONE_TREE, acts on the shared world."""

    def __init__(self, drone, bus, decisions):
        self.drone = drone
        self.id = drone["id"]
        self.bus = bus
        self.decisions = decisions
        self.memory = {"commitment": None, "briefings": {}, "sweeps": 0, "awards": 0, "declines": 0, "active_node": None}

    # perception
    def incident(self, sim):
        return sim._find_incident(self.drone["task_id"])

    def perceive(self, sim):
        messages = self.bus.drain(self.id)
        for message in messages:
            if message["kind"] == "cancel" and message.get("incident_id") == self.drone.get("task_id"):
                self.memory["commitment"] = None
        self.memory["zone_size"] = len(self.drone.get("zone", []))
        self.memory["search_target"] = self.drone.get("search_target")
        return messages

    def decide(self, decision, reason, options=(), incident_id=None, **extra):
        entry = self.decisions.record(self.id, decision, reason, options, incident_id=incident_id, **extra)
        self.drone["last_decision"] = {k: entry[k] for k in ("t", "decision", "reason")}
        return entry

    def tick(self, sim, delta):
        self.perceive(sim)
        DRONE_TREE.tick(self, sim, delta)

    # bidding (the aircraft evaluates the task itself)
    def bid(self, sim, incident, route_planner):
        drone = self.drone
        if not drone["active"] or not drone["link"] or drone["state"] not in ("ready", "patrolling"):
            return {"ok": False, "reason": "not available"}
        route = route_planner(drone, incident["position"])
        recovery = sim._drone_path({**drone, "position": incident["position"]}, drone.get("home", sim._base))
        if route is None or recovery is None:
            return {"ok": False, "reason": "no permitted route"}
        travel = sim._route_distance(drone["position"], route)
        required = (travel + sim._route_distance(incident["position"], recovery)) * 0.018 + 7 + 22
        if drone["energy_wh"] < required:
            return {"ok": False, "reason": f"energy {drone['energy_wh']:.0f} Wh < {required:.0f} Wh needed"}
        supplies = [need for need in incident["needs"] if need in ("first_aid", "medical", "food") and need not in incident["supplied"]]
        wanted = [p for p in (incident.get("drone_plan") or {}).get("payloads", []) if p in supplies] or supplies
        compatible = drone["payload"] in wanted and drone["payload_stock"] > 0
        # Bid: ETA seconds plus a share of remaining energy this task would consume.
        value = travel / 18 + 20 * required / max(1e-6, drone["energy_wh"])
        return {"ok": True, "bid": value, "route": route, "required_wh": required, "travel_m": travel,
                "mismatch": 0 if compatible or not supplies else 1}

    def accept(self, sim, incident, offer, purpose):
        drone = self.drone
        was_searching = drone["state"] == "patrolling"
        drone.update(state="enroute", task_id=incident["id"], route=offer["route"], delivered=False, coverage_task=None, search_target=None)
        self.memory["commitment"] = incident["id"]; self.memory["awards"] += 1
        self.bus.post(self.id, "swarm", "accept", f"{self.id} accepts {incident['id']} ({purpose}).", incident["id"],
                      incident.get("request_id"), {"bid": _r(offer["bid"], 3), "required_wh": _r(offer["required_wh"], 1)})
        self.decide("accept_task", f"{purpose}; bid {offer['bid']:.1f} (ETA {offer['travel_m'] / 18:.0f} s, needs {offer['required_wh']:.0f} of {drone['energy_wh']:.0f} Wh)",
                    incident_id=incident["id"])
        return was_searching

    # actions
    def drop_and_return(self, sim):
        drone, incident = self.drone, self.incident(sim)
        if drone["payload_stock"] > 0 and drone["payload"] in incident["needs"] and drone["payload"] not in incident["supplied"]:
            incident["supplied"].append(drone["payload"]); drone["delivered"] = True; drone["payload_stock"] -= 1; drone["delivered_at"] = sim.t
            sim._event("supply", f"{drone['id']} simulated {drone['payload']} delivery to the ground crew; incident remains open.", incident["id"])
            sim._signal("ack", "drone", drone["id"], [incident["id"], *[r for r in incident["resource_ids"]]], drone["position"], incident["id"], f"{drone['id']} dropped {drone['payload']} at {incident['id']}.")
            self.bus.post(self.id, "swarm", "report", f"{self.id} dropped {drone['payload']} at {incident['id']}.", incident["id"], incident.get("request_id"), {"supplied": drone["payload"]})
        self.decide("return", "supply drop complete; ground crew holds the scene", incident_id=incident["id"])
        sim._return(drone, "supply drop complete; ground crew holds the scene")

    def start_observing(self, sim):
        drone, incident = self.drone, self.incident(sim)
        drone["state"] = "observing"
        incident["status"] = "observing" if not incident["handed_over"] else incident["status"]
        sim._brief(incident, drone)
        first_look = incident["observations"] <= 1
        dropped = None
        if drone["payload_stock"] > 0 and drone["payload"] in incident["needs"] and drone["payload"] not in incident["supplied"]:
            incident["supplied"].append(drone["payload"]); drone["delivered"] = True; drone["payload_stock"] -= 1; drone["delivered_at"] = sim.t
            dropped = drone["payload"]
            sim._event("supply", f"{drone['id']} simulated {drone['payload']} delivery; incident remains open.", incident["id"])
            sim._signal("ack", "drone", drone["id"], [incident["id"]], drone["position"], incident["id"], f"{drone['id']} dropped {drone['payload']} at {incident['id']}.")
        sim._brief(incident, drone)
        self.memory["briefings"][incident["id"]] = incident["briefing"]["revision"]
        self.decide("assess", f"on station at {incident['id']}; briefing r{incident['briefing']['revision']}" + (f", dropped {dropped}" if dropped else ""),
                    incident_id=incident["id"])
        self.bus.post(self.id, "swarm", "report", f"{self.id} on scene at {incident['id']}: {incident['people']} people, hazards {', '.join(incident['briefing']['hazards'])}.",
                      incident["id"], incident.get("request_id"), {"briefing_revision": incident["briefing"]["revision"], "people": incident["people"],
                                                                    "confidence": incident["briefing"]["confidence"], "supplied": dropped})
        sim._observe_evidence(incident, drone)
        sim._drone_arrival_brief(drone, incident, first_look)

    def hold_station(self, sim):
        drone, incident = self.drone, self.incident(sim)
        if sim.t - (incident["last_observed"] or 0) > 4:
            before = (incident["briefing"] or {}).get("revision")
            sim._brief(incident, drone)
            if incident["briefing"]["revision"] != before:
                sim._observe_evidence(incident, drone)
        if drone["payload_stock"] == 0 and sim.t - drone.get("delivered_at", sim.t) > 8:
            self.decide("return", "supplies exhausted; return to reload while the incident stays open", incident_id=incident["id"])
            sim._return(drone, "supplies exhausted; returning to reload while incident and agency response remain open")

    def land(self, sim):
        drone = self.drone
        drone["state"] = "charging"
        if drone["payload_stock"] == 0:
            drone["payload_stock"] = drone["payload_capacity"]
            sim._event("reload", f"{drone['id']} reloaded {drone['payload']} at {drone['dock_id'] if drone.get('dock_id') else 'the operations base'}.")
        self.memory["commitment"] = None
        self.decide("charge", "landed; recharge and reload")
        self.bus.post(self.id, "swarm", "status", f"{self.id} landed at {drone.get('dock_id') or 'base'}; charging.", body={"battery": _r(drone["battery"], 3)})

    def charge(self, sim, delta):
        drone = self.drone
        drone["energy_wh"] = min(drone["capacity_wh"], drone["energy_wh"] + delta * .7)
        drone["battery"] = drone["energy_wh"] / drone["capacity_wh"]
        if drone["battery"] > .85:
            drone["state"] = "ready"
            self.decide("ready", f"battery {drone['battery']:.0%}; available for tasks")

    def next_sweep(self, sim):
        drone = self.drone
        drone.update(coverage_task=None, search_target=None)
        if drone["battery"] >= .4 and sim._next_search(drone):
            self.memory["sweeps"] += 1
            self.decide("search", f"sweep the most likely cell of own zone ({drone['search_target']})")
        else:
            self.decide("return", "search zone swept" if drone["battery"] >= .4 else "battery below search threshold")
            sim._return(drone, "search zone swept")


# ---------------------------------------------------------------- coordinator
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
        # One reasoner (with its per-incident memory) per simulation run: incident ids repeat across runs.
        from swarm_ops.reasoning import Reasoner
        instance = Reasoner()
        return lambda situation, context: plan_response(situation, context, reasoner=instance)
    except Exception:
        return plan_response


class SwarmCoordinator:
    """The swarm-as-agency brain (see module docstring)."""

    id = "swarm"

    def __init__(self, sim, bus, decisions):
        self.sim = sim
        self.bus = bus
        self.decisions = decisions
        self.taxonomy = _load_taxonomy()
        self.reasoner = _reasoner()
        self.plans = 0
        self.fallbacks = 0

    # --- auction over the drones' own bids ---
    def award(self, incident, candidates, agents, supply_only):
        sim = self.sim
        offers, declined = [], []
        route_planner = lambda drone, target: sim._drone_path(drone, target, standoff=sim.STANDOFF_M, observe=True)
        for drone in candidates:
            agent = agents.get(drone["id"])
            if agent is None:
                continue
            offer = agent.bid(sim, incident, route_planner)
            if offer["ok"]:
                offers.append((offer["mismatch"], round(offer["bid"], 6), drone["id"], agent, offer))
            elif offer["reason"] != "not available":
                declined.append((drone["id"], offer["reason"]))
        if not offers:
            if declined:
                self.decisions.record(self.id, "no_feasible_bid", "; ".join(f"{d}: {r}" for d, r in declined[:4]), incident_id=incident["id"])
            return None
        offers.sort(key=lambda o: o[:3])
        mismatch, bid, _, agent, offer = offers[0]
        purpose = f"bringing {agent.drone['payload']}" if supply_only else "first assessment"
        self.bus.post(self.id, agent.id, "task", f"Award {incident['id']} to {agent.id} ({purpose}); winning bid {bid:.1f} of {len(offers)}.",
                      incident["id"], incident.get("request_id"), {"role": "supply" if supply_only else "assess", "bid": _r(bid, 3)})
        self.decisions.record(self.id, "award", f"{agent.id} lowest feasible bid for {incident['id']} ({purpose})"
                              + ("; payload mismatch, reconnaissance only" if mismatch else ""),
                              [{"id": o[2], "score": o[1] + 1000 * o[0]} for o in offers], incident_id=incident["id"])
        return mismatch, bid, agent, offer, len(offers)

    # --- inbound orchestrator requests ---
    def request_received(self, request, source):
        self.bus.post("command_center", self.id, "task",
                      f"agency_request {request['request_id']}: {request['kind']} {request['category']} ({request['people']} people, priority {request['priority']}).",
                      None, request["request_id"], {k: request.get(k) for k in ("kind", "category", "people", "needs", "priority", "position")} | {"source": source})

    def request_decided(self, request, result, checks):
        accepted = result["accepted"]
        self.decisions.record(self.id, "accept_request" if accepted else "decline_request", result["reason"],
                              [{"id": name, "score": 1.0 if ok else 0.0} for name, ok in checks], incident_id=result.get("incident_id"),
                              request_id=request["request_id"])
        self.bus.post(self.id, "command_center", "accept" if accepted else "decline",
                      f"{'Accepted' if accepted else 'Declined'} {request['request_id']}: {result['reason']}.", result.get("incident_id"), request["request_id"],
                      {k: result.get(k) for k in ("eta_s", "assigned_drones", "plan", "merged")})

    # --- response planning (one interface) ---
    def situation(self, incident):
        return self.sim._situation(incident)

    def plan_response(self, incident, reason="evidence changed"):
        """Plan (or re-plan) one incident's response from its situation; apply it after validation."""
        sim = self.sim
        situation = self.situation(incident)
        incident["situation"] = situation
        context = sim._planning_context()
        plan, source, error = None, "reasoner", None
        started = time.perf_counter()
        if self.reasoner is not None:
            try:
                plan = self.reasoner(situation, context)
            except Exception as exc:  # the fallback must keep the swarm working
                error = f"{type(exc).__name__}: {exc}"[:160]
                plan = None
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        if not isinstance(plan, dict):
            plan, source = self._fallback_plan(incident, situation), "fallback"
        plan, problems = self._validate(plan)
        self.plans += 1
        if source == "fallback":
            self.fallbacks += 1
        applied = sim._apply_plan(incident, plan, source, reason)
        why = plan.get("rationale") or ", ".join(f"{q['qty']}x {q['kind']}" for q in plan["resource_requests"]) or "no ground unit needed"
        if error:
            why = f"reasoner error ({error}); {why}"
        elif source == "fallback" and self.reasoner is None:
            why = f"reasoner unavailable; {why}"
        if problems:
            why += f"; dropped invalid plan items: {'; '.join(problems[:3])}"
        self.decisions.record(self.id, "plan_response", f"{reason}: {why}",
                              [{"id": q["kind"], "score": q["qty"]} for q in plan["resource_requests"]], fallback=source == "fallback",
                              incident_id=incident["id"], decision_source="reasoner" if source == "reasoner" else "fallback",
                              plan_source=plan.get("source") or source, applied=applied, latency_ms=latency_ms,
                              inputs={k: situation.get(k) for k in ("people", "vulnerable", "injured", "trend", "briefing_revision", "severity")},
                              factors={"hazards": situation.get("hazards"), "escalation": plan.get("escalation")})
        return plan

    def _fallback_plan(self, incident, situation):
        """FALLBACK only (reasoner unavailable or failed): capability needs from the DisruptionOps taxonomy
        data -> best providing kind available to the swarm; else the engine's category template."""
        sim = self.sim
        hint = list(incident.get("required_hint") or incident["required_kinds"])
        if not incident.get("plan_owned"):
            # Prescribed scenario kinds stay authoritative; the plan only restates them.
            kinds, source = hint, "fallback:prescribed"
        else:
            kinds, source = self._taxonomy_kinds(incident), "fallback:disruptionops-taxonomy"
            if not kinds:
                kinds, source = hint or list(sim.INCIDENT_TEMPLATES.get(incident["category"], (None,) * 5)[4] or []), "fallback:engine-template"
        payloads = [n for n in incident["needs"] if n in SUPPLIES and n not in incident["supplied"]]
        return {"resource_requests": [{"kind": k, "qty": 1, "urgency": None, "why": source} for k in kinds],
                "drones": [{"role": "assess"}] + [{"role": "supply", "payload": p} for p in payloads],
                "rationale": f"{source}: {', '.join(kinds) or 'no ground kind'}", "source": source}

    def _taxonomy_kinds(self, incident):
        tax = self.taxonomy
        if not tax:
            return []
        category = incident.get("disruptionops_category") or CATEGORY_TO_DO.get(incident["category"], incident["category"])
        entry = next((c for c in tax.get("incident_categories", []) if c.get("id") == category), None)
        if entry is None:
            return []
        needs = entry.get("needs") or {}
        if isinstance(needs, list):
            needs = {n.get("capability"): n.get("qty", 1) for n in needs}
        available = self.sim._known_kinds()
        out = []
        for capability in needs:
            providers = (tax.get("kinds_providing") or {}).get(capability) or {}
            offers = sorted(((score, DO_TO_SIM.get(kind, kind)) for kind, score in providers.items() if DO_TO_SIM.get(kind, kind) in available),
                            key=lambda o: (-o[0], o[1]))
            if offers and offers[0][1] not in out:
                out.append(offers[0][1])
        return out

    def _validate(self, plan):
        """Normalise a plan; unknown kinds/roles/payloads are dropped and reported."""
        problems = []
        known = set(self.sim._known_kinds()) | set(self.sim.RESOURCE_KIND_NAMES)
        requests = []
        raw = plan.get("agency_requests", plan.get("resource_requests", plan.get("requests", plan.get("required_kinds", []))))
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, str):
                item = {"kind": item}
            if not isinstance(item, dict):
                problems.append(f"request {item!r}"); continue
            kind = item.get("sim_kind") or DO_TO_SIM.get(item.get("kind"), item.get("kind"))
            if kind not in known:
                problems.append(f"kind {kind!r}"); continue
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
                problems.append(f"drone {item!r}"); continue
            payload = None if item.get("payload") in (None, "none") else item.get("payload")
            if payload is not None and payload not in SUPPLIES:
                problems.append(f"payload {payload!r}"); payload = None
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
                    problems.append(f"delta {item!r}"[:60]); continue
                why = next((s.get("why") for s in plan.get("stand_down") or [] if DO_TO_SIM.get(s.get("kind"), s.get("kind")) == kind), None) if delta < 0 else None
                deltas.append({"kind": kind, "delta": max(-10, min(10, delta)), "why": str(why or "")[:160]})
        return {"resource_requests": requests, "requests_delta": deltas, "drones": drones, "severity": severity, "escalation": escalation,
                "rationale": str(plan.get("rationale") or "")[:240], "source": str(plan.get("source") or "")[:60] or None}, problems
