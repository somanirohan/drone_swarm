"""Decentralized coordination: simulated mesh radio + CBBA in every drone (simulation only).

Fixture tests drive ``CBBAAgent`` directly (conflict-free assignment, convergence, packet loss, lost peers,
partition and rejoin through ``Mesh`` geometry). Engine tests check the three coordination modes.
"""
import json
import math
import random
import unittest
from unittest import mock

from swarm_ops.cbba import LOCK, SUBTASK_KINDS, CBBAAgent, mission_subtasks, relay_chain, scene_subtasks
from swarm_ops.engine import OperationsSimulation
from swarm_ops.mesh import Mesh
from swarm_ops.server import COMMANDS, INTEGRATION_COMMANDS


# ---------------------------------------------------------------- fixtures
def tasks_fixture(n, seed=1, mission="M1"):
    rng = random.Random(seed)
    return [{"id": f"{mission}/t{k}", "mission_id": mission, "kind": "search", "position": [rng.uniform(-500, 500), rng.uniform(-500, 500)],
             "priority": 40.0 + 5 * (k % 3), "duration": 10.0} for k in range(n)]


def scorer(position):
    def score(tasks):
        p, t, total = position, 0.0, 0.0
        for task in tasks:
            t += math.hypot(task["position"][0] - p[0], task["position"][1] - p[1]) / 18.0
            total += task["priority"] * math.exp(-t / 300.0)
            t += task["duration"]
            p = task["position"]
        return total
    return score


def claims(agents):
    out = {}
    for a in agents:
        for j in a.bundle:
            out.setdefault(j, []).append(a.id)
    return out


def agreed(agents):
    tasks = set().union(*(a.open_tasks() for a in agents))
    for j in tasks:
        winners = {a.z.get(j) for a in agents}
        if len(winners) != 1:
            return False
        w = winners.pop()
        if w is not None and j not in next(a for a in agents if a.id == w).bundle:
            return False
    return all(len(v) == 1 for v in claims(agents).values())


def synchronous(agents, links, positions, rounds, loss=0.0, seed=7):
    """Each round: every agent builds its bundle, then broadcasts to its neighbours (each copy lost with
    probability ``loss``). Returns the first round after which all agents agree, else None."""
    rng = random.Random(seed)
    for r in range(rounds):
        t = float(r)
        for a in agents:
            a.build_bundle(scorer(positions[a.id]), t)
        outbox = [(a, a.message(t)) for a in agents]
        for a, msg in outbox:
            for b in agents:
                if b.id in links[a.id] and rng.random() >= loss:
                    b.receive(msg, t + 0.5)
        if agreed(agents) and all(len(a.bundle) == min(a.max_bundle, len(a.open_tasks())) or not any(
                j not in claims(agents) for j in a.open_tasks()) for a in agents):
            return r
    return None


def make_agents(n, tasks, max_bundle=2, timeout=10.0):
    agents = [CBBAAgent(f"D{k + 1:02}", max_bundle=max_bundle, peer_timeout=timeout) for k in range(n)]
    for a in agents:
        a.add_tasks(tasks)
    return agents


