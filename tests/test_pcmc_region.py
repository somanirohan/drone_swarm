"""PCMC region data (assets/operations/pcmc-region.json) and the pccoe_pawana_night scenario definition."""
from __future__ import annotations

import json
import math
import re
import unittest
from pathlib import Path

from swarm_ops.scenarios import pcmc

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "assets" / "operations" / "agency-catalog.json").read_text(encoding="utf8"))
WORLD = pcmc.region()


def inside(p, poly):
    result, j = False, len(poly) - 1
    for i, q in enumerate(poly):
        r = poly[j]
        if (q[1] > p[1]) != (r[1] > p[1]) and p[0] < (r[0] - q[0]) * (p[1] - q[1]) / (r[1] - q[1]) + q[0]:
            result = not result
        j = i
    return result


def ward_at(p):
    hits = [w["id"] for w in WORLD["wards"] if inside(p, w["polygon"])]
    return hits[0] if len(hits) == 1 else None


class RegionData(unittest.TestCase):
    def test_world_shape(self):
        self.assertEqual(WORLD["id"], "pcmc-region")
        self.assertEqual(WORLD["kind"], "region")
        for key in ("anchor", "bounds", "terrain", "buildings", "roads", "river", "river_width", "bases", "shelters", "no_fly", "metadata"):
            self.assertIn(key, WORLD)
        x0, y0, x1, y1 = WORLD["bounds"]
        self.assertAlmostEqual(x1 - x0, 13728, delta=50)
        self.assertAlmostEqual(y1 - y0, 14042, delta=50)
        self.assertLess((ROOT / "assets" / "operations" / "pcmc-region.json").stat().st_size, 6_000_000)

    def test_all_32_units_at_catalog_positions(self):
        units = {u["source_id"]: u for u in WORLD["units"]}
        catalog = {r["source_id"]: r for r in CATALOG["resources"]}
        self.assertEqual(len(catalog), 32)
        for uid, entry in catalog.items():
            self.assertRegex(uid, r"^PC-[A-Z]+-\d\d$")
            self.assertIn(uid, units)
            self.assertLessEqual(math.dist(units[uid]["local_xy"], entry["local_xy"]), 5.0, uid)
            self.assertEqual(units[uid]["kind"], entry["kind"])
        x0, y0, x1, y1 = WORLD["bounds"]
        for u in units.values():
            self.assertTrue(x0 <= u["local_xy"][0] <= x1 and y0 <= u["local_xy"][1] <= y1, u["source_id"])
            if u["kind"] == "boat":
                self.assertLess(u["river_distance_m"], 600, u["source_id"])  # boats 01/03 are staged 374/498 m off the Pawana; they launch at river_xy
            else:
                self.assertLess(u["road_distance_m"], 150, u["source_id"])

    def test_wards_tile_the_envelope(self):
        self.assertEqual(len(WORLD["wards"]), 14)
        x0, y0, x1, y1 = WORLD["bounds"]
        self.assertAlmostEqual(sum(w["area_sq_km"] for w in WORLD["wards"]), (x1 - x0) * (y1 - y0) / 1e6, delta=0.2)
        for w in WORLD["wards"]:
            self.assertEqual(ward_at(w["centroid"]), w["id"])
            self.assertGreater(w["population"], 0)
        # Every sampled point of the envelope lies in exactly one ward.
        for i in range(1, 20):
            for j in range(1, 20):
                p = [x0 + (x1 - x0) * (i + .37) / 20, y0 + (y1 - y0) * (j + .41) / 20]
                self.assertIsNotNone(ward_at(p), p)

    def test_river_and_water(self):
        self.assertGreater(len(WORLD["river"]), 20)
        pawana = WORLD["waterways"][0]
        self.assertIn("Pawana", pawana["name"])
        length = sum(math.dist(a, b) for a, b in zip(pawana["points"], pawana["points"][1:]))
        self.assertGreater(length, 15000)
        self.assertGreaterEqual(len(WORLD["river_crossings"]), 5)
        # The Pawana runs through the flood-prone wards named in migration 029.
        wards = {ward_at(p) for p in pawana["points"]}
        self.assertTrue({"w-pc-03", "w-pc-05", "w-pc-07"} <= wards)

    def test_flood_zones_cover_river_sites_not_stations(self):
        zones = WORLD["flood_zones"]
        self.assertTrue({z["reach"] for z in zones} == {"flood-ravet", "flood-chinchwad", "flood-pimpri"})

        def wet(p):
            return any(math.dist(p, z["center"]) < z["radius"] for z in zones)
        spots = {h["id"]: h for h in WORLD["hotspots"]}
        self.assertTrue(wet(spots["ravet-bridge"]["site_xy"]) and wet(spots["morya-ghat"]["site_xy"]))
        for hid in ("akurdi-underpass", "pimpri-camp", "kudalwadi-chemical", "birla-marg", "sb-patil-shelter"):
            self.assertFalse(wet(spots[hid]["site_xy"]), hid)
        for unit in WORLD["units"]:
            if unit["kind"] != "boat":
                self.assertFalse(wet(unit["road_xy"]), unit["source_id"])

    def test_lifelines_and_roads(self):
        self.assertEqual(len(WORLD["lifelines"]), 16)
        self.assertEqual({l["kind"] for l in WORLD["lifelines"]}, {"hospital", "shelter", "relief_centre", "medical_camp", "water_point"})
        self.assertGreater(len(WORLD["roads"]), 2000)
        ends = {}
        for road in WORLD["roads"]:
            self.assertGreaterEqual(len(road["points"]), 2)
            for p in (road["points"][0], road["points"][-1]):
                ends[tuple(p)] = ends.get(tuple(p), 0) + 1
        # Connected network: union-find over shared end points gives one component.
        parent = {}

        def find(a):
            parent.setdefault(a, a)
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for road in WORLD["roads"]:
            a, b = find(tuple(road["points"][0])), find(tuple(road["points"][-1]))
            parent[a] = b
        self.assertEqual(len({find(k) for k in ends}), 1)


