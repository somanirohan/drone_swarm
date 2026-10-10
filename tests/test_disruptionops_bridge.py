"""Real HTTP stub + restart tests; no production service or credentials."""
import asyncio
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from swarm_ops.integrations.disruptionops_bridge import DisruptionOpsBridge, BridgeError, geo_position, local_position, observation_event, validate_external_state


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "outbox.sqlite"
        self.received = {}
        self.fail = False
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.headers.get("X-Swarm-Operations-Token") != "test-only-token":
                    self.send_error(403)
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if parent.fail:
                    self.send_error(503)
                    return
                parent.received.setdefault(body["id"], body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"accepted": True, "event_id": body["id"]}).encode())

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                run_id = parse_qs(urlparse(self.path).query)["run_id"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"mode": "simulation", "run_id": run_id,
                    "incidents": [], "resources": [{"id": "ambulance-1", "kind": "ambulance", "state": "on_site",
                    "location": geo_position([100, 200, 0]), "incident_id": "I1"}],
                    "handoffs": []}).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/v1"
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            bridge.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def bridge(self, **kwargs):
        b = DisruptionOpsBridge(url=self.url, token="test-only-token", db_path=self.db,
                                enabled=True, **kwargs)
        self.bridges.append(b)
        return b

    def event(self, event_id="runA:I1", run="runA"):
        return {"id": event_id, "run_id": run, "type": "incident.observed", "payload": {"test": 1}}

    async def test_restart_retains_pending_and_idempotency(self):
        first = self.bridge()
        self.assertTrue(first.enqueue_event(self.event()))
        self.assertFalse(first.enqueue_event(self.event()))
        first.close()
        second = self.bridge()
        self.assertEqual(await second.flush_once(), 1)
        self.assertEqual(await second.flush_once(), 0)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(second.status()["pending"], 0)

    async def test_retry_and_backoff_never_marks_failed_as_delivered(self):
        b = self.bridge()
        b.enqueue_event(self.event())
        self.fail = True
        self.assertEqual(await b.flush_once(now=100), 0)
        self.assertEqual(b.status()["pending"], 1)
        self.fail = False
        self.assertEqual(await b.flush_once(now=101), 0)
        self.assertEqual(await b.flush_once(now=103), 1)

    async def test_run_ids_isolate_repeat_local_incident_ids(self):
        b = self.bridge()
        b.enqueue_event(self.event("runA:I1", "runA"))
        b.enqueue_event(self.event("runB:I1", "runB"))
        self.assertEqual(await b.flush_once(), 2)
        self.assertEqual(len(self.received), 2)

    async def test_on_site_is_not_handoff_and_positions_are_measured_geo(self):
        updates = []
        b = self.bridge(on_update=updates.append)
        state = await b.poll_once("runA")
        self.assertEqual(state["handoffs"], [])
        self.assertAlmostEqual(state["resources"][0]["position"][0], 100, places=6)
        self.assertAlmostEqual(state["resources"][0]["position"][1], 200, places=6)
        self.assertEqual(len(updates), 1)

    async def test_reused_event_id_changed_body_is_rejected(self):
        b = self.bridge()
        b.enqueue_event(self.event())
        changed = self.event()
        changed["payload"] = {"test": 2}
        with self.assertRaises(ValueError):
            b.enqueue_event(changed)

    async def test_hidden_incidents_not_exported_repeat_snapshots_deduped(self):
        b = self.bridge()
        snap = {"run_id": "runA", "incidents": [
            {"id": "hidden", "position": [0,0,0], "status": "hidden"},
            {"id": "I1", "position": [100,200,0], "status": "detected", "briefing": {"revision": 1}}]}
        self.assertEqual(b.enqueue_snapshot(snap), 1)
        self.assertEqual(b.enqueue_snapshot(snap), 0)
        self.assertEqual(await b.flush_once(), 1)
        snap["incidents"][1]["briefing"]["revision"] = 2
        self.assertEqual(b.enqueue_snapshot(snap), 1)
        self.assertEqual(b.enqueue_snapshot(snap), 0)
        self.assertEqual(await b.flush_once(), 1)
        self.assertEqual({e["type"] for e in self.received.values()}, {"incident.observed", "drone.briefing"})

    async def test_disabled_bridge_makes_no_files_or_requests(self):
        path = Path(self.tmp.name) / "disabled.sqlite"
        b = DisruptionOpsBridge(url="", token="", db_path=path)
        self.assertFalse(b.enqueue_event(self.event()))
        self.assertEqual(await b.flush_once(), 0)
        self.assertFalse(path.exists())

    async def test_non_simulation_response_is_refused(self):
        async def wrong(*args):
            return {"mode": "live", "run_id": "runA"}
        b = self.bridge(request_fn=wrong)
        with self.assertRaises(BridgeError):
            await b.poll_once("runA")

    async def test_actual_engine_snapshot_is_exported_without_hidden_dispatch_claims(self):
        from swarm_ops.engine import OperationsSimulation
        b = self.bridge()
        sim = OperationsSimulation()
        self.assertEqual(b.enqueue_snapshot(sim.snapshot()), 2)
        self.assertEqual(b.enqueue_snapshot(sim.snapshot()), 0)
        self.assertEqual(await b.flush_once(), 2)
        categories = {e["payload"]["category"] for e in self.received.values()}
        self.assertEqual(categories, {"person_stranded", "unknown_report"})

    async def test_generic_medical_does_not_become_heat_casualty(self):
        event = observation_event(run_id="test", incident={"id":"I1", "category":"medical",
            "position":[0,0,0], "title":"Injured person", "briefing":None})
        self.assertEqual(event["payload"]["category"], "unknown_report")

    async def test_external_state_rejects_bad_coordinates_status_and_collections(self):
        base = {"mode":"simulation", "run_id":"runA", "resources":[{
            "id":"A1", "kind":"ambulance", "state":"on_site", "position":[100,200,0]}]}
        import copy
        for position in ([float("nan"),0,0], [0,float("inf"),0], [1,2], [1,2,3,4], [True,0,0], [60000,0,0]):
            bad = copy.deepcopy(base)
            bad["resources"][0]["position"] = position
            with self.assertRaises(BridgeError):
                validate_external_state(bad, "runA")
        bad = copy.deepcopy(base)
        bad["resources"][0]["state"]="fire_missiles"
        with self.assertRaises(BridgeError):
            validate_external_state(bad, "runA")
        with self.assertRaises(BridgeError):
            validate_external_state({"mode":"simulation","run_id":"runA","resources":[{}]*501}, "runA")

    async def test_invalid_external_import_never_invokes_callback(self):
        updates = []
        async def malformed(*args):
            return {"mode":"simulation", "run_id":"runA", "resources":[{
                "id":"A1", "kind":"ambulance", "state":"on_site", "position":[float("nan"),0,0]}]}
        b = self.bridge(request_fn=malformed, on_update=updates.append)
        with self.assertRaises(BridgeError):
            await b.poll_once("runA")
        self.assertEqual(updates, [])

    async def test_handoff_requires_matching_current_on_site_assignment(self):
        payload = {"mode":"simulation", "run_id":"runA", "resources":[{
            "id":"A1", "kind":"ambulance", "state":"en_route", "position":[100,200,0], "incident_id":"I1"}],
            "handoffs":[{"incident_id":"I1","resource_id":"A1","briefing_revision":1,"accepted":True}]}
        self.assertEqual(validate_external_state(payload, "runA")["handoffs"], [])
        payload["resources"][0]["state"]="on_site"
        self.assertEqual(len(validate_external_state(payload, "runA")["handoffs"]), 1)


if __name__ == "__main__":
    unittest.main()
