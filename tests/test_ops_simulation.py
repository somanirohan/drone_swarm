"""Meaningful dispatch, ownership and failure checks for the operations demo."""
import copy
import json
import math
import time
import unittest
from pathlib import Path

from swarm_ops.engine import ACTIVITIES, BEHAVIORS, PERSON_STATUSES, OperationsSimulation, _default_world, _inside


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
        drone["payload_stock"]=0  # nothing left to drop -> recall (not a supply retask)
        self.advance(sim,5)
        self.assertTrue(incident["handed_over"])
        self.assertEqual(incident["briefing"]["source"],"ground crew")
        self.assertEqual(drone["state"],"returning")
        self.assertNotEqual(incident["status"],"resolved")
        self.assertTrue(any(e["kind"]=="drone_recall" for e in sim.events))
        kinds=[s["kind"] for s in sim.snapshot()["signals"]]
        self.assertIn("recall",kinds)
        self.assertIn("briefing",kinds)

    def test_ground_first_retasks_drone_that_still_carries_a_needed_supply(self):
        sim=OperationsSimulation()
        incident=sim.incidents[0]
        resource=next(r for r in sim.resources if r["id"]==incident["resource_ids"][0])
        resource["position"]=[*incident["position"][:2],0];resource["route"]=[]
        drone=next(d for d in sim.drones if d["id"]==incident["drone_id"])
        self.assertIn(drone["payload"],incident["needs"])
        self.advance(sim,5)
        self.assertEqual(incident["briefing"]["source"],"ground crew")
        self.assertEqual(drone["state"],"enroute")
        self.assertEqual(drone["retasked"],"supply")
        self.assertIn(sim._activity(drone),("launching","delivering"))
        self.assertEqual(sim._behavior(drone),"relay")
        self.advance(sim,60)
        self.assertIn(drone["payload"],incident["supplied"])
        self.assertEqual(incident["briefing"]["source"],"ground crew","A supply drop must not overwrite the crew briefing")
        self.assertIn(drone["state"],("returning","charging","ready"))

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
        sim.bus.post("command_center","rescue_service","dispatch","Dispatch rescue",incident["id"],"T-FREE",{"dispatch_id":"T-FREE","kind":"rescue","qty":1})
        sim.nodes.pump()
        self.assertFalse(incident["resource_ids"],"no unit is free, the agency node keeps the order open")
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
        sim=OperationsSimulation();count=len(sim.incidents)
        result=sim.command("detect",{"position":[1000,1000,0],"category":"medical"})
        self.assertFalse(result["ok"])
        self.assertIn("operating area",result["error"])
        self.assertEqual(len(sim.incidents),count,"a declined request creates no incident and sends no aircraft")

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
        drone=next(d for d in sim.drones if d["id"]==incident["drone_id"])
        drone["payload_stock"]=0  # no supply to finish, so an accepted handoff recalls it
        sim._brief(incident,drone)
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


PUNE=Path(__file__).resolve().parents[1]/"assets"/"operations"/"pune-world.json"
CATALOG=Path(__file__).resolve().parents[1]/"assets"/"operations"/"agency-catalog.json"


