"""Decentralized coordination layer (simulation only).

This is the *world side* of decentralized mode: radio physics (swarm_ops.mesh), the command link that hands the
swarm a mission, and the scheduler that gives every aircraft a turn. It never assigns a sub-task to a drone and
never computes an assignment: each ``DroneAgent.coordinate`` decides from its own state, its own observations
and the messages its radio received (CBBA, swarm_ops.cbba).

Command link = the DisruptionOps side (or its stand-in). It transmits ``mission`` orders ("go to I005, report the
situation") to whichever aircraft are gateways at that moment, retransmits until a report comes back, and
transmits ``close`` when the incident is resolved or no longer decentralized. Reports from drones travel back over
the mesh (multi-hop) and are posted on the bus as ``report`` messages to ``command_center``.

Drone-to-drone ``consensus`` traffic is high volume: it is counted in ``mesh`` stats and sampled, never posted on
the bus log.
"""
from __future__ import annotations

import math

from swarm_ops.cbba import LOCK
from swarm_ops.mesh import Mesh, default_range

COORDINATION_MODES = ("central", "decentralized", "orchestrated")
DEFAULTS = {"mode": "central", "radio_range_m": None, "packet_loss": 0.0, "latency_s": 0.1, "max_bundle": 2, "peer_timeout_s": 10.0,
            "heartbeat_s": 2.0, "commit_s": 1.0}


