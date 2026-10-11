"""Agents of the operations swarm: drones, the swarm coordinator, and the bus between them.

Everything here is deterministic and offline (no LLM in the decision path).

* ``MessageBus``: every message between agents and to/from the command centre (orchestrator)
  is a record ``{id, t, from, to, kind, incident_id, request_id, text, body}``. Kinds:
  task | accept | decline | report | status | ack | cancel | handoff | situation_report | alert | dispatch | unit_status.
* ``DecisionLog``: every decision is a trace ``{t, agent, decision, reason, options:[{id, score}]}``;
  ``fallback: true`` marks decisions taken by a fallback rule instead of the reasoner.
* ``DroneAgent``: perceive -> decide (priority behaviour tree) -> act, with memory of its zone,
  sweep, commitments and the briefing revisions it produced. It evaluates its own bid for a task
  (route, recovery route, energy) and accepts or declines an award.
* ``SwarmCoordinator``: the swarm's drone-side brain. It decides on orchestrator requests (the swarm
  accepts or declines DRONE work only) and runs the single-item auction over drone bids. It does NOT
  decide which ground units or how many are needed: the swarm node (swarm_ops.nodes.swarm) reports the
  situation and the orchestrator (swarm_ops.nodes.orchestrator, or DisruptionOps) allocates.
"""
from __future__ import annotations

import json
import math
import time
from collections import deque
from pathlib import Path

MESSAGE_KINDS = ("task", "accept", "decline", "report", "status", "ack", "cancel", "handoff",
                 "situation_report", "alert", "dispatch", "unit_status")
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
        self.listeners = []   # world-side observers (e.g. the engine's order ledger); never node-to-node shortcuts
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
        for listener in list(self.listeners):
            listener(message)
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
class SwarmCoordinator:
    """The swarm-as-agency drone-side brain (see module docstring)."""

    id = "swarm"

    def __init__(self, sim, bus, decisions):
        self.sim = sim
        self.bus = bus
        self.decisions = decisions

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
