"""Replay operations acceptance cases against the current prepared local world.

Run from the repository root:
  .conda-drones/python.exe tests/operations_acceptance.py

This uses only the in-process kinematic demonstrator. It never contacts an
agency backend, real aircraft, Cloudflare, or external services.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from swarm_ops.engine import OperationsSimulation, SCENARIOS  # noqa: E402


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(report, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    temporary.replace(destination)


def replay(world, scenario, seed, fleet, seconds, fault=None):
    start = time.perf_counter()
    simulation = OperationsSimulation(world, seed=seed, fleet_size=fleet)
    if scenario != simulation.scenario["id"]:
        result = simulation.command("scenario", {"id":scenario,"seed":seed})
        if not result["ok"]:
            raise RuntimeError(result)
    initialized_ms = (time.perf_counter()-start)*1000
    failures = []
    fault_evidence = None
    if fault:
        target = simulation.incidents[0]
        aircraft = target["drone_id"]
        result = simulation.command("fault", {"drone_id":aircraft,"kind":fault})
        fault_evidence = {"kind":fault,"drone_id":aircraft,"incident_id":target["id"],"accepted":result["ok"]}
        if not result["ok"]:
            failures.append("Fault injection was not accepted: "+str(result))
    started = time.perf_counter()
    for _ in range(seconds):
        simulation.step(1.0)
    elapsed = time.perf_counter()-started
    snapshot = simulation.snapshot(include_world=False)
    incidents = []
    for incident in snapshot["incidents"]:
        row = {key:incident.get(key) for key in ("id","category","status","people","evacuated_people","handed_over","required_kinds","completed_kinds","wait_reason","uncertain_delivery","resolved_at")}
        row["position"] = incident["position"]
        incidents.append(row)
        if incident["status"] != "resolved":
            failures.append(f"{incident['id']} remains {incident['status']}: {incident.get('wait_reason')}")
        if not incident["handed_over"]:
            failures.append(f"{incident['id']} has no acknowledged physical responder handoff")
        if not set(incident["required_kinds"]).issubset(incident["completed_kinds"]):
            failures.append(f"{incident['id']} missing a required agency completion")
        if incident["evacuated_people"] != incident["people"]:
            failures.append(f"{incident['id']} transferred {incident['evacuated_people']}/{incident['people']} people")
    if fault == "link":
        target = next(i for i in incidents if i["id"]==fault_evidence["incident_id"])
        if not target["uncertain_delivery"]:
            failures.append("Radio silence uncertainty was incorrectly erased")
        fault_evidence["uncertainty_retained"] = target["uncertain_delivery"]
        fault_evidence["recall_confirmation_required"] = True
    if snapshot["metrics"]["external_ground"]:
        failures.append("Acceptance runner unexpectedly switched to external authority")
    return {"scenario":scenario,"seed":seed,"fleet":fleet,"simulated_seconds":seconds,
            "initialization_ms":round(initialized_ms,3),"simulation_compute_seconds":round(elapsed,3),
            "passed":not failures,"failures":failures,"fault":fault_evidence,
            "flood_closed_segments":sum(c["reason"]=="flood" for c in snapshot["road_conditions"]),
            "bridge_closed_segments":sum(c["reason"]=="bridge_damage" for c in snapshot["road_conditions"]),
            "incidents":incidents,"metrics":snapshot["metrics"]}


def percentile(samples, ratio):
    ordered = sorted(samples)
    return ordered[min(len(ordered)-1,int((len(ordered)-1)*ratio))]


def profile(world, fleet, frames, dense=False):
    simulation = OperationsSimulation(world, fleet_size=fleet)
    if dense:
        # Explicit workload stress fixture: every aircraft fully charged so the
        # whole fleet flies zone searches, rather than benchmarking reserves.
        for drone in simulation.drones:
            drone["energy_wh"]=drone["capacity_wh"];drone["battery"]=1.0
        simulation._allocate()
    steps, snapshots, totals = [], [], []
    sizes = []
    moving_counts = []
    airborne_counts = []
    for _ in range(frames):
        start = time.perf_counter()
        simulation.step(.1)
        middle = time.perf_counter()
        frame = simulation.snapshot(include_world=False)
        serialized = json.dumps(frame, allow_nan=False, separators=(",",":"))
        end = time.perf_counter()
        steps.append((middle-start)*1000)
        snapshots.append((end-middle)*1000)
        totals.append((end-start)*1000)
        sizes.append(len(serialized.encode("utf-8")))
        moving_counts.append(sum(d["state"] not in ("ready","charging","crashed","emergency_landed") for d in frame["drones"]))
        airborne_counts.append(sum(d["active"] and d["position"][2]>8 for d in frame["drones"]))
        if "world" in frame:
            raise AssertionError("Static world geometry leaked into an incremental snapshot")
    def summary(samples):
        return {"mean_ms":round(statistics.mean(samples),3),"p95_ms":round(percentile(samples,.95),3),"max_ms":round(max(samples),3)}
    return {"fleet":fleet,"workload":"dense coverage stress fixture" if dense else "normal scenario coverage and reserve policy","requested_hz":10,"frame_budget_ms":100,"frames":frames,
            "step":summary(steps),"snapshot_plus_json":summary(snapshots),"combined":summary(totals),
            "mean_frame_bytes":round(statistics.mean(sizes)),"max_frame_bytes":max(sizes),
            "mean_flying_or_working_drones":round(statistics.mean(moving_counts),2),"max_flying_or_working_drones":max(moving_counts),
            "mean_airborne_drones":round(statistics.mean(airborne_counts),2),"max_airborne_drones":max(airborne_counts),
            "passed_mean_budget":statistics.mean(totals)<100,
            "passed_p95_budget":percentile(totals,.95)<100,"frames_over_budget":sum(value>100 for value in totals),
            "limitations":"In-process CPU kinematics and serialization only; excludes browser rendering, networking, perception, and hardware flight control."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world",type=Path,default=ROOT/"assets/operations/pune-world.json")
    parser.add_argument("--output",type=Path,default=ROOT/"docs/implementation/acceptance-results.json")
    parser.add_argument("--seeds",type=int,nargs="+",default=[42,91])
    parser.add_argument("--fleet",type=int,default=50)
    parser.add_argument("--seconds",type=int,default=600)
    parser.add_argument("--profile-fleet",type=int,default=100)
    parser.add_argument("--profile-frames",type=int,default=100)
    args = parser.parse_args()
    if args.seconds<1 or args.profile_frames<1 or not 1<=args.fleet<=100 or not 1<=args.profile_fleet<=100:
        parser.error("seconds/frames must be positive; fleet sizes must be 1..100")
    world = json.loads(args.world.read_text(encoding="utf-8"))
    points = [point for road in world["roads"] for point in road["points"]]
    bounds = world["bounds"]
    out_of_bounds = sum(not bounds[0]-1e-6<=p[0]<=bounds[2]+1e-6 or not bounds[1]-1e-6<=p[1]<=bounds[3]+1e-6 for p in points)
    report = {"schema_version":1,"status":"running","generated_at":datetime.now(timezone.utc).isoformat(),
              "local_only":True,"world_path":str(args.world.resolve()),"world_sha256":digest(args.world),
              "engine_sha256":digest(ROOT/"swarm_ops"/"engine.py"),
              "world":{"buildings":len(world["buildings"]),"roads":len(world["roads"]),"bounds":bounds,
                       "road_points":len(points),"out_of_bounds_road_points":out_of_bounds,"terrain_present":"terrain" in world},
              "cases":[],"profiles":[],"limitations":["Prescribed synthetic flood corridor and elevated crossings, not hydrodynamics.","Deterministic simulation harness, not decentralized consensus or certified flight physics.","Rescue extraction and crew acknowledgment are simulated; transfer routes are executed kinematically."]}
    save(report,args.output)
    started = time.perf_counter()
    for scenario in SCENARIOS:
        for seed in args.seeds:
            result = replay(world,scenario,seed,args.fleet,args.seconds)
            report["cases"].append(result);save(report,args.output)
            print(json.dumps({"scenario":scenario,"seed":seed,"passed":result["passed"],"failures":result["failures"],"compute_s":result["simulation_compute_seconds"]}),flush=True)
    for scenario,fault in (("rooftop_flood","crash"),("medical_supply","battery"),("blocked_bridge","link")):
        result = replay(world,scenario,43,args.fleet,args.seconds,fault=fault)
        report["cases"].append(result);save(report,args.output)
        print(json.dumps({"scenario":scenario,"fault":fault,"passed":result["passed"],"failures":result["failures"]}),flush=True)
    report["profiles"].append(profile(world,args.profile_fleet,args.profile_frames))
    report["profiles"].append(profile(world,args.profile_fleet,args.profile_frames,dense=True))
    report["passed"] = not out_of_bounds and all(c["passed"] for c in report["cases"]) and all(p["passed_mean_budget"] for p in report["profiles"])
    report["all_profiles_meet_p95_budget"] = all(p["passed_p95_budget"] for p in report["profiles"])
    report["status"] = "complete"
    report["wall_seconds"] = round(time.perf_counter()-started,3)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save(report,args.output)
    print(json.dumps({"passed":report["passed"],"cases":len(report["cases"]),"profile":report["profiles"][0],"artifact":str(args.output.resolve())}),flush=True)
    return 0 if report["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
