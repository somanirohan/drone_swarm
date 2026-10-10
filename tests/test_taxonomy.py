"""build_taxonomy: SQL replay semantics and the committed taxonomy asset."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_taxonomy as bt  # noqa: E402

ASSET = ROOT / "assets" / "operations" / "disruptionops-taxonomy.json"
CLONE = Path("D:/drone_research_reference/DisruptionOps-latest/backend/migrations")


class ReplayTests(unittest.TestCase):
    def test_comments_quotes_and_upsert(self):
        a = ("-- it's a comment with a quote\n"
             "insert into capabilities (id, label, description) values ('x', 'X', 'it''s ok') on conflict (id) do nothing;\n"
             "insert into resource_kind_capabilities (kind_id, capability_id, effectiveness) values\n"
             "  ('k', 'x', 0.5) on conflict do nothing;\n")
        b = ("insert into capabilities (id, label) values ('x', 'ignored') on conflict (id) do nothing;\n"
             "insert into public.resource_kind_capabilities (kind_id, capability_id, effectiveness) values ('k','x',0.9)\n"
             "on conflict (kind_id, capability_id) do update set effectiveness = excluded.effectiveness;\n"
             "insert into lifeline_kinds (id, display_name, shelters_people) values ('p','Pump',false);\n"
             "update public.lifeline_kinds set serves_public = false where id in ('p');\n")
        st = bt.replay([("001_a.sql", a), ("002_b.sql", b)])
        self.assertEqual(st["capabilities"][("x",)]["description"], "it's ok")   # do nothing: first wins
        self.assertEqual(st["capabilities"][("x",)]["label"], "X")
        self.assertEqual(st["resource_kind_capabilities"][("k", "x")]["effectiveness"], 0.9)  # upsert: later wins
        self.assertIs(st["lifeline_kinds"][("p",)]["serves_public"], False)

    def test_row_width_mismatch_is_loud(self):
        with self.assertRaises(ValueError):
            bt.replay([("001.sql", "insert into capabilities (id, label) values ('a');")])

    def test_unicode_and_null(self):
        st = bt.replay([("019.sql", "insert into incident_category_keywords (category_id, phrase, weight) values ('c','मदद',1.0),('c','x',null);")])
        self.assertIn(("c", "मदद"), st["incident_category_keywords"])


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(ASSET.read_text(encoding="utf8"))

    def test_counts_and_source(self):
        c = self.data["counts"]
        self.assertEqual((c["hazard_types"], c["capabilities"], c["resource_kinds"], c["incident_categories"],
                          c["lifeline_kinds"], c["agencies"]), (5, 12, 11, 16, 11, 20))
        self.assertEqual(self.data["source"]["migration_count"], 38)
        self.assertRegex(self.data["source"]["commit"], r"^[0-9a-f]{40}$")

    def test_later_migrations_are_honoured(self):
        kinds = {k["id"]: k for k in self.data["resource_kinds"]}
        self.assertEqual(kinds["bus"]["capabilities"]["supply_delivery"], 0.45)       # 006 upsert
        self.assertIn("field_assessment", kinds["police"]["capabilities"])             # 032
        self.assertIn("helicopter", kinds)                                             # 035
        cats = {c["id"]: c for c in self.data["incident_categories"]}
        self.assertEqual(cats["fire"]["needs"], {"fire_suppression": 1, "medical_transport": 1, "traffic_control": 1})
        self.assertEqual(cats["unknown_report"]["needs"], {"field_assessment": 1})     # 023
        self.assertTrue(cats["person_stranded"]["life_safety"])
        life = {k["id"]: k for k in self.data["lifeline_kinds"]}
        self.assertFalse(life["substation"]["serves_public"])                          # 007 update
        self.assertTrue(life["hotel"]["shelters_people"])                              # 035

    def test_internal_references_resolve(self):
        caps = {c["id"] for c in self.data["capabilities"]}
        hazards = {h["id"] for h in self.data["hazard_types"]}
        for k in self.data["resource_kinds"]:
            self.assertLessEqual(set(k["capabilities"]), caps)
        for c in self.data["incident_categories"]:
            self.assertLessEqual(set(c["needs"]), caps)
            self.assertIn(c["hazard_id"], hazards)

    @unittest.skipUnless(CLONE.is_dir(), "DisruptionOps reference clone not present")
    def test_asset_matches_migrations(self):
        fresh = bt.build(CLONE, self.data["source"]["commit"])
        self.assertEqual(fresh, self.data)


if __name__ == "__main__":
    unittest.main()
