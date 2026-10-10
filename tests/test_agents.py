"""Agent architecture: decisions, messages, plan_response (reasoner + traced fallback), control, traffic."""
import json
import math
import unittest

from swarm_ops.agents import MESSAGE_KINDS
from swarm_ops.engine import OperationsSimulation


def request(sim, request_id, **fields):
    payload = {"request_id": request_id, "kind": "sos", "position": [-100, -200], "category": "medical", "people": 2,
               "needs": ["first_aid"], "priority": 4, "requested_by": "DisruptionOps"}
    payload.update(fields)
    return sim.command("agency_request", payload)


class AgentSnapshotTests(unittest.TestCase):
    def test_snapshot_decisions_messages_and_per_drone_last_decision(self):
        sim = OperationsSimulation(fleet_size=10)
        for _ in range(40): sim.step(1)
        snap = sim.snapshot(include_world=False)
        json.dumps(snap, allow_nan=False)
        self.assertTrue(snap["decisions"]) ; self.assertLessEqual(len(snap["decisions"]), 100)
        for d in snap["decisions"]:
            self.assertTrue({"t", "agent", "decision", "reason", "options"} <= set(d))
            for o in d["options"]: self.assertEqual(set(o), {"id", "score"})
        self.assertLessEqual(len(snap["messages"]), 200)
        for m in snap["messages"]:
            self.assertEqual(set(m), {"id", "t", "from", "to", "kind", "incident_id", "request_id", "text", "body"})
            self.assertIn(m["kind"], MESSAGE_KINDS)
        self.assertTrue(any(d["last_decision"] for d in snap["drones"]))
        awards = [d for d in snap["decisions"] if d["decision"] == "award"]
        self.assertTrue(awards)
        winner = awards[0]["reason"].split()[0]
        self.assertEqual(awards[0]["options"][0]["id"], winner, "lowest bid wins and is listed first")
        self.assertTrue(all(i["situation"] for i in snap["incidents"]))
        situation = snap["incidents"][0]["situation"]
        for key in ("people", "vulnerable", "injured", "crowd_density_per_100m2", "hazards", "trend", "lifelines", "units", "briefing_revision"):
            self.assertIn(key, situation)
        self.assertEqual(snap["control"]["autonomy"], "running")

    def test_command_centre_traffic_is_on_the_bus(self):
        sim = OperationsSimulation(fleet_size=10)
        result = request(sim, "ORCH-BUS", people=5, needs=["first_aid", "medical"])
        self.assertTrue(result["accepted"])
        messages = sim.bus.recent()
        inbound = [m for m in messages if m["request_id"] == "ORCH-BUS" and m["from"] == "command_center" and m["kind"] == "task"]
        reply = [m for m in messages if m["request_id"] == "ORCH-BUS" and m["to"] == "command_center" and m["kind"] == "accept"]
        self.assertTrue(inbound and reply)
        self.assertTrue(any(m["kind"] == "resource_request" and m["incident_id"] == result["incident_id"] for m in messages))
        self.assertTrue(any(m["kind"] == "task" and m["from"] == "swarm" and m["to"].startswith("D") for m in messages), "award to a drone")
        self.assertTrue(any(m["kind"] == "accept" and m["from"].startswith("D") for m in messages), "drone accepts")
        for _ in range(120): sim.step(1)
        self.assertTrue(any(m["kind"] == "status" and m["to"] == "command_center" for m in sim.bus.recent()))
        self.assertTrue(any(m["kind"] == "report" and m["from"].startswith("D") for m in sim.bus.recent()))
        declined = request(sim, "ORCH-OUT", position=[9000, 0])
        self.assertFalse(declined["accepted"])
        self.assertTrue(any(m["kind"] == "decline" and m["request_id"] == "ORCH-OUT" for m in sim.bus.recent()))


