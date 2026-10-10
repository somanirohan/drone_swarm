"""Scenario/world registry hook and flight path planning."""
import unittest

from swarm_ops import engine
from swarm_ops.engine import OperationsSimulation, _default_world, register_scenario, register_world, scenario_catalog


def _square_world(size=7000, step=500):
    """A generic (non-Pune) world: a road grid and a few buildings, no river."""
    half = size
    roads = [{"id": f"h{i}", "points": [[-half, v], [half, v]]} for i, v in enumerate(range(-half, half + 1, step))]
    roads += [{"id": f"v{i}", "points": [[v, -half], [v, half]]} for i, v in enumerate(range(-half, half + 1, step))]
    buildings = [{"id": f"T{i}", "footprint": [[x - 30, y - 30], [x + 30, y - 30], [x + 30, y + 30], [x - 30, y + 30]], "height": 40 + i * 5}
                 for i, (x, y) in enumerate([(250, 60), (-2250, 1060), (3250, -2940), (5250, 5060)])]
    return {"id": "test-square", "bounds": [-half, -half, half, half], "roads": roads, "buildings": buildings,
            "bases": [{"id": "base", "position": [-250, -250, 0]}], "shelters": [{"id": "s", "position": [-750, -750, 0]}], "no_fly": []}


class RegistryTests(unittest.TestCase):
    def tearDown(self):
        for key in ("test_square_ops", "test_docked"):
            engine.SCENARIO_REGISTRY.pop(key, None); engine.SCENARIOS.pop(key, None)
        engine.WORLD_REGISTRY.pop("test-square", None); engine._WORLD_CACHE.pop("test-square", None)

    def test_registered_scenario_loads_world_and_builds_with_public_helpers(self):
        register_world("test-square", _square_world)

        def build(sim):
            roof, building_id = sim.place_on_roof("T0")
            sim.add_incident(roof, "fire", people=4, building_id=building_id)
            road = sim.nearest_road_point([1300, -1040])
            self.assertIsNotNone(road)
            sim.add_resource("fire_engine", sim.nearest_road_point([-1000, -1000]), name="Tender 1")
            sim.add_resource("ambulance", sim.nearest_road_point([-1000, -500]))
            sim.add_incident(road, "medical", people=1)

        register_scenario("test_square_ops", "Square ops", "test", build, world_id="test-square")
        self.assertIn("test_square_ops", [s["id"] for s in scenario_catalog()])
        sim = OperationsSimulation(fleet_size=6)
        home_world = sim.world
        self.assertTrue(sim.command("scenario", {"id": "test_square_ops", "seed": 3})["ok"])
        self.assertEqual(sim.world_id, "test-square")
        self.assertEqual(sim.bounds, [-7000, -7000, 7000, 7000])
        self.assertEqual(sim.scenario["world_id"], "test-square")
        self.assertEqual(len(sim.incidents), 2)
        fire = sim.incidents[0]
        self.assertEqual(fire["building_id"], "T0")
        self.assertTrue(fire["resource_ids"], "the builder's ground unit is dispatched")
        self.assertIsNotNone(fire["drone_id"])
        for _ in range(30): sim.step(1)
        self.assertIn("world", sim.snapshot())
        # Built-in scenarios return to the constructor's world.
        self.assertTrue(sim.command("scenario", {"id": "rooftop_flood"})["ok"])
        self.assertIs(sim.world, home_world)
        self.assertEqual(sim.world_id, "default-grid")

    def test_builder_docks_start_docked_fleet(self):
        def build(sim):
            sim.add_dock([-300, -300]); sim.add_dock([300, -300], name="East dock")
            sim.add_incident(sim.nearest_road_point([0, 100]), "medical")
            sim.add_resource("rescue_team", sim.nearest_road_point([-400, -400]))
        register_scenario("test_docked", "Docked", "test", build, world_id=None)
        sim = OperationsSimulation(fleet_size=8, scenario="test_docked")
        self.assertEqual(len(sim.docks), 2)
        self.assertEqual(sim.resources[0]["kind"], "rescue")
        self.assertEqual(sim.resources[0]["model"], "rescue_van")
        self.assertTrue(all(d.get("dock_id") for d in sim.drones))
        self.assertLessEqual(sim._launched(), 3)

    def test_unknown_world_and_bad_registration_are_rejected(self):
        with self.assertRaises(ValueError): register_scenario("", "x", "y", None)
        with self.assertRaises(ValueError): register_scenario("x", "x", "y", 5)
        register_scenario("test_docked", "Bad", "world missing", lambda sim: None, world_id="nowhere")
        sim = OperationsSimulation(fleet_size=3)
        result = sim.command("scenario", {"id": "test_docked"})
        self.assertFalse(result["ok"]); self.assertIn("Unknown world", result["error"])


if __name__ == "__main__":
    unittest.main()
