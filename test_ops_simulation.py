"""Meaningful dispatch, ownership and failure checks for the operations demo."""
import copy
import json
import unittest

from ops_simulation import OperationsSimulation, _default_world


class OperationsTests(unittest.TestCase):
    def advance(self,sim,seconds):
        for _ in range(seconds):sim.step(1)

    def test_twenty_drone_schema_and_parallel_dispatch(self):
        sim=OperationsSimulation(fleet_size=20)
        snapshot=sim.snapshot()
        self.assertEqual(snapshot["schema_version"],2)
        self.assertEqual(len(snapshot["drones"]),20)
        json.dumps(snapshot,allow_nan=False)
        for incident in snapshot["incidents"]:
            self.assertIsNotNone(incident["drone_id"])
            self.assertTrue(incident["resource_ids"])
        self.assertTrue(all(d["battery"]<=1 for d in sim.drones))

    def test_delivery_does_not_resolve_and_ack_requires_arrival(self):
        sim=OperationsSimulation()
        incident=sim.incidents[1]
        resource=next(r for r in sim.resources if r["id"]==incident["resource_ids"][0])
        early=sim.command("ack_handoff",{"incident_id":incident["id"],"resource_id":resource["id"]})
        self.assertFalse(early["ok"])
        self.advance(sim,50)
        self.assertTrue(incident["supplied"])
        self.assertNotEqual(incident["status"],"resolved")
        self.assertFalse(incident["handed_over"])
        self.assertIsNotNone(incident["drone_id"])
        self.advance(sim,100)
        self.assertTrue(incident["handed_over"])
        self.assertIsNone(incident["drone_id"])
        self.assertEqual(incident["status"],"evacuating")
        self.assertEqual(incident["evacuated_people"],0)
        self.advance(sim,100)
        self.assertEqual(incident["status"],"resolved")
        self.assertEqual(incident["evacuated_people"],1)

    def test_ground_first_produces_own_briefing_then_recalls(self):
        sim=OperationsSimulation()
        incident=sim.incidents[0]
        resource=next(r for r in sim.resources if r["id"]==incident["resource_ids"][0])
        resource["position"]=[*incident["position"][:2],0]
        resource["route"]=[]
        drone=next(d for d in sim.drones if d["id"]==incident["drone_id"])
        self.advance(sim,5)
        self.assertTrue(incident["handed_over"])
        self.assertEqual(incident["briefing"]["source"],"ground crew")
        self.assertEqual(drone["state"],"returning")
        self.assertNotEqual(incident["status"],"resolved")

    def test_crashed_owner_is_replaced_without_losing_incident(self):
        sim=OperationsSimulation()
        incident=sim.incidents[0];old=incident["drone_id"]
        result=sim.command("fault",{"drone_id":old,"kind":"crash"})
        self.assertTrue(result["ok"])
        self.assertIsNotNone(incident["drone_id"])
        self.assertNotEqual(incident["drone_id"],old)
        failed=next(d for d in sim.drones if d["id"]==old)
        self.assertFalse(failed["active"])
        self.assertEqual(failed["state"],"crashed")

    def test_link_loss_does_not_blindly_duplicate_exclusive_task(self):
        sim=OperationsSimulation()
        incident=sim.incidents[0];old=incident["drone_id"]
        sim.command("fault",{"drone_id":old,"kind":"link"})
        self.advance(sim,20)
        self.assertTrue(incident["uncertain_delivery"])
        self.assertEqual(incident["drone_id"],old)
        self.assertEqual(sum(d["task_id"]==incident["id"] for d in sim.drones),1)
        self.assertEqual(next(d for d in sim.drones if d["id"]==old)["state"],"lost_link")

    def test_low_energy_returns_and_reassigns(self):
        sim=OperationsSimulation()
        incident=sim.incidents[0];old=incident["drone_id"]
        sim.command("fault",{"drone_id":old,"kind":"battery"})
        self.assertNotEqual(incident["drone_id"],old)
        self.assertEqual(next(d for d in sim.drones if d["id"]==old)["state"],"returning")

    def test_free_ground_unit_dispatches_while_drone_works(self):
        sim=OperationsSimulation()
        incident=sim.incidents[0]
        for resource in sim.resources:
            if resource["incident_id"]==incident["id"]:
                resource.update(state="busy",incident_id=None,route=[],free_at=None)
        incident["resource_ids"]=[]
        sim._allocate()
        self.assertFalse(incident["resource_ids"])
        available=next(r for r in sim.resources if r["kind"]=="rescue" and r["state"]=="busy")
        self.assertTrue(sim.command("release_resource",{"resource_id":available["id"]})["ok"])
        self.assertEqual(available["incident_id"],incident["id"])
        self.assertEqual(available["state"],"enroute")
        self.assertIsNotNone(incident["drone_id"])

    def test_no_roads_means_no_ground_teleportation(self):
        world=_default_world();world["roads"]=[]
        sim=OperationsSimulation(world)
        self.advance(sim,10)
        self.assertFalse(sim.incidents[1]["resource_ids"])
        self.assertTrue(all(r["state"] in ("available","busy") for r in sim.resources if r["transport"]=="road"))
        self.assertTrue(sim.incidents[0]["resource_ids"],"A boat does not require a road graph")

    def test_no_fly_excludes_target_and_does_not_block_ground(self):
        world=_default_world()
        position=OperationsSimulation(world).incidents[1]["position"]
        world["no_fly"]=[{"center":position[:2],"radius":60}]
        sim=OperationsSimulation(world)
        incident=sim.incidents[1]
        self.assertIsNone(incident["drone_id"])
        self.assertTrue(incident["resource_ids"])

    def test_repeated_detection_merges_and_escalates(self):
        sim=OperationsSimulation()
        incident=sim.incidents[1];count=len(sim.incidents)
        sim.command("detect",{"position":incident["position"],"category":incident["category"],"severity":5,"people":3})
        self.assertEqual(len(sim.incidents),count)
        self.assertEqual(incident["severity"],5)
        self.assertEqual(incident["people"],3)

    def test_pause_speed_and_reset_reject_nonfinite(self):
        sim=OperationsSimulation();old=sim.run_id
        sim.command("pause");sim.step(10);self.assertEqual(sim.t,0)
        self.assertFalse(sim.command("speed",{"value":float("nan")})["ok"])
        sim.command("speed",{"value":4});sim.command("resume");sim.step(1)
        self.assertEqual(sim.t,4)
        sim.command("reset");self.assertEqual(sim.t,0);self.assertNotEqual(old,sim.run_id)

    def test_random_button_changes_scenario(self):
        sim=OperationsSimulation();old=sim.scenario["id"]
        self.assertTrue(sim.command("random_scenario")["ok"])
        self.assertNotEqual(old,sim.scenario["id"])

    def test_invalid_scenario_cannot_change_seed_or_run(self):
        sim=OperationsSimulation();before=sim.snapshot()
        result=sim.command("scenario",{"id":"not-a-scenario","seed":91})
        self.assertFalse(result["ok"])
        self.assertEqual(sim.seed,42)
        self.assertEqual(sim.snapshot(),before)

    def test_incremental_snapshot_omits_geometry(self):
        sim=OperationsSimulation()
        self.assertIn("world",sim.snapshot())
        incremental=sim.snapshot(include_world=False)
        self.assertNotIn("world",incremental)
        self.assertIn("flood",incremental)
        self.assertIn("road_conditions",incremental)

    def test_outside_boundary_detection_does_not_send_aircraft_outside_mission(self):
        sim=OperationsSimulation()
        sim.command("detect",{"position":[1000,1000,0],"category":"medical"})
        self.assertIsNone(sim.incidents[-1]["drone_id"])

    def test_repeated_fault_cannot_reanimate_crashed_aircraft(self):
        sim=OperationsSimulation();identifier=sim.incidents[0]["drone_id"]
        sim.command("fault",{"drone_id":identifier,"kind":"crash"})
        self.assertFalse(sim.command("fault",{"drone_id":identifier,"kind":"battery"})["ok"])
        drone=next(d for d in sim.drones if d["id"]==identifier)
        self.assertEqual(drone["state"],"crashed")

    def test_restricted_nearest_patrol_patch_does_not_starve_other_claims(self):
        world=_default_world()
        # The nearest coverage cell to base is excluded, but farther cells
        # must still be considered independently by eligible aircraft.
        world["no_fly"]=[{"center":[-400,-400],"radius":20}]
        sim=OperationsSimulation(world,fleet_size=20)
        patrols=[d for d in sim.drones if d["state"]=="patrolling"]
        self.assertGreater(len(patrols),10)
        self.assertNotIn((-400,-400),[d["coverage_task"] for d in patrols])

    def test_external_authority_requires_explicit_fresh_ack(self):
        sim=OperationsSimulation();incident=sim.incidents[0]
        sim._brief(incident,next(d for d in sim.drones if d["id"]==incident["drone_id"]))
        payload={"source":"disruptionops","run_id":sim.run_id,"resources":[{"id":"OPS-R1","kind":"rescue","state":"on_site","position":[*incident["position"][:2],0],"incident_id":incident["id"]}],"handoffs":[]}
        self.assertTrue(sim.command("external_resource_update",payload)["ok"])
        self.advance(sim,5)
        self.assertFalse(incident["handed_over"])
        self.assertEqual(len(sim.resources),1)
        stale=copy.deepcopy(payload);stale["handoffs"]=[{"incident_id":incident["id"],"resource_id":"OPS-R1","accepted":True,"briefing_revision":0}]
        sim.command("external_resource_update",stale)
        self.assertFalse(incident["handed_over"])
        payload["handoffs"]=[{"incident_id":incident["id"],"resource_id":"OPS-R1","accepted":True,"briefing_revision":incident["briefing"]["revision"]}]
        sim.command("external_resource_update",payload)
        self.assertTrue(incident["handed_over"])
        self.assertIsNone(incident["drone_id"])
        self.assertEqual(sim.resources[0]["position"],[*incident["position"][:2],0])

    def test_wrong_run_external_update_is_rejected(self):
        sim=OperationsSimulation()
        self.assertFalse(sim.command("external_resource_update",{"source":"disruptionops","run_id":"wrong"})["ok"])
        self.assertFalse(sim.external_ground)

    def test_seeded_motion_and_decisions_repeat(self):
        a=OperationsSimulation(seed=17);b=OperationsSimulation(seed=17)
        self.advance(a,10);self.advance(b,10)
        self.assertEqual(a.drones,b.drones)
        self.assertEqual(a.incidents,b.incidents)

    def test_patrol_claims_unique_patches_and_accumulates_coverage(self):
        sim=OperationsSimulation()
        claims=[d["coverage_task"] for d in sim.drones if d["coverage_task"] is not None]
        self.assertEqual(len(claims),len(set(claims)))
        self.advance(sim,100)
        self.assertGreater(sim.snapshot()["metrics"]["covered_patches"],10)

    def test_confirmed_crash_after_link_loss_allows_replacement(self):
        sim=OperationsSimulation();incident=sim.incidents[0];old=incident["drone_id"]
        sim.command("fault",{"drone_id":old,"kind":"link"})
        sim.command("fault",{"drone_id":old,"kind":"crash"})
        self.assertFalse(incident["uncertain_delivery"])
        self.assertNotEqual(incident["drone_id"],old)

    def test_flood_uses_water_rescue_and_closes_unsafe_roads(self):
        sim=OperationsSimulation();incident=sim.incidents[0]
        resource=next(r for r in sim.resources if r["id"] in incident["resource_ids"])
        self.assertEqual(resource["transport"],"boat")
        self.assertTrue(sim._submerged(incident["position"]))
        self.assertTrue(sim.snapshot()["road_conditions"])
        self.assertIsNone(sim._ground_path(sim._base,incident["position"]))
        self.assertTrue(sim.incidents[1]["resource_ids"],"Dry ambulance routes remain usable")
        self.assertTrue(all(sim._submerged(p) for p in resource["route"]))

    def test_seed_varies_roof_people_and_dry_incident_positions(self):
        a=OperationsSimulation(seed=42);b=OperationsSimulation(seed=43)
        self.assertNotEqual([(i["position"],i["people"]) for i in a.incidents],[(i["position"],i["people"]) for i in b.incidents])

    def test_drone_payload_is_matching_and_consumed_then_reloaded(self):
        sim=OperationsSimulation();incident=sim.incidents[0]
        drone=next(d for d in sim.drones if d["id"]==incident["drone_id"])
        # Keep the boat committed elsewhere so arrival acknowledgment cannot
        # legitimately recall the drone before the supply drop.
        boat=next(r for r in sim.resources if r["id"] in incident["resource_ids"])
        boat.update(state="busy",incident_id=None,free_at=None,route=[])
        incident["resource_ids"]=[]
        self.assertEqual(drone["payload"],"food")
        self.advance(sim,60)
        self.assertEqual(drone["payload_stock"],0)
        self.assertIn("food",incident["supplied"])
        self.advance(sim,100)
        self.assertEqual(drone["payload_stock"],1)
        self.assertTrue(any(e["kind"]=="reload" for e in sim.events))

    def test_boat_capacity_requires_actual_safe_shore_trips(self):
        sim=OperationsSimulation();incident=sim.incidents[0];incident["people"]=8
        rescue=next(r for r in sim.resources if r["id"] in incident["resource_ids"])
        rescue["capacity"]=3
        self.advance(sim,100)
        self.assertTrue(incident["handed_over"])
        self.assertNotEqual(incident["status"],"resolved")
        self.assertLess(incident["evacuated_people"],incident["people"])
        self.advance(sim,350)
        self.assertEqual(incident["status"],"resolved")
        self.assertEqual(incident["evacuated_people"],8)
        self.assertGreaterEqual(sum(e["kind"]=="evacuation" for e in sim.events),3)

    def test_multi_agency_incident_waits_for_every_required_service(self):
        sim=OperationsSimulation();incident=sim.incidents[1]
        incident["required_kinds"]=["ambulance","engineering"]
        ambulance=next(r for r in sim.resources if r["kind"]=="ambulance" and r["incident_id"]==incident["id"])
        incident["handed_over"]=True
        sim._complete_service(ambulance,incident)
        self.assertNotEqual(incident["status"],"resolved")
        engineer=next(r for r in sim.resources if r["kind"]=="engineering")
        sim._complete_service(engineer,incident)
        self.assertEqual(incident["status"],"resolved")


if __name__=="__main__":unittest.main()