@unittest.skipUnless(PUNE.exists(),"prepared Pune world not present")
class DockFireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world=json.loads(PUNE.read_text(encoding="utf8"))

    def run_to_end(self,sim,limit=900):
        peak=0
        for _ in range(limit):
            sim.step(1);peak=max(peak,sim._launched())
            if all(i["status"]=="resolved" for i in sim.incidents) and all(d["state"] in ("ready","charging") for d in sim.drones):break
        return peak

    def first_on_scene(self,sim,incident_id):
        for e in sim.events:
            if e.get("incident_id")==incident_id and e["kind"] in ("briefing","ground_arrival"):
                return "drone" if e["kind"]=="briefing" else "ground"

    def test_init_is_fast_cap_holds_and_everything_resolves_and_docks(self):
        for fleet in (5,50):
            start=time.perf_counter()
            sim=OperationsSimulation(self.world,seed=91,fleet_size=fleet,scenario="dock_fire")
            self.assertLess(time.perf_counter()-start,3.0,"dock_fire init must stay well under a few seconds")
            self.assertTrue(all(d["state"]=="ready" and d["position"]==d["home"] for d in sim.drones if d["task_id"] is None))
            peak=self.run_to_end(sim)
            self.assertLessEqual(peak,3)
            self.assertTrue(all(i["status"]=="resolved" for i in sim.incidents))
            for drone in sim.drones:
                self.assertEqual(drone["position"],drone["home"],f"{drone['id']} must land on its own dock pad")
                self.assertIn(drone["state"],("ready","charging"))
            self.assertEqual(sim.snapshot()["metrics"]["dispatch_cap"],3)

    def test_ground_first_ordering_seed_42(self):
        sim=OperationsSimulation(self.world,seed=42,fleet_size=20,scenario="dock_fire")
        self.run_to_end(sim)
        self.assertEqual(self.first_on_scene(sim,"I002"),"ground")
        self.assertEqual(sim._find_incident("I002")["briefing"]["source"],"ground crew")
        self.assertTrue(any(e["kind"] in ("drone_retask","drone_recall") and e.get("incident_id")=="I002" for e in sim.events))
        self.assertEqual(self.first_on_scene(sim,"I001"),"drone")

    def test_drone_first_ordering_seed_91_briefs_every_dispatched_agency(self):
        sim=OperationsSimulation(self.world,seed=91,fleet_size=20,scenario="dock_fire")
        briefs=[]
        for _ in range(60):
            sim.step(1);briefs+=[s for s in sim.signals if s["kind"]=="briefing" and s["from_type"]=="drone" and s not in briefs]
        for incident in sim.incidents:
            self.assertEqual(self.first_on_scene(sim,incident["id"]),"drone")
            recipients={rid for s in briefs if s["incident_id"]==incident["id"] for rid in s["to_ids"]}
            self.assertTrue(set(incident["resource_ids"])<=recipients,f"{incident['id']} brief must reach every dispatched unit")

    def test_units_are_real_pcmc_catalog_units(self):
        catalog={c["source_id"]:c for c in json.loads(CATALOG.read_text(encoding="utf8"))["resources"]}
        sim=OperationsSimulation(self.world,seed=42,fleet_size=10,scenario="dock_fire")
        self.assertTrue(sim.resources)
        for resource in sim.resources:
            self.assertTrue(resource["id"].startswith("PC-"))
            entry=catalog[resource["id"]]
            self.assertEqual(resource["name"],entry["name"])
            self.assertEqual(resource["agency"],entry["operator"])
            self.assertEqual(resource["real_position"][:2],[round(v,1) for v in entry["local_xy"]])
            if not entry["on_map"] and not resource.get("patrol"):
                self.assertGreater(resource["approach_m"],0,"off-map units pay a real approach distance")
        departures=[]
        for _ in range(5):
            sim.step(1)
        departures=[e for e in sim.events if e["kind"]=="ground_departure"]
        self.assertTrue(departures and "ETA to district" in departures[0]["message"])

    def test_random_incident_is_reproducible_and_runs_the_full_chain(self):
        a=OperationsSimulation(self.world,seed=5,fleet_size=10,scenario="dock_fire")
        b=OperationsSimulation(self.world,seed=5,fleet_size=10,scenario="dock_fire")
        ra=a.command("random_incident",{"category":"trapped"});rb=b.command("random_incident",{"category":"trapped"})
        self.assertTrue(ra["ok"]);self.assertEqual(ra["position"],rb["position"])
        self.assertNotEqual(a.command("random_incident",{"category":"trapped"})["position"],ra["position"])
        incident=a._find_incident(ra["incident_id"])
        self.assertIsNotNone(incident["building_id"])
        self.assertTrue(any(s["kind"]=="sos" and s["incident_id"]==incident["id"] for s in a.signals))
        self.assertTrue(incident["resource_ids"],"ground unit dispatched concurrently")
        self.assertFalse(a.command("random_incident",{"category":"volcano"})["ok"])
        self.run_to_end(a,1500)
        self.assertEqual(incident["status"],"resolved")
        self.assertTrue(incident["handed_over"])
        self.assertTrue(any(e["kind"] in ("drone_launch","drone_retask","drone_recall") and e.get("incident_id")==incident["id"] for e in a.events))

    def test_people_placement_is_believable(self):
        sim=OperationsSimulation(self.world,seed=7,fleet_size=10,scenario="dock_fire")
        people=sim.snapshot()["people"]
        self.assertEqual(len(people),sum(i["people"] for i in sim.incidents))
        for person in people:
            incident=sim._find_incident(person["incident_id"])
            if incident["category"] in ("fire","trapped"):
                self.assertEqual(person["on"],"roof")
                footprint=next(b for b in sim._building_index() if b[0]==person["building_id"])[2]
                self.assertTrue(_inside(person["position"][:2],footprint))
            else:
                self.assertEqual(person["on"],"ground")
                self.assertIsNone(sim._building_at(person["position"][:2]))
        self.assertEqual(people,OperationsSimulation(self.world,seed=7,fleet_size=10,scenario="dock_fire").snapshot()["people"])