class CBBAFixtureTests(unittest.TestCase):
    def setUp(self):
        rng = random.Random(3)
        self.positions = {f"D{k + 1:02}": [rng.uniform(-600, 600), rng.uniform(-600, 600)] for k in range(6)}

    def test_conflict_free_and_converges_fully_connected(self):
        agents = make_agents(6, tasks_fixture(9))
        links = {a.id: {b.id for b in agents if b is not a} for a in agents}
        rounds = synchronous(agents, links, self.positions, 30)
        self.assertIsNotNone(rounds, "CBBA did not converge in 30 rounds")
        self.assertLessEqual(rounds, 12)
        c = claims(agents)
        self.assertTrue(all(len(v) == 1 for v in c.values()), c)
        self.assertEqual(len(c), 9, "every task assigned (6 agents x bundle 2 >= 9 tasks)")
        for a in agents:
            self.assertLessEqual(len(a.bundle), 2)

    def test_converges_on_a_line_topology(self):
        agents = make_agents(6, tasks_fixture(8, seed=4))
        ids = [a.id for a in agents]
        links = {x: {ids[i - 1]} if i else set() for i, x in enumerate(ids)}
        for i, x in enumerate(ids[:-1]):
            links[x].add(ids[i + 1])
        rounds = synchronous(agents, links, self.positions, 60)
        self.assertIsNotNone(rounds, "no convergence on a 6-hop line")
        self.assertTrue(all(len(v) == 1 for v in claims(agents).values()))

    def test_twenty_percent_packet_loss(self):
        agents = make_agents(6, tasks_fixture(9, seed=5))
        links = {a.id: {b.id for b in agents if b is not a} for a in agents}
        rounds = synchronous(agents, links, self.positions, 80, loss=0.2)
        self.assertIsNotNone(rounds, "no convergence with 20% loss")
        self.assertTrue(all(len(v) == 1 for v in claims(agents).values()))

    def test_rule_table_examples(self):
        a = CBBAAgent("D02")
        a.add_tasks(tasks_fixture(1))
        j = "M1/t0"
        # sender thinks it wins with a higher bid than ours -> update, we lose the task
        a.build_bundle(lambda ts: 10.0 * len(ts), 0)
        self.assertEqual(a.z[j], "D02")
        a.receive({"from": "D03", "y": {j: 20.0}, "z": {j: "D03"}, "s": {"D03": 1.0}, "tasks": {}, "done": {}}, 1.0)
        self.assertEqual((a.z[j], a.bundle), ("D03", []))
        # sender says WE win but we believe the sender -> reset
        a.receive({"from": "D03", "y": {j: 0.0}, "z": {j: "D02"}, "s": {"D03": 2.0}, "tasks": {}, "done": {}}, 2.0)
        self.assertIsNone(a.z[j])
        # stale third-party info does not override newer info
        a.receive({"from": "D04", "y": {j: 30.0}, "z": {j: "D05"}, "s": {"D04": 3.0, "D05": 2.5}, "tasks": {}, "done": {}}, 3.0)
        self.assertEqual(a.z[j], "D05")
        a.receive({"from": "D03", "y": {j: 25.0}, "z": {j: "D06"}, "s": {"D03": 4.0, "D06": 1.0, "D05": 1.0}, "tasks": {}, "done": {}}, 4.0)
        self.assertEqual(a.z[j], "D05", "older info about D06/D05 must not replace D05")

    def test_lost_peer_tasks_are_retaken(self):
        agents = make_agents(4, tasks_fixture(4, seed=6), max_bundle=1, timeout=5.0)
        links = {a.id: {b.id for b in agents if b is not a} for a in agents}
        self.assertIsNotNone(synchronous(agents, links, self.positions, 30))
        victim = agents[0]
        lost_tasks = list(victim.bundle)
        self.assertTrue(lost_tasks)
        survivors = agents[1:]
        for a in survivors:          # survivors' bundles are full (max 1) -> give them room by completing their tasks
            for j in list(a.bundle):
                for b in survivors:
                    b.complete(j, 10.0)
        t = 10.0
        for step in range(30):
            t += 1.0
            for a in survivors:
                a.check_peers(t)
                a.build_bundle(scorer(self.positions[a.id]), t)
            for a in survivors:
                msg = a.message(t)
                for b in survivors:
                    if b is not a:
                        b.receive(msg, t + 0.5)
        winners = {a.z.get(lost_tasks[0]) for a in survivors}
        self.assertEqual(len(winners), 1)
        self.assertIn(winners.pop(), {a.id for a in survivors}, "the crashed drone's sub-task was re-won by a survivor")
        self.assertTrue(all(victim.id in a.lost for a in survivors))


