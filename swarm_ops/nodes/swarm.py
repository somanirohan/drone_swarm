"""SwarmNode: the drone side of the system.

The swarm decides only about its OWN aircraft (accept or decline drone work, which drone, which role, dropping
its own onboard supplies when that helps at once) and REPORTS WHAT IT SEES. It never decides which ground
units, or how many, a scene needs: that is the orchestrator's job (DisruptionOps). It tells the situation:
how many people, how many hurt, hazards, how the scene can be reached, and which units are already near
("PC-AMB-06 ambulance 400 m away, en route").

Outbound messages (to ``command_center``):

* ``situation_report`` on first look, on a significant change (hysteresis), periodically, and on request;
* ``alert`` for drone faults, a life threat, a worsening scene and a blocked access.

Fusion of the evidence is ``swarm_ops.reasoning.belief`` (unchanged).
"""
from __future__ import annotations

import math

from .base import Node

REPORT_PERIOD_S = 30.0      # a periodic report if nothing else was sent for this long
WATCH_PERIOD_S = 5.0        # how often the node re-assesses open incidents for significant change
NEARBY_RADIUS_M = 1500.0
HAZARD_LABELS = ((0.65, "high"), (0.35, "moderate"), (0.15, "low"))


def _level(value):
    return next((label for floor, label in HAZARD_LABELS if value >= floor), "none")