class SnapshotContractTests(unittest.TestCase):
    def test_drone_signal_search_and_people_fields(self):
        sim=OperationsSimulation(fleet_size=10)
        for _ in range(4):sim.step(1)
        snap=sim.snapshot()
        json.dumps(snap,allow_nan=False)
        for drone in snap["drones"]:
            self.assertIn(drone["activity"],ACTIVITIES)
            self.assertIn(drone["behavior"],BEHAVIORS)
            self.assertGreaterEqual(drone["sensor_radius_m"],0)
            z=drone["position"][2]
            if z>3:self.assertAlmostEqual(drone["sensor_radius_m"],z*math.tan(math.radians(30)),delta=.1)
            self.assertLessEqual(drone["speed_mps"],18.01)
            self.assertIsInstance(drone["zone"],list)
        self.assertTrue(snap["signals"])
        for signal in snap["signals"]:
            self.assertEqual(set(signal),{"id","t","kind","from_type","from_id","to_ids","position","incident_id","text"})
            self.assertIn(signal["kind"],("sos","briefing","ack","recall","dispatch","arrival","supply_request","resource_request"))
            self.assertIn(signal["from_type"],("drone","resource","incident"))
            self.assertLessEqual(snap["t"]-signal["t"],6)
        self.assertTrue(snap["search_map"] and {"id","center","probability","last_observed","status"}<=set(snap["search_map"][0]))
        self.assertTrue(all(p["status"] in PERSON_STATUSES for p in snap["people"]))
        for _ in range(10):sim.step(1)
        self.assertTrue(all(sim.t-s["t"]<=6 for s in sim.snapshot()["signals"]),"signals older than 6 sim-seconds are dropped")

    def test_search_zones_partition_cells_and_observation_lowers_probability(self):
        sim=OperationsSimulation(fleet_size=10)
        zones=[set(d["zone"]) for d in sim.drones]
        union=set().union(*zones)
        self.assertEqual(sum(len(z) for z in zones),len(union),"zones are disjoint")
        self.assertEqual(union,{c for c,cell in sim.search_cells.items() if not cell["blocked"]})
        for drone in sim.drones:
            if drone["state"]=="patrolling":self.assertIn(drone["search_target"],drone["zone"])
        before={c:cell["p"] for c,cell in sim.search_cells.items()}
        for _ in range(60):sim.step(1)
        observed=[c for c,cell in sim.search_cells.items() if cell["last"] is not None]
        self.assertTrue(observed)
        self.assertTrue(any(sim.search_cells[c]["p"]<before[c]*.6 for c in observed))
        crashed=sim.drones[0]["id"];sim.command("fault",{"drone_id":crashed,"kind":"crash"})
        self.assertEqual(sim.drones[0]["zone"],[],"a failed aircraft's zone is redistributed")
        self.assertEqual(set().union(*[set(d["zone"]) for d in sim.drones]),union)

    def test_fleet_command_reinitialises_and_validates(self):
        sim=OperationsSimulation(fleet_size=20);old=sim.run_id
        self.assertTrue(sim.command("fleet",{"value":5})["ok"])
        self.assertEqual(len(sim.drones),5);self.assertNotEqual(sim.run_id,old)
        self.assertEqual(sim.scenario["id"],"rooftop_flood")
        for bad in (0,101,True,2.5,"5",None,float("inf")):
            self.assertFalse(sim.command("fleet",{"value":bad})["ok"],bad)
        self.assertEqual(len(sim.drones),5)

    def test_detect_snaps_and_returns_incident_id_with_open_cap(self):
        sim=OperationsSimulation()
        result=sim.command("detect",{"position":[-150,-160],"category":"trapped","people":3})
        self.assertTrue(result["ok"])
        incident=sim._find_incident(result["incident_id"])
        self.assertEqual(incident["building_id"],"B3")
        self.assertEqual(incident["position"][2],30.0,"roof height of the clicked building")
        self.assertFalse(sim.command("detect",{"position":[0,"x"],"category":"medical"})["ok"])
        added=0
        while sim.command("random_incident",{"category":"medical"})["ok"]:added+=1
        self.assertEqual(sum(i["status"]!="resolved" for i in sim.incidents),12)


