"""Multi-node architecture: the swarm reports, the orchestrator allocates, agency nodes own their units."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import swarm_ops.scenarios  # noqa: E402,F401
from swarm_ops.engine import OperationsSimulation  # noqa: E402

UNIT_WORDS = ("needed_kind", "required_kinds", "resource_requests", "ambulance_qty")


def request(sim, rid, **fields):
    payload = {"request_id": rid, "kind": "sos", "position": [-200, -300], "category": "medical", "people": 4, "needs": ["first_aid"],
               "priority": 4, "requested_by": "DisruptionOps"}
    payload.update(fields)
    return sim.command("agency_request", payload)


class SwarmReportsNotRequests(unittest.TestCase):
    def test_swarm_never_emits_unit_kinds_or_quantities(self):
        sim = OperationsSimulation(fleet_size=10)
        request(sim, "N1")
        for _ in range(120):
            sim.step(1)
        swarm_side = [m for m in sim.bus.recent(200) if m["from"] == "swarm" or m["from"].startswith("D")]
        self.assertTrue(any(m["kind"] == "situation_report" for m in swarm_side))
        for m in swarm_side:
            self.assertNotEqual(m["kind"], "resource_request")
            self.assertNotEqual(m["kind"], "dispatch")
            text = json.dumps(m["body"])
            self.assertFalse(any(word in text for word in UNIT_WORDS), (m["kind"], list(m["body"])))
            self.assertNotRegex(m["text"], r"\d+x (ambulance|rescue|fire_engine|boat)")
        self.assertFalse([e for e in sim.events if e["kind"] == "resource_request"])

    def test_situation_report_has_the_required_fields_and_a_nearby_ambulance(self):
        sim = OperationsSimulation(fleet_size=10)
        sim.step(10); sim.step(8)   # the second ambulance frees up
        ambulance = next(r for r in sim.resources if r["kind"] == "ambulance" and r["state"] == "available")
        spot = sim.nearest_road_point([ambulance["position"][0] + 150, ambulance["position"][1] + 150])
        request(sim, "N2", position=spot, people=3)
        sim.step(2)
        report = next(m for m in reversed(sim.bus.recent(200)) if m["kind"] == "situation_report" and m["request_id"] == "N2")["body"]
        for key in ("incident_id", "request_id", "observed_at", "revision", "people", "injured", "vulnerable", "hazards", "trend", "access", "nearby_units",
                    "lifelines", "drones_on_scene", "supplies_dropped", "confidence", "uncertainty_note", "text"):
            self.assertIn(key, report)
        self.assertTrue({"estimate", "low", "high"} <= set(report["people"]))
        unit = next(u for u in report["nearby_units"] if u["kind"] == "ambulance")
        self.assertGreater(unit["distance_m"], 0)
        self.assertIsNotNone(unit["eta_s"])

    def test_alerts_on_drone_faults(self):
        sim = OperationsSimulation(fleet_size=10)
        request(sim, "N3")
        drone = next(d for d in sim.drones if d["task_id"])
        self.assertTrue(sim.command("fault", {"drone_id": drone["id"], "kind": "link"})["ok"])
        other = next(d for d in sim.drones if d["task_id"] is None)
        self.assertTrue(sim.command("fault", {"drone_id": other["id"], "kind": "crash"})["ok"])
        sim.step(2)
        faults = [m["body"]["fault"] for m in sim.bus.recent(200) if m["kind"] == "alert" and m["body"].get("alert") == "drone_fault"]
        self.assertIn("link_loss", faults)
        self.assertIn("crash", faults)


class Orchestration(unittest.TestCase):
    def test_orchestrator_dispatches_and_agency_chooses_its_unit(self):
        sim = OperationsSimulation(fleet_size=10)
        sim.step(10); sim.step(8)
        result = request(sim, "N4")
        messages = sim.bus.recent(200)
        dispatch = next(m for m in messages if m["kind"] == "dispatch" and m["incident_id"] == result["incident_id"])
        self.assertEqual((dispatch["from"], dispatch["to"]), ("command_center", "ambulance_service"))
        status = next(m for m in messages if m["kind"] == "unit_status" and m["from"] == "ambulance_service" and m["incident_id"] == result["incident_id"])
        self.assertEqual(status["body"]["state"], "en_route")
        unit = next(r for r in sim.resources if r["id"] == status["body"]["unit_id"])
        self.assertEqual(unit["incident_id"], result["incident_id"])

    def test_external_mode_disables_the_orchestrator(self):
        sim = OperationsSimulation(fleet_size=10)
        sim.step(10); sim.step(8)
        before = len([m for m in sim.bus.recent(200) if m["kind"] == "dispatch"])
        self.assertTrue(sim.command("control", {"orchestrator": "external"})["ok"])
        self.assertFalse(sim.nodes.orchestrator.enabled)
        result = request(sim, "N5")
        for _ in range(30):
            sim.step(1)
        self.assertTrue(any(m["kind"] == "situation_report" and m["incident_id"] == result["incident_id"] for m in sim.bus.recent(200)))
        self.assertEqual(len([m for m in sim.bus.recent(200) if m["kind"] == "dispatch"]), before, "nobody dispatches until DisruptionOps does")
        self.assertEqual(sim._find_incident(result["incident_id"])["resource_requests"], [])
        incident_id = result["incident_id"]
        unit = next(r for r in sim.resources if r["kind"] == "ambulance" and r["state"] == "available")
        self.assertTrue(sim.command("assign", {"incident_id": incident_id, "resources": [{"id": unit["id"], "kind": "ambulance"}]})["ok"])
        for _ in range(10):
            sim.step(1)
        self.assertTrue(any(m["from"] == "ambulance_service" and m["kind"] == "unit_status" and m["incident_id"] == incident_id for m in sim.bus.recent(200)))

    def test_request_situation_forces_a_fresh_report(self):
        sim = OperationsSimulation(fleet_size=10)
        result = request(sim, "N6")
        sim.step(3)
        count = len([m for m in sim.bus.recent(200) if m["kind"] == "situation_report" and m["incident_id"] == result["incident_id"]])
        self.assertTrue(sim.command("request_situation", {"incident_id": result["incident_id"]})["ok"])
        self.assertEqual(len([m for m in sim.bus.recent(200) if m["kind"] == "situation_report" and m["incident_id"] == result["incident_id"]]), count + 1)


class ConcurrentNodes(unittest.IsolatedAsyncioTestCase):
    async def test_nodes_run_as_separate_tasks_and_only_exchange_messages(self):
        sim = OperationsSimulation(fleet_size=10)
        await sim.nodes.start()
        try:
            self.assertEqual(set(sim.nodes.tasks), {"swarm", "command_center", "ambulance_service", "fire_service", "rescue_service", "police_service", "public_works"})
            self.assertEqual(len({id(t) for t in sim.nodes.tasks.values()}), 7)
            request(sim, "A1")
            for _ in range(40):
                sim.step(.25)
                await asyncio.sleep(0.05)
            kinds = {m["kind"] for m in sim.bus.recent(200)}
            self.assertTrue({"situation_report", "dispatch", "unit_status"} <= kinds, kinds)
            nodes = sim.nodes.nodes
            for node in nodes:   # no node holds another node
                self.assertFalse([v for v in vars(node).values() if any(v is other for other in nodes if other is not node)])
            self.assertTrue(all(n.stats["ticks"] > 0 for n in nodes))
        finally:
            await sim.nodes.stop()


@unittest.skipUnless(__import__("importlib").util.find_spec("websockets"), "websockets missing")
class ClientKit(unittest.IsolatedAsyncioTestCase):
    async def test_swarm_client_round_trip(self):
        os.environ["OPS_INTEGRATION_TOKEN"] = "kit-token"
        from swarm_ops.server import OperationsServer
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "swarm_ops" / "integrations" / "client"))
        from swarm_client import SwarmClient
        folder = tempfile.TemporaryDirectory()
        server = OperationsServer(OperationsSimulation(fleet_size=10), journal_path=Path(folder.name) / "j.sqlite3", port=0)
        await server.start()
        client = SwarmClient(f"ws://127.0.0.1:{server.port}/ops/ws", "kit-token")
        reports = []
        client.on_situation_report = reports.append
        try:
            client.start()
            await client.wait_ready()
            ack = await client.send_control(orchestrator="external")
            self.assertTrue(ack["ok"], ack)
            ack = await client.send_agency_request("kit-1", [-200, -300], "medical", people=2, priority=4, requested_by="kit")
            self.assertTrue(ack["ok"] and ack["result"]["accepted"], ack)
            again = await client.send_agency_request("kit-1", [-200, -300], "medical", people=2, priority=4, requested_by="kit")
            self.assertTrue(again["ok"])
            for _ in range(60):
                if any(r["request_id"] == "kit-1" for r in reports):
                    break
                await asyncio.sleep(0.1)
            self.assertTrue(any(r["request_id"] == "kit-1" for r in reports))
        finally:
            await client.close()
            await server.stop()
            folder.cleanup()
            os.environ.pop("OPS_INTEGRATION_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
