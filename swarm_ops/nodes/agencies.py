"""Agency nodes: each owns its own ground units and decides which of THEM answers a dispatch.

An agency node receives ``dispatch`` messages (from the orchestrator or DisruptionOps), picks one of its
own available units (nearest feasible, exact kind preferred), asks the world to start moving it (the
engine only simulates the movement on roads or water) and reports ``unit_status`` (dispatched, arrived,
on scene, transporting, available again, no unit available) and ``handoff`` messages back. It never reads
or writes another node's state.
"""
from __future__ import annotations

from .base import Node

LABEL = {"available": "available", "enroute": "en_route", "awaiting_handoff": "arrived", "on_scene": "on_scene",
         "evacuating": "transporting", "holding": "holding", "busy": "unavailable", "unavailable": "unavailable"}


class AgencyNode(Node):
    kinds = ()
    cadence = 1.0

    def __init__(self, runtime):
        super().__init__(runtime)
        self.pending = []      # dispatch orders still to be filled
        self.known = {}        # unit id -> last reported signature
        self.reported_unavailable = set()

    def wants_tick(self):
        return bool(self.pending)

    # -- inbox
    def handle(self, message):
        body = message.get("body") or {}
        if message["kind"] == "dispatch" and message["to"] == self.id:
            self.on_dispatch(message, body)
        elif message["kind"] == "cancel" and message["to"] == self.id:
            for order in self.pending:
                if order["dispatch_id"] == body.get("dispatch_id"):
                    order["remaining"] = max(0, order["remaining"] - int(body.get("qty") or order["remaining"]))
            self.pending = [o for o in self.pending if o["remaining"] > 0]

    def on_dispatch(self, message, body):
        dispatch_id = body.get("dispatch_id") or message.get("request_id") or f"{message.get('incident_id')}-{message['id']}"
        if any(o["dispatch_id"] == dispatch_id for o in self.pending):
            return
        self.pending.append({"dispatch_id": dispatch_id, "incident_id": message.get("incident_id"), "kind": body.get("kind"),
                             "remaining": max(1, int(body.get("qty") or 1)), "urgency": body.get("urgency"), "unit_id": body.get("unit_id"),
                             "issuer": message["from"], "stage": body.get("stage")})
        self.fill_pending()

    # -- decisions
    def fill_pending(self):
        sim = self.sim
        for order in list(self.pending):
            if not sim.world_incident_open(order["incident_id"]):
                self.pending.remove(order)
                continue
            while order["remaining"] > 0:
                offers = sim.world_offers(self.id, order["kind"], order["incident_id"])
                if order.get("unit_id"):   # the issuer named the unit (DisruptionOps choice); it must be ours
                    offers = [o for o in offers if o["unit_id"] == order["unit_id"]]
                if not offers:
                    sim.world_note_wait(order["incident_id"], f"No available {order['kind']} with a traversable route")
                    if order["dispatch_id"] not in self.reported_unavailable:
                        self.reported_unavailable.add(order["dispatch_id"])
                        self.send(order["issuer"], "decline",
                                  f"{self.label}: no available {order['kind']} for {order['incident_id']} yet; keeping the order open.",
                                  order["incident_id"], order["dispatch_id"], {"dispatch_id": order["dispatch_id"], "reason": "no unit available", "kind": order["kind"]})
                    break
                pick = min(offers, key=lambda o: (not o["exact"], o["eta_s"], o["unit_id"]))
                result = sim.world_send_unit(self.id, pick["unit_id"], order["incident_id"], order["kind"], order["dispatch_id"])
                if result is None:
                    break
                order["remaining"] -= 1
                self.reported_unavailable.discard(order["dispatch_id"])
                self.send(order["issuer"], "unit_status", f"{pick['unit_id']} ({pick['kind']}) dispatched to {order['incident_id']}, ETA {result['eta_s']:.0f} s.",
                          order["incident_id"], order["dispatch_id"],
                          {"unit_id": pick["unit_id"], "kind": pick["kind"], "name": result["name"], "agency": result["agency"], "state": "en_route",
                           "phys_state": "enroute", "incident_id": order["incident_id"], "dispatch_id": order["dispatch_id"], "eta_s": result["eta_s"],
                           "position": result["position"], "capacity": result["capacity"], "approach_m": result["approach_m"],
                           "transport": result["transport"], "why": result["why"]})
                self.known[pick["unit_id"]] = ("enroute", order["incident_id"], False)
        self.pending = [o for o in self.pending if o["remaining"] > 0]

    def tick(self):
        sim = self.sim
        self.fill_pending()
        units = sim.world_units(self.id)
        roster = []
        for unit in units:
            signature = (unit["state"], unit["incident_id"], bool(unit["acknowledged"]))
            previous = self.known.get(unit["id"], "new")
            self.known[unit["id"]] = signature
            if previous == "new":
                roster.append(unit)
            elif previous != signature:
                self.report_change(unit, previous)
        if roster:
            self.send("command_center", "unit_status", f"{self.label}: {len(roster)} unit(s) registered ({', '.join(sorted({u['kind'] for u in roster}))}).",
                      body={"roster": [self.unit_body(u) for u in roster], "agency": self.label})
        # A unit that is much nearer than the one en route takes over (the farther unit stands down).
        for unit in units:
            if unit["state"] == "enroute" and unit["incident_id"]:
                sim.world_reassign_nearer(self.id, unit["id"])
        return bool(roster)

    def unit_body(self, unit, **extra):
        return {"unit_id": unit["id"], "kind": unit["kind"], "name": unit["name"], "agency": unit["agency"], "state": LABEL.get(unit["state"], unit["state"]),
                "phys_state": unit["state"], "incident_id": unit["incident_id"], "eta_s": None if unit["eta"] is None else round(unit["eta"], 1),
                "position": unit["position"], "capacity": unit["capacity"], "approach_m": unit["approach_m"], "transport": unit["transport"], **extra}

    def report_change(self, unit, previous):
        state, incident_id = unit["state"], unit["incident_id"]
        old_state, old_incident, old_ack = previous
        if state == "on_scene" and unit["acknowledged"] and not old_ack:
            self.send("command_center", "handoff", f"{unit['id']} on scene at {incident_id} and acknowledged the briefing; scene handed over.", incident_id,
                      body=self.unit_body(unit, handoff=True))
            return
        if state == "awaiting_handoff":
            text = f"{unit['id']} ({unit['kind']}) arrived at {incident_id}."
        elif state == "on_scene":
            text = f"{unit['id']} working at {incident_id}."
        elif state == "evacuating":
            text = f"{unit['id']} transporting people from {incident_id}."
        elif state == "available":
            outcome = unit.get("last_outcome") or {}
            completed = old_incident is not None and outcome.get("incident_id") == old_incident
            text = f"{unit['id']} available again" + (f" after finishing {old_incident}." if completed else ".")
            self.send("command_center", "unit_status", text, old_incident,
                      body=self.unit_body(unit, released_from=old_incident, outcome="completed" if completed else "released"))
            return
        elif state == "enroute":
            text = f"{unit['id']} en route to {incident_id}, ETA {unit['eta'] or 0:.0f} s."
        else:
            text = f"{unit['id']} {LABEL.get(state, state)}."
        self.send("command_center", "unit_status", text, incident_id, body=self.unit_body(unit))


class AmbulanceService(AgencyNode):
    id, label, kinds = "ambulance_service", "AmbulanceService", ("ambulance",)


class FireService(AgencyNode):
    id, label, kinds = "fire_service", "FireService", ("fire_engine",)


class RescueService(AgencyNode):
    """Rescue teams and boats."""
    id, label, kinds = "rescue_service", "RescueService", ("rescue", "boat")


class PoliceService(AgencyNode):
    id, label, kinds = "police_service", "PoliceService", ("police",)


class PublicWorks(AgencyNode):
    """JCB/engineering, pumps, tankers, relief trucks and buses."""
    id, label, kinds = "public_works", "PublicWorks", ("engineering", "pump", "water_tanker", "supply_truck", "bus")


AGENCY_CLASSES = (AmbulanceService, FireService, RescueService, PoliceService, PublicWorks)