class AgencyRequestTests(unittest.TestCase):
    def request(self,sim,request_id,**fields):
        payload={"request_id":request_id,"kind":"sos","position":[-100,-200],"category":"medical","people":2,"needs":["first_aid"],"priority":4,"requested_by":"DisruptionOps"}
        payload.update(fields)
        return sim.command("agency_request",payload)

    def test_accept_decline_and_idempotent_request_id(self):
        sim=OperationsSimulation()
        first=self.request(sim,"ORCH-1")
        self.assertTrue(first["ok"]);self.assertTrue(first["accepted"])
        self.assertTrue(first["plan"]);self.assertEqual(first["assigned_drones"],[sim._find_incident(first["incident_id"])["drone_id"]])
        self.assertIsNotNone(first["eta_s"])
        incident=sim._find_incident(first["incident_id"])
        self.assertEqual(incident["source"],"orchestrator");self.assertEqual(incident["request_id"],"ORCH-1")
        count=len(sim.incidents)
        again=self.request(sim,"ORCH-1",position=[200,200])
        self.assertTrue(again["duplicate"]);self.assertEqual(again["incident_id"],first["incident_id"]);self.assertEqual(len(sim.incidents),count)
        outside=self.request(sim,"ORCH-2",position=[5000,0])
        self.assertTrue(outside["ok"]);self.assertFalse(outside["accepted"]);self.assertIn("operating area",outside["reason"])
        world=_default_world();world["no_fly"]=[{"center":[100,-100],"radius":60}]
        restricted=self.request(OperationsSimulation(world),"ORCH-3",position=[100,-100])
        self.assertFalse(restricted["accepted"]);self.assertIn("no-fly",restricted["reason"])
        self.assertFalse(self.request(sim,"ORCH-4",kind="launch_missiles")["ok"])
        metrics=sim.snapshot()["metrics"]["agency"]
        self.assertEqual((metrics["requests_received"],metrics["accepted"],metrics["declined"]),(2,1,1))
        statuses=[e.get("status") for e in sim.events]
        self.assertIn("accepted",statuses);self.assertIn("declined",statuses);self.assertIn("dispatch_ordered",statuses)

    def test_chain_accept_assess_request_resources_handover_complete(self):
        sim=OperationsSimulation(fleet_size=10)
        result=self.request(sim,"ORCH-CHAIN",position=[-200,-300],people=5,needs=["first_aid","medical"])
        incident=sim._find_incident(result["incident_id"])
        self.assertEqual(incident["resource_requests"][0]["needed_kind"],"ambulance")
        for _ in range(900):
            sim.step(1)
            if incident["status"]=="resolved":break
        self.assertEqual(incident["status"],"resolved")
        self.assertIn(incident["first_on_scene"],("drone","ground"))
        self.assertEqual(incident["evacuated_people"],5,"capacity-2 ambulances must not double count")
        statuses=[e.get("status") for e in sim.events if e.get("incident_id")==incident["id"]]
        for expected in ("accepted","launched","dispatch_ordered","ground_arrived","handed_over","completed"):
            self.assertIn(expected,statuses)
        if incident["first_on_scene"]=="drone":
            self.assertIn("on_scene",statuses);self.assertIn("briefing",statuses)
            # 5 people exceed one 2-seat ambulance: the briefing raises a follow-up request.
            self.assertTrue(any(q["stage"]=="assessment" for q in incident["resource_requests"]))
        self.assertTrue(all(q["status"]=="fulfilled" for q in incident["resource_requests"] if q["stage"]=="report"))
        metrics=sim.snapshot()["metrics"]["agency"]
        self.assertGreaterEqual(metrics["resource_requests_raised"],metrics["resource_requests_fulfilled"])
        self.assertIsNotNone(metrics["median_time_to_briefing_s"])

    def test_random_issue_uses_agency_path(self):
        sim=OperationsSimulation()
        result=sim.command("random_incident",{"category":"medical"})
        self.assertTrue(result["ok"]);self.assertTrue(result["accepted"])
        incident=sim._find_incident(result["incident_id"])
        self.assertEqual(incident["source"],"orchestrator-sim")
        self.assertTrue(incident["request_id"].startswith("sim-"))


if __name__=="__main__":unittest.main()