class MeshPartitionTests(unittest.TestCase):
    """Partitions emerge from geometry (mesh radio); both sides keep working and reconcile on rejoin."""

    def run_mesh(self, mesh, agents, positions, t0, seconds, dt=0.5):
        t = t0
        for _ in range(int(seconds / dt)):
            t += dt
            mesh.update_topology({a.id: positions[a.id] for a in agents}, [[0, 0]], t)
            mesh.deliver(t)
            for a in agents:
                for message in mesh.drain(a.id):
                    a.receive(message["payload"], t)
                a.check_peers(t)
                a.build_bundle(scorer(positions[a.id]), t)
                mesh.broadcast(a.id, a.message(t), t)
        return t

    def test_partition_both_sides_work_then_reconcile(self):
        mesh = Mesh(seed=3, radio_range_m=600, latency_s=0.1, packet_loss=0.1)
        tasks = tasks_fixture(4, seed=9)
        agents = make_agents(6, tasks, max_bundle=1, timeout=6.0)
        positions = {a.id: [100.0 * k, 0.0] for k, a in enumerate(agents)}
        t = self.run_mesh(mesh, agents, positions, 0.0, 15)
        self.assertEqual(len(mesh.components), 1)
        self.assertTrue(all(len(v) == 1 for v in claims(agents).values()))
        # side B flies 3 km away: two partitions, no scripted split
        side_a, side_b = agents[:3], agents[3:]
        for a in side_b:
            positions[a.id] = [positions[a.id][0] + 3000.0, 0.0]
        t = self.run_mesh(mesh, agents, positions, t, 25)
        self.assertEqual(len(mesh.components), 2)
        for side in (side_a, side_b):
            mine = claims(side)
            self.assertEqual(len(mine), 3, f"each side keeps working all it can ({mine})")
            self.assertTrue(all(len(v) == 1 for v in mine.values()))
        # rejoin: duplicates across the former partition are resolved, one winner per task
        for a in side_b:
            positions[a.id] = [positions[a.id][0] - 3000.0, 0.0]
        self.run_mesh(mesh, agents, positions, t, 20)
        self.assertEqual(len(mesh.components), 1)
        c = claims(agents)
        self.assertTrue(all(len(v) == 1 for v in c.values()), f"duplicate winners after rejoin: {c}")
        self.assertEqual(len(c), 4)
        self.assertTrue(agreed(agents))

    def test_flooding_multi_hop_and_gateways(self):
        mesh = Mesh(seed=1, radio_range_m=500, gateway_range_m=100)
        positions = {"D01": [0, 0], "D02": [400, 0], "D03": [800, 0], "D04": [1200, 0]}
        mesh.update_topology(positions, [[0, 0]], 0.0)
        self.assertEqual(mesh.gateways, {"D01"})
        self.assertEqual(mesh.hops_to_gateway["D04"], 3)
        self.assertNotIn("D03", mesh.neighbours["D01"])
        mesh.to_command("D04", {"report": {"mission_id": "M"}}, 0.0)
        t = 0.0
        for _ in range(20):
            t += 0.25
            mesh.deliver(t)
        reports = mesh.drain_command()
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["hops"], 3)
        self.assertIn("links", mesh.snapshot())


class SubtaskDerivationTests(unittest.TestCase):
    def test_local_subtasks_and_relays(self):
        mission = {"id": "I005", "position": [1500.0, 0.0, 0.0], "severity": 4}
        chain = relay_chain(mission, [[0, 0]], 600, 600, "D01", 0)
        self.assertEqual(len(chain), 2)
        derived = mission_subtasks(mission, [[0, 0]], 600, 600, "D01", 0)
        self.assertEqual(derived[0]["id"], "I005/assess")
        scene = scene_subtasks(mission, {"people": 6, "people_uncertain": True, "hazards": ["water"], "needs_unmet": ["food"],
                                         "ground_unit_coming": True, "severity": 4, "category": "flood"}, "D01", 1)
        kinds = {t["kind"] for t in scene}
        self.assertEqual(kinds, {"overwatch", "search", "supply", "handover"})
        for task in derived + scene:
            self.assertIn(task["kind"], SUBTASK_KINDS)
            self.assertTrue(set(task) <= {"id", "mission_id", "kind", "position", "priority", "duration", "created_by", "created_t", "payload"},
                            "a sub-task never carries unit kinds or quantities")


# ---------------------------------------------------------------- engine
def run(sim, seconds, every=None):
    for k in range(int(seconds)):
        sim.step(1)
        if every:
            every(sim)


