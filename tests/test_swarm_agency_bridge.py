"""DisruptionOps-side swarm_agency patch module against the REAL swarm server (fake DisruptionOps doors)."""
import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from websockets.asyncio.client import connect

from swarm_ops.engine import OperationsSimulation
from swarm_ops.server import OperationsServer

MODULE = Path(__file__).resolve().parents[1] / "swarm_ops/integrations/disruptionops_patch/backend/app/drone/swarm_agency.py"
spec = importlib.util.spec_from_file_location("swarm_agency_patch", MODULE)
sa = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sa
spec.loader.exec_module(sa)


class FakePorts:
    def __init__(self, paused=False, officer=False):
        self.paused, self.officer = paused, officer
        self.events, self.responses, self.requests, self.reports = [], [], [], []
        self.lng, self.lat = sa.to_geo(120, 60)

    async def candidates(self):
        return [{"incident_id": "11111111-aaaa", "title": "Person stranded", "category": "person_stranded", "severity": 5,
                 "lng": self.lng, "lat": self.lat, "ward_id": "w-pc-01", "ward_name": "Akurdi", "needs": {"water_rescue": 1},
                 "report_count": 3, "life_safety": True, "hazards": ["flood"], "notes": "an elderly woman and two children",
                 "elderly_share": 0.09}]

    async def ground_units(self, needs, lng, lat):
        return [{"id": "PC-BOAT-01", "kind": "boat", "label": "Boat", "distance_m": 900}]

    def autonomy_paused(self):
        return self.paused

    async def officer_control(self, incident_id):
        return self.officer

    async def sent_before(self, incident_id, kind):
        return any(e[0] == "drone.request_sent" for e in self.events)

    async def record(self, kind, incident_id, ward_id, payload):
        self.events.append((kind, incident_id, payload))

    async def open_agency_request(self, incident_id, ward_id, capability, quantity, note):
        self.requests.append((capability, note))
        return "arq-1"

    async def record_reply(self, arid, kind, text):
        self.responses.append((arid, kind))
        return kind

    async def planned_assignments(self):
        return [{"incident_id": "11111111-aaaa", "category": "person_stranded", "ward_id": "w-pc-01", "people": 3, "severity": 5,
                 "lng": self.lng, "lat": self.lat,
                 "resources": [{"id": "PC-BOAT-01", "kind": "boat", "agency": "pcmcfire", "lng": self.lng, "lat": self.lat, "eta_s": 300}]},
                {"incident_id": "22222222-bbbb", "category": "unknown_report", "ward_id": "w-pc-01", "people": 1, "severity": 3,
                 "lng": self.lng, "lat": self.lat, "resources": []}]

    async def agency_request_status(self, arid):
        return "pending"

    async def file_report(self, **kw):
        self.reports.append(kw)
        return {"incident_id": "x", "created": False, "linked": True}


class PureTests(unittest.TestCase):
    def test_request_kind_is_data_driven(self):
        self.assertEqual(sa.request_kind("unknown_report", {"field_assessment": 1}, False), "assess")
        self.assertEqual(sa.request_kind("supply_shortage", {"supply_delivery": 1}, False), "supply")
        self.assertEqual(sa.request_kind("person_stranded", {"water_rescue": 1}, True), "sos")
        self.assertIsNone(sa.request_kind("flooded_road", {"dewatering": 1}, False))

    def test_geometry_round_trip_and_payload(self):
        lng, lat = sa.to_geo(120, 60)
        self.assertEqual(sa.to_local(lng, lat), [120.0, 60.0])
        payload = sa.build_payload({"incident_id": "abc", "kind": "sos", "category": "person_stranded", "people": 3, "severity": 5,
                                    "lng": lng, "lat": lat, "ward_id": "w", "vulnerable": 2})
        self.assertEqual(payload["position"], [120.0, 60.0])
        self.assertEqual(payload["requested_by"], "disruptionops:abc")
        self.assertEqual(payload["situation"]["vulnerable"], 2)

    def test_classify_event(self):
        e = sa.classify_event({"id": "r:3", "status": "completed", "request_id": "q", "incident_id": "I1", "message": "m"})
        self.assertEqual((e["kind"], e["reply_kind"]), ("drone.completed", None))
        self.assertIsNone(sa.classify_event({"id": "r:4", "kind": "detect"}))

    def test_reply_from_ack(self):
        self.assertEqual(sa.reply_from_ack({"accepted": True, "reason": "accepted; aircraft assigned", "assigned_drones": ["D1"], "eta_s": 42})[0], "accept_full")
        self.assertEqual(sa.reply_from_ack({"accepted": True, "reason": "accepted; aircraft queued"})[0], "delayed")
        self.assertEqual(sa.reply_from_ack({"accepted": False, "reason": "outside the swarm's operating area"})[0], "decline_jurisdiction")
        self.assertEqual(sa.reply_from_ack({"accepted": False, "reason": "swarm at capacity"})[0], "decline_capacity")
        self.assertEqual(sa.reply_from_ack({"accepted": True, "reply_kind": "accept_partial"})[0], "accept_partial")
        self.assertEqual(sa.reply_from_ack({}, ok=False)[0], "decline_capacity")

    def test_vulnerable(self):
        self.assertGreaterEqual(sa.vulnerable_count("elderly woman", 0.0, 4), 1)
        self.assertEqual(sa.vulnerable_count("water", 0.05, 4), 0)


