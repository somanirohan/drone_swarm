"""Scenario/world registry hook and flight path planning."""
import math
import time
import unittest

from swarm_ops import engine
from swarm_ops.engine import OperationsSimulation, _default_world, register_scenario, register_world, scenario_catalog
from swarm_ops.planning import FlightPlanner, path_length


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


def _heading_reversals(points, near=None, radius=80):
    heads = []
    for a, b in zip(points, points[1:]):
        if math.dist(a[:2], b[:2]) > 0.3:
            heads.append((math.atan2(b[1] - a[1], b[0] - a[0]), b))
    count = 0
    for (h1, _), (h2, p) in zip(heads, heads[1:]):
        turn = abs((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
        if turn > math.radians(120) and (near is None or math.dist(p[:2], near[:2]) < radius):
            count += 1
    return count


def _winding(points, target, radius=80):
    total = 0.0; prev = None
    for p in points:
        d = math.dist(p[:2], target[:2])
        if 1 < d < radius:
            a = math.atan2(p[1] - target[1], p[0] - target[0])
            if prev is not None: total += (a - prev + math.pi) % (2 * math.pi) - math.pi
            prev = a
        else:
            prev = None
    return abs(math.degrees(total))


def _obstacle_world():
    world = _default_world()
    # A wide no-fly wall between west and east, a concave notch, a tall tower in the far north-east only.
    world["no_fly"] = [{"id": "wall", "polygon": [[-40, -300], [40, -300], [40, 200], [-40, 200]]},
                       {"center": [250, -350], "radius": 50}]
    world["buildings"].append({"id": "TOWER", "footprint": [[520, 520], [560, 520], [560, 560], [520, 560]], "height": 150})
    return world


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.world = _obstacle_world()
        self.planner = FlightPlanner(self.world)

    def full(self, start, waypoints):
        return [list(start)] + waypoints

    def test_unobstructed_is_direct_and_ends_at_standoff_facing_the_drone(self):
        start, target = [-400, -450, 0], [-150, -160, 30]
        waypoints, info = self.planner.route(start, target, cruise=65, standoff=20, observe=True)
        points = self.full(start, waypoints)
        self.assertLessEqual(path_length(points) / math.dist(start[:2], target[:2]), 1.05)
        self.assertEqual(waypoints[0][:2], start[:2], "climbs at the drone's own position: no snap-back")
        hover = waypoints[-1]
        self.assertAlmostEqual(math.dist(hover[:2], target[:2]), 20, delta=0.1)
        # The hover point lies between the drone and the target (side facing the approach).
        self.assertLess(math.dist(hover[:2], start[:2]), math.dist(target[:2], start[:2]))
        self.assertEqual(_heading_reversals(points), 0)
        self.assertLess(hover[2], waypoints[0][2], "descends to an observation altitude")
        self.assertGreaterEqual(hover[2], target[2] + 12)

    def test_obstructed_path_goes_round_no_fly_with_bounded_detour(self):
        for start, target in (([-300, 0, 70], [300, 0, 0]), ([-300, -100, 0], [250, -150, 0]), ([350, 150, 70], [-350, -250, 0])):
            waypoints, info = self.planner.route(start, target, cruise=65)
            self.assertIsNotNone(waypoints, (start, target))
            points = self.full(start, waypoints)
            for a, b in zip(points, points[1:]):
                self.assertFalse(self.planner.blocked(a[:2], b[:2]), "no leg crosses a no-fly polygon")
            self.assertLessEqual(path_length(points) / math.dist(start[:2], target[:2]), 1.35, (start, target))
            self.assertEqual(_heading_reversals(points), 0)
            self.assertLess(_winding(points, target), 90)
            self.assertEqual(waypoints[-1][:2], [target[0], target[1]])

    def test_altitude_comes_from_obstacles_on_this_path_not_the_tallest_building(self):
        low, _ = self.planner.route([-400, -450, 0], [-300, -300, 0], cruise=40)
        self.assertLess(max(w[2] for w in low), 100, "the 150 m tower far away does not raise this flight")
        high, info = self.planner.route([300, 540, 0], [590, 540, 0], cruise=40)
        self.assertGreaterEqual(max(w[2] for w in high), 150 + 12, "a path over the tower climbs above it")

    def test_target_or_start_in_no_fly_has_no_route(self):
        self.assertIsNone(self.planner.route([-400, -450, 0], [0, 0, 0], cruise=65)[0])
        self.assertIsNone(self.planner.route([0, 0, 0], [-400, -450, 0], cruise=65)[0])

    def test_region_scale_planning_is_fast_and_cached(self):
        world = {"bounds": [-7000, -7000, 7000, 7000], "buildings": [], "no_fly": [
            {"polygon": [[-1000 + 40 * math.cos(i / 40 * math.tau) * 25, 2000 + 40 * math.sin(i / 40 * math.tau) * 20] for i in range(40)]},
            {"polygon": [[3000, -6000], [6000, -6000], [6000, -3000], [4500, -4500], [3000, -3000]]}]}
        planner = FlightPlanner(world)
        start = time.perf_counter()
        pairs = [((-6500 + 1300 * i, -6500 + 900 * j), (6500 - 1100 * j, 6000 - 1200 * i)) for i in range(10) for j in range(10)]
        for a, b in pairs:
            planner.route([*a, 0], [*b, 0], cruise=80)
        first = time.perf_counter() - start
        start = time.perf_counter()
        for a, b in pairs:
            planner.route([*a, 0], [*b, 0], cruise=80)
        again = time.perf_counter() - start
        self.assertLess(first, 2.0, f"100 region plans took {first:.2f}s")
        self.assertLess(again, first)
        self.assertGreater(planner.stats["visibility"], 0)

    def test_simulated_rescue_flights_are_direct_without_loops(self):
        sim = OperationsSimulation(fleet_size=10)
        tracks = {}
        for _ in range(240):
            sim.step(0.25)
            for drone in sim.drones:
                if drone["state"] == "enroute" and drone["task_id"]:
                    tracks.setdefault((drone["id"], drone["task_id"]), []).append(list(drone["position"]))
        self.assertTrue(tracks)
        for (drone_id, incident_id), points in tracks.items():
            target = sim._find_incident(incident_id)["position"]
            straight = math.dist(points[0][:2], points[-1][:2])
            if straight > 30:
                self.assertLessEqual(path_length(points) / straight, 1.05, drone_id)
            self.assertEqual(_heading_reversals(points, near=target), 0, drone_id)
            self.assertLess(_winding(points, target), 90, drone_id)


if __name__ == "__main__":
    unittest.main()