class EngineModeTests(unittest.TestCase):
    def test_default_is_central_and_layer_idle(self):
        sim = OperationsSimulation(fleet_size=6)
        run(sim, 20)
        self.assertEqual(sim.coordination["mode"], "central")
        self.assertFalse(sim.decentral.active)
        self.assertEqual(sim.decentral.mesh.stats["messages_sent"], 0)
        self.assertTrue(any(d["decision"] == "award" for d in sim.decisions.recent(100)))
        snap = sim.snapshot(include_world=False)
        self.assertEqual(snap["subtasks"], [])
        self.assertEqual(snap["drones"][0]["coordination"]["mode"], "central")

    def test_decentralized_never_calls_the_central_auction(self):
        sim = OperationsSimulation(fleet_size=10)
        with mock.patch.object(sim.coordinator, "award", side_effect=AssertionError("central award in decentralized mode")) as award:
            ack = sim.command("set_coordination", {"mode": "decentralized"})
            self.assertTrue(ack["ok"], ack)
            sim2 = OperationsSimulation(fleet_size=10, coordination="decentralized")
            with mock.patch.object(sim2.coordinator, "award", side_effect=AssertionError("central award")) as award2:
                run(sim2, 120)
                award2.assert_not_called()
            award.assert_not_called()
        done = [r for r in sim2.snapshot(include_world=False)["subtasks"] if r["status"] == "done"]
        self.assertTrue(any(r["kind"] == "assess" for r in done) or sim2.decentral.done_log, "the swarm assessed its missions on its own")
        self.assertTrue(all(i.get("drone_on_scene_at") is not None for i in sim2.incidents), "every mission scene was reached")
        kinds = {x[2].split("/")[1].split("-")[0] for x in sim2.decentral.done_log}
        self.assertTrue({"assess", "search"} <= kinds or {"assess", "overwatch"} <= kinds, kinds)

    def test_snapshot_fields_and_message_volume(self):
        sim = OperationsSimulation(fleet_size=8, coordination="decentralized")
        run(sim, 40)
        snap = sim.snapshot(include_world=False)
        json.dumps(snap, allow_nan=False)
        for d in snap["drones"]:
            self.assertTrue({"mode", "bundle", "winning_bids", "neighbours", "last_consensus_t", "gateway"} <= set(d["coordination"]))
        self.assertTrue(snap["subtasks"])
        for row in snap["subtasks"]:
            self.assertTrue({"id", "mission_id", "kind", "position", "winner", "bid", "status"} <= set(row))
        self.assertTrue({"links", "partitions", "messages_sent", "dropped"} <= set(snap["mesh"]))
        self.assertGreater(snap["mesh"]["by_kind"].get("consensus", 0), 50)
        self.assertFalse([m for m in snap["messages"] if m["kind"] in ("bid", "consensus")], "consensus traffic is aggregated, not on the bus log")
        self.assertTrue(any(d["decision"].startswith("cbba_") for d in snap["decisions"]))
        self.assertTrue(any(m["kind"] == "report" and (m["body"] or {}).get("via") == "mesh" for m in snap["messages"]))

    def test_crash_mid_mission_subtask_rewon(self):
        sim = OperationsSimulation(fleet_size=10, coordination="decentralized")
        victim = None
        for _ in range(60):
            sim.step(1)
            victim = next((d for d in sim.drones if d.get("dc_working") and d["state"] == "enroute" and d.get("subtask")), None)
            if victim:
                break
        self.assertIsNotNone(victim)
        subtask = victim["subtask"]
        crash_t = sim.t
        self.assertTrue(sim.command("fault", {"drone_id": victim["id"], "kind": "crash"})["ok"])
        taken = None
        for _ in range(60):
            sim.step(1)
            holders = [d["id"] for d in sim.drones if d["active"] and subtask in sim.agents[d["id"]].cbba.bundle]
            if holders or any(subtask in sim.agents[d["id"]].cbba.done for d in sim.drones if d["active"]):
                taken = (holders, sim.t - crash_t)
                break
        self.assertIsNotNone(taken, f"{subtask} of crashed {victim['id']} never re-won")
        self.assertNotIn(victim["id"], taken[0])
        self.assertLessEqual(taken[1], 30)
        self.assertTrue(any(d["decision"] in ("peer_lost", "cbba_takeover") for d in sim.decisions.recent(100)))

    def test_twenty_percent_packet_loss_engine(self):
        sim = OperationsSimulation(fleet_size=10)
        self.assertTrue(sim.command("set_coordination", {"mode": "decentralized", "packet_loss": 0.2})["ok"])
        executing_twice = []

        def check(s):
            seen = {}
            for d in s.drones:
                if d["active"] and d.get("dc_working") and d["state"] == "observing" and d.get("subtask"):
                    seen.setdefault(d["subtask"], []).append(d["id"])
            executing_twice.extend(v for v in seen.values() if len(v) > 1)
        run(sim, 150, check)
        self.assertGreater(sim.decentral.mesh.stats["dropped"], 0)
        self.assertTrue(all(i.get("drone_on_scene_at") is not None for i in sim.incidents))
        self.assertEqual(executing_twice, [], "two drones executing the same sub-task")

    def test_no_external_work_no_units_and_scoped_to_mission(self):
        sim = OperationsSimulation(fleet_size=10)
        target = sim.incidents[0]["id"]
        other = sim.incidents[1]["id"]
        sim._initialize(sim.scenario["id"])        # fresh start before any central award
        ack = sim.command("set_coordination", {"mode": "decentralized", "incident_id": target})
        self.assertTrue(ack["ok"], ack)
        run(sim, 120)
        for agent in sim.agents.values():
            if agent.cbba is None:
                continue
            self.assertTrue(set(agent.cbba.missions) <= {target}, "drones only work the mission they were given")
            self.assertTrue({t["mission_id"] for t in agent.cbba.tasks.values()} <= {target})
        forbidden = {"units", "unit_kinds", "required", "required_kinds", "resource_kinds", "count", "quantity", "kind_counts"}
        for m in sim.bus.recent(200):
            if m["from"].startswith("D"):
                self.assertNotIn(m["kind"], ("dispatch", "task"), m)
                self.assertFalse(forbidden & set(m["body"] or {}), m)
        self.assertEqual(sim._coordination_mode(sim._find_incident(other)), "central")

    def test_orchestrated_mode_unchanged_and_global_dependent(self):
        sim = OperationsSimulation(fleet_size=8)
        self.assertTrue(sim.command("set_coordination", {"mode": "orchestrated"})["ok"])
        sim._initialize(sim.scenario["id"])
        self.assertEqual(sim.coordination["mode"], "orchestrated")
        with mock.patch.object(sim.coordinator, "award", side_effect=AssertionError("no self-allocation when dependent")):
            run(sim, 5)
        incident = sim.incidents[0]
        self.assertIn("waiting for DisruptionOps", incident["wait_reason"] or "")
        ack = sim.command("assign", {"incident_id": incident["id"], "drones": [{"count": 1, "role": "assess"}]})
        self.assertTrue(ack["ok"] and ack["assigned_drones"], ack)
        self.assertEqual(incident["control_mode"], "orchestrated")
        self.assertFalse(sim.decentral.active)

    def test_set_coordination_validation_and_cli_commands(self):
        sim = OperationsSimulation(fleet_size=4)
        self.assertFalse(sim.command("set_coordination", {"mode": "anarchy"})["ok"])
        self.assertFalse(sim.command("set_coordination", {"mode": "decentralized", "packet_loss": 2})["ok"])
        ack = sim.command("set_coordination", {"mode": "decentralized", "radio_range_m": 1500, "max_bundle": 1})
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["coordination"]["radio_range_m"], 1500.0)
        self.assertEqual(sim.decentral.mesh.radio_range_m, 1500.0)
        sim.command("reset", {})
        self.assertEqual(sim.coordination["mode"], "decentralized", "mode survives a scenario reset")
        self.assertIn("set_coordination", COMMANDS)
        self.assertIn("set_coordination", INTEGRATION_COMMANDS)
        with self.assertRaises(ValueError):
            OperationsSimulation(fleet_size=2, coordination="nope")


if __name__ == "__main__":
    unittest.main()