class DecentralizedLayer:
    def __init__(self, sim, config):
        self.sim = sim
        self.config = dict(config)
        if not self.config.get("radio_range_m"):
            self.config["radio_range_m"] = default_range(sim.bounds)
        self.mesh = Mesh(seed=sim.seed, radio_range_m=self.config["radio_range_m"], latency_s=self.config["latency_s"],
                         packet_loss=self.config["packet_loss"])
        self.heartbeat_s = float(self.config["heartbeat_s"])
        self.commit_s = float(self.config["commit_s"])
        self.missions = {}       # command side: mission id -> {issued_t, last_tx, first_report_t, ...}
        self.active = False
        self._next_topology = -1.0
        self.done_log = []       # (t, drone, subtask) for metrics
        self.stations = []

    # ------------------------------------------------------------------ configuration
    def configure(self, **params):
        for key in ("radio_range_m", "packet_loss", "latency_s", "max_bundle", "peer_timeout_s", "heartbeat_s", "commit_s"):
            if params.get(key) is not None:
                self.config[key] = params[key]
        self.mesh.configure(radio_range_m=self.config["radio_range_m"], latency_s=self.config["latency_s"], packet_loss=self.config["packet_loss"])
        self.heartbeat_s = float(self.config["heartbeat_s"])
        self.commit_s = float(self.config["commit_s"])
        for agent in self.sim.agents.values():
            if agent.cbba is not None:
                agent.enable_cbba(self.config["max_bundle"], self.config["peer_timeout_s"])
        self._next_topology = -1.0

    def activate(self):
        if not self.active:
            self.active = True
            for agent in self.sim.agents.values():
                agent.enable_cbba(self.config["max_bundle"], self.config["peer_timeout_s"])
            self._topology(force=True)

    # ------------------------------------------------------------------ radio physics
    def _stations(self):
        sim = self.sim
        return [list(sim._base[:2])] + [list(dock["position"][:2]) for dock in sim.docks]

    def _topology(self, force=False):
        sim = self.sim
        if not force and sim.t < self._next_topology:
            return
        self._next_topology = sim.t + 0.5
        self.stations = self._stations()
        radios = {d["id"]: d["position"] for d in sim.drones if d["active"] and d["link"]}
        self.mesh.update_topology(radios, self.stations, sim.t)

    def hears_command(self, drone_id):
        return drone_id in self.mesh.gateways

    # ------------------------------------------------------------------ command link (DisruptionOps side)
    def issue_mission(self, incident):
        sim = self.sim
        self.activate()
        entry = self.missions.get(incident["id"])
        if entry is not None:
            return entry
        entry = self.missions[incident["id"]] = {"issued_t": round(sim.t, 2), "last_tx": None, "first_report_t": None, "first_report_by": None,
                                                 "report_hops": None, "closed_t": None, "created_t": incident.get("created_at")}
        incident["coordination"] = {"mode": "decentralized", "mission_issued_t": round(sim.t, 2)}
        sim.decisions.record("command_center", "mission_issued", f"mission to the swarm: go to {incident['id']}, report the situation "
                             "(decentralized: drones derive and allocate the aerial sub-tasks themselves)", incident_id=incident["id"], decision_source="rule")
        sim.bus.post("command_center", "swarm", "task", f"Mission {incident['id']}: go to the scene and report the situation (decentralized).",
                     incident["id"], incident.get("request_id"), {"mission": incident["id"], "coordination": "decentralized"})
        sim._event("mission_issued", f"Mission {incident['id']} sent over the command link to the gateway drones; the swarm self-organises.", incident["id"])
        self._transmit(incident)
        return entry

    def _mission_def(self, incident):
        return {"id": incident["id"], "position": [round(v, 1) for v in incident["position"][:3]], "category": incident["category"],
                "severity": incident["severity"], "people_reported": incident["people"], "issued_t": self.missions[incident["id"]]["issued_t"],
                "text": f"go to {incident['id']}, report the situation"}

    def _transmit(self, incident):
        entry = self.missions[incident["id"]]
        self._topology()
        entry["last_tx"] = self.sim.t
        self.mesh.from_command({"mission": self._mission_def(incident)}, self.sim.t, kind="mission")

    def close_mission(self, incident_id, reason):
        entry = self.missions.get(incident_id)
        if entry is None or entry["closed_t"] is not None:
            return False
        entry["closed_t"] = round(self.sim.t, 2)
        entry["close_reason"] = reason
        self.mesh.from_command({"close": incident_id}, self.sim.t, kind="mission")
        self.sim.decisions.record("command_center", "mission_closed", f"{incident_id}: {reason}", incident_id=incident_id, decision_source="rule")
        return True

    def _command_side(self):
        sim = self.sim
        for mid, entry in self.missions.items():
            incident = sim._find_incident(mid)
            if entry["closed_t"] is not None:
                # keep telling the swarm until the close is heard (the command side cannot see who heard it)
                if entry.get("close_tx", -1e9) + 5 <= sim.t and sim.t - entry["closed_t"] <= 60:
                    entry["close_tx"] = sim.t
                    self.mesh.from_command({"close": mid}, sim.t, kind="mission")
                continue
            if incident is None or incident["status"] == "resolved":
                self.close_mission(mid, "incident resolved")
            elif sim._coordination_mode(incident) != "decentralized":
                self.close_mission(mid, f"coordination switched to {sim._coordination_mode(incident)}")
            elif entry["first_report_t"] is None and sim.t - (entry["last_tx"] or -1e9) >= 5.0:
                self._transmit(incident)          # no report yet: retransmit (gateways change, packets drop)
        for message in self.mesh.drain_command():
            report = (message.get("payload") or {}).get("report")
            if not report:
                continue
            entry = self.missions.get(report.get("mission_id"))
            if entry is None:
                continue
            first = entry["first_report_t"] is None
            if first:
                entry.update(first_report_t=round(sim.t, 2), first_report_by=report["by"], report_hops=message["hops"])
            sim.bus.post(report["by"], "command_center", "report",
                         f"{report['by']} report on {report['mission_id']} reached command over the mesh ({message['hops']} hops): "
                         f"{report.get('people')} people, hazards {', '.join(report.get('hazards') or []) or 'none seen'}.",
                         report["mission_id"], None, {"mission_id": report["mission_id"], "subtask": report.get("subtask"), "people": report.get("people"),
                                                      "hazards": report.get("hazards"), "hops": message["hops"], "observed_t": report.get("t"), "via": "mesh"})

    # ------------------------------------------------------------------ hooks used by the drones' own logic
    def report(self, agent, task, incident):
        hazards = [h for h in ((incident.get("briefing") or {}).get("hazards") or []) if h and h != "none"]
        self.mesh.to_command(agent.id, {"report": {"mission_id": task["mission_id"], "subtask": task["id"], "by": agent.id, "t": round(self.sim.t, 2),
                                                   "people": incident["people"], "hazards": hazards}}, self.sim.t)

    def note_done(self, drone_id, subtask):
        self.done_log.append((round(self.sim.t, 2), drone_id, subtask))

    # ------------------------------------------------------------------ scheduler
    def step(self, delta):
        if not self.active:
            return
        sim = self.sim
        self._topology()
        self._command_side()
        self.mesh.deliver(sim.t)
        for drone in sim.drones:
            agent = sim.agents[drone["id"]]
            if not drone["active"] or agent.cbba is None:
                continue
            if not drone["link"]:
                self.mesh.drain(drone["id"])   # radio fault: hears nothing, sends nothing
                continue
            agent.coordinate(sim, self, sim.t, self.mesh.drain(drone["id"]))

    # ------------------------------------------------------------------ observer views (UI / telemetry only)
    def drone_view(self, drone):
        agent = self.sim.agents[drone["id"]]
        c = agent.cbba
        if not self.active and self.mesh.topology_t is None:
            self._topology(force=True)
        mode = "decentralized" if drone.get("dc_working") or (c is not None and c.bundle) else None
        if mode is None:
            incident = self.sim._find_incident(drone["task_id"]) if drone["task_id"] else None
            mode = self.sim._coordination_mode(incident) if incident else self.sim.coordination["mode"]
        bids = {}
        if c is not None:
            for j in c.bundle:
                y = c.y.get(j, 0.0)
                bids[j] = round(y - LOCK if y >= LOCK else y, 2)
        neighbours = self.mesh.neighbours.get(drone["id"], {})
        return {"mode": mode, "bundle": list(c.bundle) if c else [], "path": list(c.path) if c else [], "winning_bids": bids,
                "locked": sorted(c.locked) if c else [], "subtask": drone.get("subtask"), "subtask_kind": drone.get("subtask_kind") if drone.get("subtask") else None,
                "neighbours": sorted(neighbours), "link_quality": {k: v for k, v in sorted(neighbours.items())},
                "last_consensus_t": None if c is None or c.last_consensus_t is None else round(c.last_consensus_t, 2),
                "gateway": drone["id"] in self.mesh.gateways, "hops_to_gateway": self.mesh.hops_to_gateway.get(drone["id"]),
                "gateway_hops_estimate": agent.gw_hops, "known_peers_lost": sorted(c.lost) if c else []}

    def subtasks_view(self):
        """Observer aggregation of every drone's local view (not used by any decision)."""
        sim = self.sim
        defs, done, claims = {}, {}, {}
        for drone in sim.drones:
            c = sim.agents[drone["id"]].cbba
            if c is None:
                continue
            for j, d in c.tasks.items():
                defs.setdefault(j, d)
            done.update({j: t for j, t in c.done.items() if j not in done})
            if not drone["active"]:
                continue
            for j in c.bundle:
                y = c.y.get(j, 0.0)
                claims.setdefault(j, []).append((y, drone["id"]))
        rows = []
        for j in sorted(defs):
            d = defs[j]
            entry = self.missions.get(d["mission_id"]) or {}
            if entry.get("closed_t") is not None and sim.t - entry["closed_t"] > 120:
                continue
            ranked = sorted(claims.get(j, []), key=lambda c: (-c[0], c[1]))
            winner = ranked[0][1] if ranked else None
            bid = None if not ranked else round(ranked[0][0] - LOCK if ranked[0][0] >= LOCK else ranked[0][0], 2)
            if j in done:
                status = "done"
            elif entry.get("closed_t") is not None:
                status = "closed"
            elif winner:
                w = next(x for x in sim.drones if x["id"] == winner)
                status = "executing" if w.get("subtask") == j and w["state"] == "observing" else "enroute" if w.get("subtask") == j else "assigned"
            else:
                status = "open"
            rows.append({"id": j, "mission_id": d["mission_id"], "kind": d["kind"], "position": list(d["position"]), "winner": winner, "bid": bid,
                         "status": status, "created_by": d.get("created_by"), "conflicts": [c[1] for c in ranked[1:]]})
        return rows

    def snapshot(self):
        if self.mesh.topology_t is None or not self.active:
            self._topology(force=True)
        agents = [a.cbba for a in self.sim.agents.values() if a.cbba is not None]
        return {"active": self.active, "config": {k: self.config[k] for k in ("radio_range_m", "packet_loss", "latency_s", "max_bundle", "peer_timeout_s", "heartbeat_s")},
                "missions": {k: dict(v) for k, v in self.missions.items()},
                "consensus_messages_processed": sum(c.rounds for c in agents), "subtasks_done": len(self.done_log),
                "recent_done": [list(x) for x in self.done_log[-10:]]}