class RoundTrip(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = OperationsSimulation(seed=7, fleet_size=5)
        self.server = OperationsServer(self.engine, control_token="c" * 24, port=0,
                                       journal_path=Path(self.folder.name) / "e.sqlite3", frame_hz=20)
        self.server.integration_token = "integration-token-for-tests"
        await self.server.start()
        self.url = f"ws://127.0.0.1:{self.server.port}/ops/ws"

    async def asyncTearDown(self):
        await self.server.stop()
        self.folder.cleanup()

    async def connected(self, ports, token="integration-token-for-tests"):
        agency = sa.SwarmAgency(url=self.url, token=token, ports=ports)
        ws = await connect(f"{self.url}?token={token}")
        agency._ws, agency.connected = ws, True
        agency._reader_task = asyncio.create_task(agency._reader(ws))
        await asyncio.sleep(0.3)        # first snapshot primes the seen-set
        return agency, ws

    async def test_request_is_sent_and_recorded(self):
        ports = FakePorts()
        agency, ws = await self.connected(ports)
        try:
            self.assertEqual(await agency.scan_once(), 1)
            kinds = [e[0] for e in ports.events]
            self.assertIn("drone.request_sent", kinds)
            self.assertTrue({"drone.accepted", "drone.declined"} & set(kinds))
            self.assertEqual(ports.requests[0][0], "aerial_assessment")
            self.assertIn(ports.responses[0][1], ("accept_full", "delayed", "decline_capacity", "decline_jurisdiction"))
            sent = next(e for e in ports.events if e[0] == "drone.request_sent")[2]
            self.assertEqual(sent["situation"]["available_ground_units"][0]["id"], "PC-BOAT-01")
            self.assertGreaterEqual(sent["situation"]["vulnerable"], 1)
            # the same incident is not requested twice
            self.assertEqual(await agency.scan_once(), 0)
            # later swarm events arrive over the socket and are ingested for our request
            for _ in range(24):
                if any(k in ("drone.launched", "drone.on_scene", "drone.briefing") for k, _, _ in ports.events):
                    break
                await asyncio.sleep(0.25)
            self.assertTrue(any(k in ("drone.launched", "drone.on_scene", "drone.briefing", "drone.declined", "drone.accepted") for k, _, _ in ports.events))
            for _, incident, payload in ports.events:
                if incident:
                    self.assertEqual(incident, "11111111-aaaa")
        finally:
            await ws.close()

    async def test_emergency_stop_and_officer_control_hold_requests(self):
        for kwargs in ({"paused": True}, {"officer": True}):
            ports = FakePorts(**kwargs)
            agency, ws = await self.connected(ports)
            try:
                self.assertEqual(await agency.scan_once(), 0)
                self.assertEqual(ports.events, [])
                self.assertEqual(agency.held, 1)
            finally:
                await ws.close()

    async def test_wrong_token_cannot_command(self):
        ports = FakePorts()
        agency, ws = await self.connected(ports, token="wrong-token")
        try:
            await agency.scan_once()
            self.assertTrue(any(e[0] == "drone.declined" for e in ports.events))   # ack ok=false is recorded as a decline
            self.assertEqual(ports.responses[0][1], "decline_capacity")
        finally:
            await ws.close()

    async def test_orchestrated_mode_sends_incidents_only_and_survives_unknown_action(self):
        ports = FakePorts()
        agency = sa.SwarmAgency(url="x", token="t", ports=ports, orchestrate=True)
        captured = []

        async def fake_command(action, payload, timeout=8.0):
            captured.append((action, payload))
            return {"type": "ack", "ok": True, "result": {}}
        agency.command = fake_command
        self.assertEqual(await agency.orchestrate_once(), 1)         # unknown_report skipped
        action, payload = captured[0]
        self.assertEqual(action, "orchestrate_incident")
        self.assertEqual(payload["external_id"], "11111111-aaaa")
        self.assertEqual(payload["assignments"]["resources"][0]["id"], "PC-BOAT-01")
        self.assertEqual(payload["assignments"]["drones"][0]["role"], "assess")
        self.assertEqual(await agency.orchestrate_once(), 0)         # unchanged: not re-sent
        # against the real server (no such action yet): rejected cleanly, then disabled
        real, ws = await self.connected(FakePorts())
        real.orchestrate = True
        try:
            self.assertIn(await real.orchestrate_once(), (0, 1))  # 1 once the engine implements orchestrate_incident
            self.assertTrue(real.orchestrate_disabled or real.orchestrated)
        finally:
            await ws.close()

    async def test_resource_request_honours_stop_and_files_through_intake(self):
        ports = FakePorts()
        agency = sa.SwarmAgency(url="x", token="t", ports=ports)
        agency.sent["r1"] = {"incident_id": "I", "ward_id": "w-pc-01", "agency_request_id": "arq-1", "ready": True,
                             "category": "person_stranded", "lng": 73.76, "lat": 18.65, "kind": "sos"}
        ev = {"swarm_event_id": "e1", "status": "resource_request", "kind": "drone.resource_request", "agency_action": None,
              "request_id": "r1", "message": "", "data": {"needed_kind": "ambulance", "qty": 1, "rationale": "casualty"}}
        await agency._ingest(ev)
        self.assertEqual(len(ports.reports), 1)
        self.assertIn("ambulance", ports.reports[0]["note"])
        ports.paused = True
        await agency._ingest({**ev, "swarm_event_id": "e2"})
        self.assertEqual(len(ports.reports), 1)
        self.assertIn("drone.resource_request_held", [e[0] for e in ports.events])


if __name__ == "__main__":
    unittest.main()