class PawanaNightScenario(unittest.TestCase):
    def test_mirrors_disruptionops_reports(self):
        expected = [("person_stranded", 3), ("person_stranded", 2), ("flooded_road", 2), ("waterlogging", 1), ("fire", 2), ("air_quality", 1),
                    ("structural_damage", 2), ("power_line", 1), ("fallen_tree", 1), ("shelter_full", 1), ("supply_shortage", 1)]
        self.assertEqual([(r["category"], r["callers"]) for r in pcmc.PAWANA_NIGHT], expected)
        for r in pcmc.PAWANA_NIGHT:
            self.assertIn(pcmc.DISRUPTIONOPS_CATEGORIES[r["category"]], ("medical", "flood", "fire", "trapped", "blocked_route"))
            # Reports carry what a caller says, never a prescribed response.
            self.assertFalse({"required", "required_kinds", "units"} & set(r))
        texts = {r["hotspot"]: r for r in pcmc.PAWANA_NIGHT}
        self.assertEqual(texts["morya-ghat"]["vulnerable"], ["elderly"])
        self.assertIn("trapped", texts["nigdi-wall"]["injuries"])
        self.assertIn("live_wire", texts["pccoe-lane"]["hazards"])
        self.assertIn("gas", texts["kudalwadi-chemical"]["hazards"])

    def test_incident_sites_in_the_named_wards(self):
        spots = {h["id"]: h for h in WORLD["hotspots"]}
        names = {w["id"]: w["name"] for w in WORLD["wards"]}
        keyword = {"ravet-bridge": "Ravet", "morya-ghat": "Chinchwad Gaon", "akurdi-underpass": "Akurdi", "pimpri-camp": "Pimpri",
                   "kudalwadi-chemical": "Chikhli", "sane-chowk": "Chikhli", "nigdi-wall": "Nigdi", "pccoe-lane": "Akurdi",
                   "birla-marg": "Thergaon", "sb-patil-shelter": "Ravet", "ravet-relief-camp": "Ravet"}
        for r in pcmc.PAWANA_NIGHT:
            hid = r["hotspot"]
            spot = spots[hid]
            self.assertIn(keyword[hid], names[spot["named_ward"]])
            site_ward = ward_at(spot["site_xy"])
            self.assertEqual(site_ward, spot["site_ward_id"])
            if hid == "ravet-relief-camp":
                # The real camp lies just inside the Akurdi cell of the migration's Voronoi; documented.
                self.assertIn("ward_note", spot)
            else:
                self.assertEqual(site_ward, spot["named_ward"], hid)
            if r["category"] == "person_stranded":
                self.assertLess(spot["river_distance_m"], 60)
            else:
                self.assertLess(spot["road_distance_m"], 120, hid)

    def test_fallback_needs_come_from_the_taxonomy(self):
        kinds = {pcmc.UNIT_KINDS[u["kind"]] for u in WORLD["units"] if u["kind"] in pcmc.UNIT_KINDS}
        self.assertIn("police", kinds)
        tax = pcmc.taxonomy()
        for r in pcmc.PAWANA_NIGHT:
            required = pcmc.fallback_required(r["category"], kinds)
            self.assertTrue(set(required) <= kinds, r["category"])
            needs = next(c["needs"] for c in tax["incident_categories"] if c["id"] == r["category"])
            self.assertEqual(len(required) <= len(needs), True)
        # Migration 032: fire and collapse need traffic_control, which only police provide.
        self.assertIn("police", pcmc.fallback_required("fire", kinds))
        self.assertNotIn("police", pcmc.fallback_required("flooded_road", kinds))

    def test_dock_sites_are_real_places(self):
        docks = WORLD["dock_sites"]
        self.assertGreaterEqual(len(docks), 6)
        stations = {re.sub(r"\s+", " ", u["name"]) for u in WORLD["units"]}
        self.assertTrue(any("Pradhikaran" in s for s in stations))
        self.assertTrue(all(ward_at(d["local_xy"]) for d in docks))


if __name__ == "__main__":
    unittest.main()