class PlanResponseTests(unittest.TestCase):
    def test_reasoner_plan_replaces_required_kinds_on_reported_incident(self):
        sim = OperationsSimulation(fleet_size=10)
        seen = []
        def reasoner(situation, context):
            seen.append((situation, context))
            return {"agency_requests": [{"kind": "rescue_team", "qty": 2, "urgency": "immediate", "why": "trapped and injured"},
                                        {"kind": "warp_drive", "qty": 1}],
                    "drones": [{"role": "assess", "payload": "none"}, {"role": "supply", "payload": "first_aid"}],
                    "assessment": {"severity": 5}, "rationale": "test plan"}
        sim.coordinator.reasoner = reasoner
        result = request(sim, "ORCH-PLAN", category="trapped", people=4)
        incident = sim._find_incident(result["incident_id"])
        self.assertEqual(incident["required_kinds"], ["rescue"])
        self.assertEqual(incident["severity"], 5)
        rescue = [q for q in incident["resource_requests"] if q["needed_kind"] == "rescue"]
        self.assertEqual(sum(q["qty"] for q in rescue), 2)
        self.assertEqual(rescue[0]["urgency"], "critical")
        trace = [d for d in sim.decisions.recent() if d["decision"] == "plan_response" and d.get("incident_id") == incident["id"]][-1]
        self.assertFalse(trace["fallback"]); self.assertEqual(trace["source"], "reasoner")
        self.assertTrue(trace["id"].startswith("dec-")); self.assertIsNotNone(trace["latency_ms"])
        self.assertIn("warp_drive", trace["reason"], "invalid plan items are dropped and reported")
        situation, context = seen[-1]
        self.assertEqual(situation["incident_id"], incident["id"])
        self.assertTrue(context["resources"] and context["drones"])

    def test_real_reasoner_deltas_never_double_request_a_need(self):
        sim = OperationsSimulation(fleet_size=10)
        result = request(sim, "ORCH-DELTA", category="fire", people=6, needs=["first_aid"])
        incident = sim._find_incident(result["incident_id"])
        def totals():
            out = {}
            for q in incident["resource_requests"]:
                if q["status"] in ("open", "fulfilled"): out[q["needed_kind"]] = out.get(q["needed_kind"], 0) + q["qty"]
            return out
        before = totals()
        for _ in range(3): sim.coordinator.plan_response(incident, "replan")
        self.assertEqual(totals(), before, "an unchanged situation raises nothing new")
        traces = [d for d in sim.decisions.recent() if d["decision"] == "plan_response"]
        self.assertTrue(all(d["source"] == "reasoner" and not d["fallback"] for d in traces))
        ids = [d["id"] for d in sim.decisions.recent()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sim.snapshot(include_world=False)["engine"]["decisions"][-1]["id"], ids[-1])

    def test_fallback_is_traced_when_reasoner_missing_or_failing(self):
        sim = OperationsSimulation(fleet_size=10)
        sim.coordinator.reasoner = None
        incident = sim._find_incident(request(sim, "ORCH-FB")["incident_id"])
        self.assertEqual(incident["required_kinds"], ["ambulance"], "taxonomy: medical -> medical_transport -> ambulance")
        trace = [d for d in sim.decisions.recent() if d["decision"] == "plan_response"][-1]
        self.assertTrue(trace["fallback"]); self.assertIn("reasoner unavailable", trace["reason"])
        def broken(situation, context): raise RuntimeError("boom")
        sim.coordinator.reasoner = broken
        incident = sim._find_incident(request(sim, "ORCH-FB2", position=[200, 200], category="fire")["incident_id"])
        self.assertIn("fire_engine", incident["required_kinds"])
        trace = [d for d in sim.decisions.recent() if d["decision"] == "plan_response"][-1]
        self.assertTrue(trace["fallback"]); self.assertIn("boom", trace["reason"])

    def test_briefing_triggers_replan_and_prescribed_scenarios_keep_their_kinds(self):
        sim = OperationsSimulation(fleet_size=10)
        before = [list(i["required_kinds"]) for i in sim.incidents]
        for _ in range(60): sim.step(1)
        self.assertEqual([i["required_kinds"][:len(b)] for i, b in zip(sim.incidents, before)], before)
        self.assertTrue(any(d["decision"] == "plan_response" and d["reason"].startswith("briefing r") for d in sim.decisions.recent()))

    def test_police_is_only_dispatched_when_a_plan_asks(self):
        sim = OperationsSimulation(fleet_size=5)
        police = sim.add_resource("police", sim.nearest_road_point([-300, -300]), id="PC-POL-01")
        self.assertEqual(police["model"], "police")
        for _ in range(5): sim.step(1)
        self.assertIsNone(police["incident_id"])
        sim.coordinator.reasoner = lambda s, c: {"agency_requests": [{"kind": "police", "qty": 1, "why": "traffic control"}]}
        incident = sim._find_incident(request(sim, "ORCH-POL", position=[-200, -300])["incident_id"])
        self.assertEqual(police["incident_id"], incident["id"])


class ControlAndTrafficTests(unittest.TestCase):
    def test_control_pause_blocks_new_launches_and_requests(self):
        sim = OperationsSimulation(fleet_size=10)
        self.assertFalse(sim.command("control", {"autonomy": "sideways"})["ok"])
        self.assertTrue(sim.command("control", {"autonomy": "paused", "reason": "airspace closed"})["ok"])
        self.assertEqual(sim.snapshot(include_world=False)["control"]["autonomy"], "paused")
        result = request(sim, "ORCH-PAUSED")
        self.assertFalse(result["accepted"]); self.assertIn("paused", result["reason"])
        airborne = {d["id"] for d in sim.drones if d["state"] in ("enroute", "observing")}
        sim.incidents[0]["drone_id"] = None  # an incident left without aircraft
        for _ in range(5): sim.step(1)
        self.assertIsNone(sim.incidents[0]["drone_id"]); self.assertIn("paused", sim.incidents[0]["wait_reason"])
        self.assertTrue(sim.command("control", {"autonomy": "running"})["ok"])
        self.assertTrue(request(sim, "ORCH-RUN")["accepted"])
        self.assertTrue(airborne)

    def test_traffic_is_bounded_deterministic_avoids_closures_and_stops_near_incidents(self):
        a = OperationsSimulation(fleet_size=5); b = OperationsSimulation(fleet_size=5)
        for _ in range(30): a.step(1); b.step(1)
        ta, tb = a.snapshot(include_world=False)["traffic"], b.snapshot(include_world=False)["traffic"]
        self.assertEqual(ta, tb); self.assertTrue(0 < len(ta) <= 80)
        for v in ta:
            self.assertEqual(set(v), {"id", "kind", "position", "heading", "speed_mps"})
            self.assertIn(v["kind"], ("car", "auto", "bike", "bus"))
        for v in a.traffic.vehicles:
            self.assertNotIn(tuple(sorted((v["a"], v["b"]))), a._blocked_edges)
        hazards = [i["position"] for i in a.incidents if i["status"] != "resolved"]
        for v in ta:
            if min(math.dist(v["position"][:2], p[:2]) for p in hazards) < 20:
                self.assertLess(v["speed_mps"], 3.0)
        a.traffic_vehicles = 0; a.command("reset")
        self.assertEqual(a.snapshot(include_world=False)["traffic"], [])


if __name__ == "__main__":
    unittest.main()
