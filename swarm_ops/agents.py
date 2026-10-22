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
                 "situation_report", "alert", "dispatch", "unit_status", "bid", "consensus")
# 'bid' / 'consensus' are drone-to-drone mesh traffic (decentralized mode). They are high volume and are NOT
# posted on the bus log: the snapshot aggregates them in ``mesh`` counters (swarm_ops.mesh).
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
    # Decentralized mode: the aircraft works the head of its own CBBA path (swarm_ops.cbba).
    Sequence("subtask", Condition("has_subtask", lambda a, s: a.drone.get("dc_working") and a.drone["state"] in ("enroute", "observing")),
             Action("work_subtask", lambda a, s, d: a.work_subtask(s, d))),
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
        self.cbba = None            # CBBAAgent, created when the drone joins decentralized coordination
        self.gw_adverts = {}        # neighbour id -> (hops to a gateway it advertised, time heard)
        self.gw_hops = None         # own estimate (0 = this drone hears the command link itself)
        self._next_heartbeat = 0.0
        self._next_scene_look = 0.0

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


    # ------------------------------------------------------------ decentralized coordination (CBBA)
    # Everything below uses ONLY this aircraft's own state, what it observes, and the radio messages it
    # received (swarm_ops.mesh). The engine-side scheduler (swarm_ops.decentral) never assigns work.
    def enable_cbba(self, max_bundle=2, peer_timeout=10.0):
        if self.cbba is None:
            from swarm_ops.cbba import CBBAAgent
            self.cbba = CBBAAgent(self.id, max_bundle=max_bundle, peer_timeout=peer_timeout)
        else:
            self.cbba.max_bundle, self.cbba.peer_timeout = int(max_bundle), float(peer_timeout)
        return self.cbba

    def can_bid(self):
        d = self.drone
        if not d["active"] or not d["link"]:
            return False
        if d["task_id"] and not d.get("dc_working"):
            return False            # busy with central / orchestrated work
        state = d["state"]
        if state in ("ready", "patrolling", "enroute", "observing"):
            return True
        if state == "charging":
            return d["battery"] >= .6
        if state == "returning":
            return d["battery"] >= .5 and not d["task_id"]
        return False

    def path_score(self, sim, tasks):
        """Own score of flying ``tasks`` in order: time-discounted sub-task priority, None if the payload does
        not fit or the energy (incl. the recovery leg home and a reserve) is not enough."""
        d = self.drone
        pos, t, total, dist, hover = d["position"], 0.0, 0.0, 0.0, 0.0
        for task in tasks:
            if task["kind"] == "supply" and (d["payload"] != task.get("payload") or d["payload_stock"] <= 0):
                return None
            leg = math.hypot(task["position"][0] - pos[0], task["position"][1] - pos[1]) * 1.15
            dist += leg
            t += leg / 18.0
            total += task["priority"] * math.exp(-t / 300.0)
            t += task["duration"]
            hover += task["duration"] * 0.075
            pos = task["position"]
        home = d.get("home", sim._base)
        dist += math.hypot(home[0] - pos[0], home[1] - pos[1]) * 1.15
        if d["energy_wh"] < dist * 0.018 + hover + 29:
            return None
        return total * (0.9 + 0.1 * d["battery"])

    def coordinate(self, sim, layer, t, inbox):
        """One decentralized step: merge radio messages, detect lost peers, derive sub-tasks from what this
        aircraft sees, rebuild its bundle, execute the head of its path, and broadcast if anything changed."""
        from swarm_ops.cbba import mission_subtasks
        d, c = self.drone, self.cbba
        for message in inbox:
            payload = message["payload"]
            if message["kind"] == "mission":
                mission = payload.get("mission")
                if mission and c.add_mission(mission, t):
                    new = c.add_tasks(mission_subtasks(mission, layer.stations, layer.mesh.radio_range_m, layer.mesh.gateway_range, self.id, t))
                    self.decide("subtask_generated", f"heard mission {mission['id']} on the command link; derived {', '.join(new) or 'nothing new'}",
                                incident_id=mission["id"], decision_source="cbba")
                if payload.get("close") and c.close_mission(payload["close"], t):
                    self.decide("mission_closed", f"command closed mission {payload['close']}", incident_id=payload["close"], decision_source="cbba")
            elif message["kind"] == "consensus":
                self.gw_adverts[payload["from"]] = (payload.get("gw_hops"), t)
                c.receive(payload, t)
        # hops to a gateway: distance-vector over neighbours' fresh adverts (0 = hears the command link itself)
        if layer.hears_command(self.id):
            self.gw_hops = 0
        else:
            fresh = [h for h, heard in self.gw_adverts.values() if h is not None and t - heard <= 6.0]
            self.gw_hops = min(fresh) + 1 if fresh else None
        for peer, held in c.check_peers(t).items():
            if held:
                self.decide("peer_lost", f"nothing heard from {peer} for {c.peer_timeout:.0f} s; its sub-tasks {', '.join(held)} are open again",
                            decision_source="cbba")
        current = d.get("subtask")
        if current and d["state"] not in ("enroute", "observing"):
            c.abandon(current, t)
            d.update(subtask=None, dc_working=False)
            self.decide("cbba_release", f"gave up {current} ({d['state']}); a peer can take it over", decision_source="cbba")
        if d.get("dc_working") and d["state"] == "observing" and t >= self._next_scene_look:
            self.look_at_scene(sim, layer, t)
        self._discover_relay(sim, layer, t)
        if self.can_bid():
            c.build_bundle(lambda tasks: self.path_score(sim, tasks), t)
        elif not d.get("dc_working"):
            for j in [j for j in c.bundle if j not in c.locked]:
                c.abandon(j, t, cooldown=5.0)
        self._trace_cbba(c)
        self.execute_head(sim, layer, t)
        if c.changed or t >= self._next_heartbeat:
            msg = c.message(t)
            msg["gw_hops"] = self.gw_hops
            layer.mesh.broadcast(self.id, msg, t, ttl=1, kind="consensus")
            c.changed = False
            self._next_heartbeat = t + layer.heartbeat_s

    def _trace_cbba(self, c):
        names = {"bid": "cbba_bid", "outbid": "cbba_outbid", "takeover": "cbba_takeover", "abandon": "cbba_release"}
        for kind, task, detail in c.events:
            reason = {"bid": f"bids {detail} on {task}", "outbid": f"outbid on {task} by {detail}; releases it and the rest of its bundle",
                      "takeover": f"takes over {task} from a lost peer (bid {detail})", "abandon": f"releases {task}"}[kind]
            self.decide(names[kind], reason, incident_id=c.tasks.get(task, {}).get("mission_id"), decision_source="cbba")
        c.events.clear()

    def _discover_relay(self, sim, layer, t):
        from swarm_ops.cbba import _task
        d, c = self.drone, self.cbba
        if not (d.get("dc_working") and d["state"] == "observing" and d.get("subtask_kind") != "relay" and self.gw_hops is None):
            self.memory["no_gateway_since"] = None
            return
        since = self.memory.get("no_gateway_since")
        if since is None:
            self.memory["no_gateway_since"] = t
            return
        mid = d.get("subtask_mission")
        if t - since < 6.0 or not mid or not layer.stations:
            return
        station = min(layer.stations, key=lambda s: math.hypot(s[0] - d["position"][0], s[1] - d["position"][1]))
        mid_point = ((d["position"][0] + station[0]) / 2, (d["position"][1] + station[1]) / 2)
        mission = c.missions.get(mid) or {"severity": 3}
        new = c.add_tasks([_task(mid, "relay-link", "relay", mid_point, self.id, t, mission.get("severity", 3))])
        if new:
            self.decide("relay_needed", f"no radio route to any gateway for {t - since:.0f} s; announces relay sub-task {new[0]}", incident_id=mid,
                        decision_source="cbba")

    def look_at_scene(self, sim, layer, t):
        from swarm_ops.cbba import SUPPLY_ITEMS, scene_subtasks
        self._next_scene_look = t + 5.0
        d, c = self.drone, self.cbba
        task = c.tasks.get(d.get("subtask") or "")
        if not task or task["kind"] not in ("assess", "overwatch", "handover"):
            return
        mid = task["mission_id"]
        incident = sim._find_incident(mid)
        if incident is None:
            return
        seen = self.memory.setdefault("handover_seen", {})
        if incident["status"] == "resolved" or (incident["handed_over"] and t - seen.setdefault(mid, t) >= 20.0):
            if c.close_mission(mid, t):
                self.decide("mission_closed", f"sees the ground crew in charge of {mid}; closes the mission for the swarm", incident_id=mid,
                            decision_source="cbba")
            return
        hazards = [h for h in ((incident.get("briefing") or {}).get("hazards") or []) if h and h != "none"]
        coming = any(r.get("incident_id") == mid and r["state"] in ("enroute", "awaiting_handoff")
                     and math.hypot(r["position"][0] - incident["position"][0], r["position"][1] - incident["position"][1]) <= 1500 for r in sim.resources)
        observation = {"people": incident["people"], "people_uncertain": incident["observations"] <= 1 and incident["people"] >= 3,
                       "hazards": hazards, "needs_unmet": [n for n in incident["needs"] if n in SUPPLY_ITEMS and n not in incident["supplied"]],
                       "ground_unit_coming": coming and not incident["handed_over"], "severity": incident["severity"], "category": incident["category"]}
        mission = c.missions.get(mid) or {"id": mid, "position": incident["position"], "severity": incident["severity"]}
        new = c.add_tasks(scene_subtasks(mission, observation, self.id, t, sim.bounds))
        if new:
            self.decide("subtask_generated", f"on scene at {mid} ({incident['people']} people, hazards {', '.join(hazards) or 'none seen'}): derives {', '.join(new)}",
                        incident_id=mid, decision_source="cbba")

    def execute_head(self, sim, layer, t):
        from swarm_ops.cbba import LOCK
        d, c = self.drone, self.cbba
        head = c.path[0] if c.path else None
        current = d.get("subtask")
        working = d.get("dc_working")
        if head is not None and head == current and working:
            return
        if head is None:
            d["subtask"] = None
            if working:
                d["dc_working"] = False
                if d["state"] in ("enroute", "observing"):
                    why = f"{current} no longer in own bundle (taken by a peer or closed)" if current else "own bundle empty: sub-tasks complete"
                    self.decide("return", why, incident_id=d.get("task_id"), decision_source="cbba")
                    sim._return(d, "decentralized sub-tasks complete" if not current else f"{current} handled by a peer")
            return
        if not self.can_bid():
            return
        if not c.committed(head, t, layer.commit_s):
            return                       # tentative claim: let consensus settle before flying
        task = c.tasks[head]
        kind, mid = task["kind"], task["mission_id"]
        scene = kind in ("assess", "overwatch", "handover", "supply")
        incident = sim._find_incident(mid)
        target = list(incident["position"]) if scene and incident else [*task["position"], 0.0]
        observe = kind in ("assess", "overwatch", "handover")
        route = sim._drone_path(d, target, standoff=sim.STANDOFF_M if observe else 0.0, observe=observe)
        if route is None:
            c.abandon(head, t)
            self._trace_cbba(c)
            return
        launched = d["state"] in ("ready", "charging")
        d.update(state="enroute", route=route, subtask=head, subtask_kind=kind, subtask_started=None, subtask_mission=mid, dc_working=True,
                 task_id=mid if scene else None, coverage_task=None, search_target=None, retasked=None, delivered=False)
        if incident and scene and incident["status"] in ("reported", "open", "pending"):
            incident["status"] = "responding"
        bid = c.y.get(head, 0.0)
        bid = bid - LOCK if bid >= LOCK else bid
        self.decide("cbba_execute", f"flies to {head} ({kind}); own bid {bid:.1f}, own path {', '.join(c.path)}" + (f" (was {current})" if current else ""),
                    incident_id=mid, decision_source="cbba")
        if launched:
            sim._event("drone_launch", f"{d['id']} launches for {head} ({kind}); won by decentralized consensus, no central award.", mid)

    def work_subtask(self, sim, delta):
        d, c = self.drone, self.cbba
        j = d.get("subtask")
        task = c.tasks.get(j) if c and j else None
        if task is None:
            return                      # the scheduler gives the next head (or recalls) this step
        if j in c.done or task["mission_id"] in c.closed:
            d["subtask"] = None
            return
        incident = sim._find_incident(task["mission_id"])
        kind = task["kind"]
        if d["state"] == "enroute":
            if d["route"]:
                return
            d["state"] = "observing"
            d["subtask_started"] = sim.t
            c.lock(j)
            if kind == "assess" and incident:
                self.start_observing(sim)
                sim.decentral.report(self, task, incident)
                self.look_at_scene(sim, sim.decentral, sim.t)
            elif kind == "supply" and incident:
                item = d["payload"]
                if d["payload_stock"] > 0 and item in incident["needs"] and item not in incident["supplied"]:
                    incident["supplied"].append(item)
                    d.update(payload_stock=d["payload_stock"] - 1, delivered=True, delivered_at=sim.t)
                    sim._event("supply", f"{d['id']} dropped its own {item} at {incident['id']} (sub-task {j}); incident remains open.", incident["id"])
                    self.bus.post(self.id, "swarm", "report", f"{self.id} dropped {item} at {incident['id']} (sub-task {j}).", incident["id"],
                                  incident.get("request_id"), {"supplied": item, "subtask": j})
                self._finish_subtask(sim, j, f"own {item} dropped")
                return
            else:
                sim._event("subtask_on_station", f"{d['id']} on station for {j} ({kind}).", task["mission_id"])
            self.decide("cbba_on_station", f"{j} ({kind}) started; claim locked", incident_id=task["mission_id"], decision_source="cbba")
            return
        elapsed = sim.t - (d.get("subtask_started") or sim.t)
        if kind == "assess":
            if incident and sim.t - (incident["last_observed"] or 0) > 4:
                sim._brief(incident, d)
            if elapsed >= task["duration"]:
                self._finish_subtask(sim, j, "scene assessed and reported")
        elif kind == "search":
            if elapsed >= task["duration"]:
                self._finish_subtask(sim, j, "sector searched")
        elif kind == "handover":
            if incident is None or incident["handed_over"] or incident["status"] == "resolved":
                self._finish_subtask(sim, j, "arriving ground unit briefed and in charge")
            elif elapsed >= 300:
                self._finish_subtask(sim, j, "handover window elapsed")
        elif kind == "overwatch":
            if incident is None or incident["status"] == "resolved" or elapsed >= 240:
                self._finish_subtask(sim, j, "overwatch period complete")
        # relay: holds until the mission is closed (or energy forces a release)

    def _finish_subtask(self, sim, j, why):
        self.cbba.complete(j, sim.t)
        self.drone["subtask"] = None
        mid = self.cbba.tasks.get(j, {}).get("mission_id")
        self.decide("subtask_done", f"{j}: {why}", incident_id=mid, decision_source="cbba")
        sim.decentral.note_done(self.id, j)


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