class SwarmNode(Node):
    id = "swarm"
    label = "SwarmNode"
    cadence = 1.0

    def __init__(self, runtime):
        super().__init__(runtime)
        self.pending = {}            # incident id -> (reason, force)
        self.last = {}               # incident id -> {"sig": ..., "t": ..., "revision": n, "trend": ..., "alerts": set}
        self.drone_seen = {}         # drone id -> (active, link, state)
        self._next_watch = 0.0
        self._taxonomy = None

    # ------------------------------------------------------------------ entry points (engine hooks and inbox)
    def notify(self, incident_id, reason, force=False):
        """The world changed for this incident (new evidence, first look, ...). Reported on the next step."""
        was = self.pending.get(incident_id, ("", False))
        self.pending[incident_id] = (reason if not was[0] or force else was[0], was[1] or force)

    def handle(self, message):
        body = message.get("body") or {}
        if message["kind"] == "task" and body.get("need") == "situation" and message.get("incident_id"):
            self.notify(message["incident_id"], "need_info", force=True)

    def wants_tick(self):
        return bool(self.pending)

    def tick(self):
        sim = self.sim
        did = self._alerts_for_drones()
        for incident_id, (reason, force) in list(self.pending.items()):
            self.pending.pop(incident_id, None)
            incident = sim._find_incident(incident_id)
            if incident is not None:
                did |= self.report(incident, reason, force)
        if sim.t >= self._next_watch:
            self._next_watch = sim.t + WATCH_PERIOD_S
            for incident in sim.incidents:
                if incident["status"] == "resolved":
                    continue
                last = self.last.get(incident["id"])
                if last is None:
                    continue    # not announced yet: the first look is sent through notify()
                periodic = sim.t - last["t"] >= REPORT_PERIOD_S
                did |= self.report(incident, "periodic" if periodic else "watch", periodic)
        return did

    # ------------------------------------------------------------------ building a report
    def _tax(self):
        if self._taxonomy is None:
            from swarm_ops.reasoning.taxonomy import load_taxonomy
            self._taxonomy = load_taxonomy()
        return self._taxonomy

    def _belief(self, scene):
        try:
            from swarm_ops.reasoning.belief import Belief
            from swarm_ops.reasoning.params import merged
            return Belief(scene, self._tax(), merged())
        except Exception:  # noqa: BLE001 - the report must still go out
            return None

    def build(self, incident, reason):
        sim = self.sim
        scene = sim._situation(incident)
        belief = self._belief(scene)
        position = incident["position"]
        if belief is not None:
            mean, sd = belief.mean("people"), belief.sd("people")
            people = {"estimate": max(0, int(round(mean))), "low": max(0, int(math.floor(mean - 1.3 * sd))), "high": int(math.ceil(mean + 1.3 * sd))}
            injured_mean, critical_mean = belief.mean("injured"), belief.mean("critical")
            vulnerable_n = belief.mean("vulnerable")
            hazards = [{"kind": name, "level": _level(g.mean), "value": round(g.mean, 2),
                        "trend": belief.trend_label if g.mean >= 0.3 and belief.trend_label in ("improving", "worsening") else "stable"}
                       for name, g in sorted(belief.hazards.items(), key=lambda kv: -kv[1].mean) if g.mean >= 0.15]
            trend = belief.trend_label if belief.trend_label in ("improving", "stable", "worsening") else "stable"
            confidence = round(max(0.05, min(0.99, 1.0 - belief.uncertainty())), 2)
            summary = belief.summary()
        else:
            n = scene["people"]
            people = {"estimate": n, "low": max(0, n - 1), "high": n + 2}
            injured_mean = float(scene.get("injured") or 0)
            critical_mean, vulnerable_n = 0.0, float(scene.get("vulnerable") or 0)
            hazards = [{"kind": h, "level": "moderate", "value": 0.5, "trend": "stable"} for h in scene["hazards"]]
            trend = scene["trend"] if scene["trend"] in ("improving", "stable", "worsening") else "stable"
            confidence, summary = round(scene.get("report_confidence", 0.6), 2), {}
        injured = {"estimate": int(round(injured_mean)), "severity": "critical" if critical_mean >= 0.5 else "serious" if injured_mean >= 0.5 else "none"}
        vulnerable = list(scene.get("vulnerable_hints") or [])
        if not vulnerable and vulnerable_n >= 0.5:
            vulnerable = ["vulnerable persons"] * int(round(vulnerable_n))
        drones_on_scene = [d["id"] for d in sim.drones if d["task_id"] == incident["id"] and d["active"] and d["state"] == "observing"]
        nearby = sim.world_sense_units(position, NEARBY_RADIUS_M, incident["id"])
        access = sim.world_access(position)
        observed = incident["observations"] > 0
        note = []
        if not observed:
            note.append("no aircraft has looked yet; figures come from the report")
        if people["high"] - people["low"] >= 3:
            note.append(f"people count uncertain (about {people['low']}-{people['high']})")
        if access["roads_open"] == 0:
            note.append("no open road found next to the scene")
        near = nearby[0] if nearby else None
        parts = [f"{people['estimate']} people (about {people['low']}-{people['high']}) at {scene['text'][:80].rstrip('.')}"]
        if injured["estimate"]:
            parts.append(f"{injured['estimate']} injured ({injured['severity']})")
        top = [h for h in hazards if h["level"] in ("moderate", "high")][:2]
        if top:
            parts.append("hazards: " + ", ".join(f"{h['kind']} {h['level']}" for h in top))
        if trend != "stable":
            parts.append(trend)
        if near:
            parts.append(f"{near['id']} {near['kind']} {near['distance_m']:.0f} m away, {near['state'].replace('_', ' ')}")
        text = "; ".join(parts) + "."
        return {"incident_id": incident["id"], "request_id": incident.get("request_id"), "observed_at": round(sim.t, 2), "reason": reason,
                "people": people, "injured": injured, "vulnerable": vulnerable, "hazards": hazards, "trend": trend, "access": access,
                "nearby_units": nearby, "lifelines": [{"name": l.get("name") or l.get("id"), "kind": l.get("kind"), "capacity": l.get("capacity"),
                                                       "distance_m": l.get("distance_m")} for l in scene.get("lifelines", [])[:4]],
                "drones_on_scene": drones_on_scene, "supplies_dropped": list(incident["supplied"]), "confidence": confidence,
                "uncertainty_note": "; ".join(note) or "figures confirmed by aircraft observation", "text": text[:300],
                "controlled_by": "disruptionops" if incident.get("control_mode") == "orchestrated" else "autonomous",
                "scene": {k: v for k, v in scene.items() if k not in ("drones_available", "units")},
                "belief": summary}

    @staticmethod
    def _signature(report):
        hazards = {h["kind"]: round(h["value"], 1) for h in report["hazards"]}
        units = tuple(sorted((u["id"], u["state"]) for u in report["nearby_units"][:5]))
        return {"people": report["people"]["estimate"], "injured": report["injured"]["estimate"], "severity": report["injured"]["severity"],
                "vulnerable": len(report["vulnerable"]), "hazards": hazards, "trend": report["trend"],
                "access": (report["access"]["roads_open"] > 0, report["access"]["roads_blocked"] > 0), "units": units,
                "drones": tuple(report["drones_on_scene"]), "supplies": tuple(report["supplies_dropped"]),
                "revision": (report["scene"].get("briefing_revision"))}

    @staticmethod
    def _significant(old, new):
        if old is None:
            return "first look"
        if abs(new["people"] - old["people"]) >= max(1, 0.15 * max(old["people"], 1)):
            return "people estimate changed"
        if new["injured"] != old["injured"] or new["severity"] != old["severity"]:
            return "injuries changed"
        if new["vulnerable"] != old["vulnerable"]:
            return "vulnerable people identified"
        for kind in set(new["hazards"]) | set(old["hazards"]):
            if abs(new["hazards"].get(kind, 0) - old["hazards"].get(kind, 0)) >= 0.2:
                return f"hazard {kind} changed"
        if new["trend"] != old["trend"]:
            return f"scene {new['trend']}"
        if new["access"] != old["access"]:
            return "access changed"
        if new["units"] != old["units"]:
            return "nearby units changed"
        if new["drones"] != old["drones"]:
            return "aircraft on scene changed"
        if new["supplies"] != old["supplies"]:
            return "supplies dropped"
        if new["revision"] != old["revision"]:
            return "new briefing"
        return None

    def report(self, incident, reason, force=False):
        sim = self.sim
        report = self.build(incident, reason)
        incident["situation"] = report["scene"]
        incident["drone_plan"] = self._drone_plan(incident)
        state = self.last.setdefault(incident["id"], {"sig": None, "t": -1e9, "revision": 0, "trend": None, "alerts": set()})
        signature = self._signature(report)
        why = self._significant(state["sig"], signature)
        self._incident_alerts(incident, report, state)
        if not force and why is None:
            return False
        state["sig"], state["t"] = signature, sim.t
        state["revision"] += 1
        report["revision"] = state["revision"]
        report["reason"] = why if reason in ("watch", "periodic") and why else reason
        sim.decisions.record(self.id, "situation_report", f"r{report['revision']} ({report['reason']}): {report['text']}"[:240], incident_id=incident["id"],
                             decision_source="rule", confidence=report["confidence"])
        self.send("command_center", "situation_report", report["text"], incident["id"], incident.get("request_id"), report)
        return True

    @staticmethod
    def _drone_plan(incident):
        """The swarm's own aircraft intent: assess, and bring supplies a drone can carry itself."""
        supplies = [n for n in incident["needs"] if n in ("first_aid", "medical", "food", "water") and n not in incident["supplied"]]
        return {"roles": ["assess"] + ["supply"] * len(supplies), "payloads": supplies}

    # ------------------------------------------------------------------ alerts
    def alert(self, alert, text, incident_id=None, severity="warning", **body):
        self.send("command_center", "alert", text, incident_id, None, {"alert": alert, "severity": severity, "incident_id": incident_id, **body})

    def _incident_alerts(self, incident, report, state):
        sent = state["alerts"]
        iid = incident["id"]
        top = max((h["value"] for h in report["hazards"]), default=0.0)
        threat = report["injured"]["severity"] == "critical" or (report["injured"]["estimate"] >= 1 and top >= 0.65)
        if threat and "life_threat" not in sent:
            sent.add("life_threat")
            self.alert("life_threat", f"Life threat at {iid}: {report['injured']['estimate']} injured ({report['injured']['severity']}), hazards {', '.join(h['kind'] for h in report['hazards'][:2]) or 'unknown'}.",
                       iid, "critical", people=report["people"]["estimate"], injured=report["injured"]["estimate"])
        elif not threat:
            sent.discard("life_threat")
        worsening = report["trend"] == "worsening"
        if worsening and "worsening" not in sent:
            sent.add("worsening")
            self.alert("situation_worsening", f"{iid} is getting worse ({report['text'][:120]}).", iid, "warning", trend="worsening")
        elif not worsening:
            sent.discard("worsening")
        blocked = report["access"]["roads_open"] == 0 and (report["access"]["roads_blocked"] > 0 or report["access"]["roads_flooded"] > 0)
        if blocked and "access" not in sent:
            sent.add("access")
            self.alert("access_blocked", f"Ground access to {iid} is blocked ({report['access']['roads_blocked']} blocked, {report['access']['roads_flooded']} flooded road segments near the scene).",
                       iid, "warning", access=report["access"])
        elif not blocked:
            sent.discard("access")

    def _alerts_for_drones(self):
        did = False
        for drone in self.sim.drones:
            now = (drone["active"], drone["link"], drone["state"])
            old = self.drone_seen.get(drone["id"])
            self.drone_seen[drone["id"]] = now
            if old is None:
                continue
            iid = drone.get("task_id")
            if old[0] and not drone["active"]:
                self.alert("drone_fault", f"{drone['id']} crashed or made an emergency landing ({drone['state']}); its task is released for re-award.", iid, "critical",
                           drone_id=drone["id"], fault="crash" if drone["state"] == "crashed" else drone["state"])
                did = True
            elif old[1] and not drone["link"]:
                self.alert("drone_fault", f"{drone['id']} lost its link; mission state kept, delivery uncertain.", iid, "warning", drone_id=drone["id"], fault="link_loss")
                did = True
            elif drone["state"] == "returning" and old[2] != "returning" and drone["battery"] < 0.26 and drone["active"]:
                self.alert("drone_fault", f"{drone['id']} battery low ({drone['battery']:.0%}); returning to {drone.get('dock_id') or 'base'}.", iid, "warning",
                           drone_id=drone["id"], fault="low_battery", battery=round(drone["battery"], 3))
                did = True
        return did
