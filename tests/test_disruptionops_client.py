"""Live reference-data mode against a local stub server (never a real DisruptionOps)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from swarm_ops import disruptionops_client as client
from swarm_ops.config import Settings

TAX = {"city": {"id": "pune"}, "hazards": [{"id": "flood", "displayName": "Flood", "sortOrder": 10}],
       "capabilities": [{"id": "water_rescue", "label": "Water rescue", "description": ""}],
       "resourceKinds": [{"id": "boat", "displayName": "Boat", "defaultCapacity": 6, "capabilities": ["water_rescue"]},
                         {"id": "newkind", "displayName": "New", "defaultCapacity": 1, "capabilities": ["water_rescue"]}],
       "lifelineKinds": [{"id": "shelter", "displayName": "Shelter", "sheltersPeople": True}],
       "incidentCategories": [{"id": "person_stranded", "displayName": "Person stranded", "hazardId": "flood",
                               "dedupRadiusM": 150, "dedupWindowMin": 15, "baseSeverity": 5, "lifeSafety": True,
                               "needs": {"water_rescue": 1}, "keywords": {"stranded": 1.0}}],
       "agencies": [{"id": "pcmc", "cityId": "pune", "name": "PCMC", "shortName": "PCMC", "kind": "government",
                     "jurisdiction": "city", "active": True, "capabilities": ["water_rescue"]}]}
WARDS = [{"id": "w-pc-01", "number": "P01", "name": "Akurdi", "centroid": [73.7624, 18.6527], "population": 64000},
         {"id": "w-gzb-01", "number": "101", "name": "elsewhere", "centroid": [77.3, 28.6], "population": 1}]
LIFE = [{"id": "lf-pc-s1", "kind": "shelter", "name": "School hall", "location": [73.763, 18.653], "wardId": "w-pc-01", "capacity": 200}]
RES = [{"id": "PC-BOAT-1", "kind": "boat", "label": "Boat 1", "operator": "PCMC", "baseLocation": [73.7625, 18.6528],
        "location": [73.7625, 18.6528], "capacity": 6, "status": "available"},
       {"id": "NDRF-1", "kind": "boat", "label": "other", "operator": "x", "baseLocation": [73.1, 18.1], "location": [73.1, 18.1],
        "capacity": 6, "status": "available"}]
INC = [{"id": "u1", "title": "t", "category": "person_stranded", "hazard": "flood", "wardId": "w-pc-01",
        "location": [73.7625, 18.6528], "severity": 5, "status": "open", "reportCount": 2},
       {"id": "u2", "title": "done", "category": "fire", "hazard": "fire", "wardId": "w-pc-01",
        "location": [73.7625, 18.6528], "severity": 1, "status": "resolved", "reportCount": 1}]


class Stub(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Stub.seen.append((self.path, self.headers.get("Authorization")))
        path = self.path.split("?")[0]
        body = {"/api/v1/taxonomy": TAX, "/api/v1/wards": WARDS, "/api/v1/lifelines": LIFE,
                "/api/v1/incidents": INC, "/api/v1/resources": RES}.get(path)
        if path == "/api/v1/resources" and self.headers.get("Authorization") != "Bearer secret-token":
            self.send_response(403)
            self.end_headers()
            return
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        client.clear_cache()
        Stub.seen.clear()

    def test_assets_when_unconfigured(self):
        data = client.load_reference_data(Settings.from_env({}))
        self.assertEqual(data["mode"], "assets")
        self.assertEqual(data["taxonomy"]["counts"]["resource_kinds"], 11)
        self.assertTrue(data["catalog"]["resources"])
        self.assertEqual(Stub.seen, [])

    def test_live_with_token(self):
        s = Settings.from_env({"DISRUPTIONOPS_URL": self.url + "/", "DISRUPTIONOPS_TOKEN": "secret-token"})
        data = client.load_reference_data(s)
        self.assertEqual(data["mode"], "live", data["errors"])
        tax = data["taxonomy"]
        self.assertEqual(tax["resource_kinds"][0]["capabilities"], {"water_rescue": 1.0})
        self.assertEqual(tax["resource_kinds"][1]["capabilities"], {"water_rescue": 1.0})   # unknown kind defaults to 1.0
        self.assertEqual(tax["incident_categories"][0]["keywords"], [{"phrase": "stranded", "weight": 1.0}])
        self.assertEqual(tax["kinds_providing"]["water_rescue"], {"boat": 1.0, "newkind": 1.0})
        cat = data["catalog"]
        self.assertEqual([w["source_id"] for w in cat["wards"]], ["w-pc-01"])               # other cities dropped
        self.assertEqual([r["source_id"] for r in cat["resources"]], ["PC-BOAT-1"])
        self.assertTrue(cat["resources"][0]["on_map"])
        self.assertEqual(cat["lifelines"][0]["capacity"], 200)
        self.assertEqual([i["id"] for i in data["incidents"]], ["u1"])                      # resolved dropped
        self.assertIn(("/api/v1/resources", "Bearer secret-token"), Stub.seen)

    def test_live_without_token_keeps_asset_units(self):
        data = client.load_reference_data(Settings.from_env({"DISRUPTIONOPS_URL": self.url}))
        self.assertEqual(data["mode"], "live")
        self.assertEqual(len(data["catalog"]["resources"]), 32)
        self.assertTrue(any("staff-only" in e for e in data["errors"]))
        self.assertNotIn("/api/v1/resources", [p for p, _ in Stub.seen])

    def test_bad_token_degrades_units_only(self):
        s = Settings.from_env({"DISRUPTIONOPS_URL": self.url, "DISRUPTIONOPS_TOKEN": "wrong"})
        data = client.load_reference_data(s)
        self.assertEqual(data["mode"], "live")
        self.assertEqual(len(data["catalog"]["resources"]), 32)

    def test_unreachable_falls_back_and_caches(self):
        s = Settings.from_env({"DISRUPTIONOPS_URL": "http://127.0.0.1:1"})
        with self.assertLogs("swarm_ops.disruptionops", level="WARNING"):
            data = client.load_reference_data(s)
        self.assertEqual(data["mode"], "assets-fallback")
        self.assertEqual(data["taxonomy"]["counts"]["agencies"], 20)
        self.assertEqual(client.load_reference_data(s)["mode"], "assets-fallback")

    def test_cache_ttl(self):
        s = Settings.from_env({"DISRUPTIONOPS_URL": self.url})
        client.load_reference_data(s)
        n = len(Stub.seen)
        client.load_reference_data(s)
        self.assertEqual(len(Stub.seen), n)
        client.load_reference_data(s, force=True)
        self.assertGreater(len(Stub.seen), n)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_summary_hides_secrets(self):
        s = Settings.from_env({"PORT": "10000", "HOST": "0.0.0.0", "OPS_CONTROL_TOKEN": "c" * 30,
                   "ALLOWED_ORIGINS": "https://A.example/, https://b.example"})
        self.assertEqual((s.port, s.host), (10000, "0.0.0.0"))
        self.assertEqual(s.allowed_origins, ("https://a.example", "https://b.example"))
        text = json.dumps(s.public_summary())
        self.assertNotIn("c" * 30, text)
        self.assertTrue(any("OPS_INTEGRATION_TOKEN" in p for p in s.problems()))

    def test_validation(self):
        for env in ({"PORT": "x"}, {"WORLD": "mars"}, {"DISRUPTIONOPS_URL": "ftp://x"}, {"ALLOWED_ORIGINS": "nohost"}, {"LOG_LEVEL": "LOUD"}):
            with self.assertRaises(ValueError):
                Settings.from_env(env)

    def test_origin_policy(self):
        s = Settings.from_env({"ALLOWED_ORIGINS": "https://app.example"})
        self.assertTrue(s.origin_allowed(None, "h"))
        self.assertTrue(s.origin_allowed("https://swarm.example", "swarm.example"))
        self.assertTrue(s.origin_allowed("https://app.example/", "swarm.example"))
        self.assertFalse(s.origin_allowed("https://evil.example", "swarm.example"))


if __name__ == "__main__":
    unittest.main()
