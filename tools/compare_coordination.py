"""Run the same scenario and seed in central, decentralized and orchestrated coordination and compare.

    .\\.conda-drones\\python.exe tools\\compare_coordination.py [--seed 42] [--fleet 20] [--seconds 600]
        [--scenario rooftop_flood] [--world pune|default] [--loss 0.0] [--extra 2]

Simulation only. Per mode it reports: time to first assessment (incident created -> first aircraft on scene),
search coverage %, aerial sub-tasks completed, recovery time after a crash (the aircraft flying to the farthest
mission is crashed at --crash-at s), and messages
sent (bus messages + mesh radio messages). Recovery: decentralized = the crashed drone's own sub-task is held
by a peer (or done); central/orchestrated = a new aircraft is sent to that incident.

Orchestrated mode uses a minimal DisruptionOps stand-in written here: it assigns one assess drone per incident
and re-assigns immediately when a drone_fault alert for that incident arrives.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swarm_ops.engine import OperationsSimulation  # noqa: E402


def build(mode, args):
    world = None
    if args.world == "pune":
        path = ROOT / "assets" / "operations" / "pune-world.json"
        world = json.loads(path.read_text(encoding="utf8")) if path.exists() else None
    sim = OperationsSimulation(world=world, seed=args.seed, fleet_size=args.fleet, scenario=args.scenario, coordination=mode)
    if mode == "decentralized" and args.loss:
        sim.command("set_coordination", {"mode": "decentralized", "packet_loss": args.loss})
    return sim


class StandIn:
    """DisruptionOps stand-in for dependent mode: one assess drone per incident, re-assign after a fault alert."""

    def __init__(self, sim):
        self.sim, self.done, self.seen_alerts = sim, set(), set()

    def tick(self):
        sim = self.sim
        for incident in sim.incidents:
            if incident["status"] == "resolved" or incident["id"] in self.done:
                continue
            ack = sim.command("assign", {"incident_id": incident["id"], "drones": [{"count": 1, "role": "assess"}]})
            if ack.get("ok"):
                self.done.add(incident["id"])
        for m in sim.bus.recent(200):
            body = m.get("body") or {}
            if m["kind"] == "alert" and body.get("alert") == "drone_fault" and body.get("fault") == "crash" and m["id"] not in self.seen_alerts:
                self.seen_alerts.add(m["id"])
                iid = body.get("incident_id") or next((i["id"] for i in sim.incidents if body.get("drone_id") in i.get("assigned_drones", [])), None)
                if iid:
                    sim.command("assign", {"incident_id": iid, "drones": [{"count": 1, "role": "assess"}]})


def working_on(sim, iid):
    return [d for d in sim.drones if d["active"] and d["task_id"] == iid and d["state"] in ("enroute", "observing")]


def run(mode, args):
    sim = build(mode, args)
    stand_in = StandIn(sim) if mode == "orchestrated" else None
    crash = {"drone": None, "incident": None, "t": None, "recovered_t": None}
    extra_at = {int(30 + 20 * k) for k in range(args.extra)}
    for second in range(args.seconds):
        if second in extra_at:
            sim.command("random_incident", {})
        if stand_in:
            stand_in.tick()
        if second == args.crash_at and crash["drone"] is None:
            candidates = []
            for incident in sim.incidents:
                for d in working_on(sim, incident["id"]):
                    if d["state"] == "enroute":
                        candidates.append((-math.dist(d["position"][:2], incident["position"][:2]), d["id"], incident["id"]))
            if candidates:
                _, did, iid = sorted(candidates)[0]
                job = next(d for d in sim.drones if d["id"] == did).get("subtask")
                before = {d["id"] for d in working_on(sim, iid)}
                sim.command("fault", {"drone_id": did, "kind": "crash"})
                crash.update(drone=did, incident=iid, t=sim.t, job=job, before=before)
        sim.step(1)
        if crash["t"] is not None and crash["recovered_t"] is None:
            incident = sim._find_incident(crash["incident"])
            if crash["job"]:      # decentralized: the crashed drone's own sub-task is held (or done) by a peer
                taken = any(crash["job"] in sim.agents[d["id"]].cbba.bundle or crash["job"] in sim.agents[d["id"]].cbba.done
                            for d in sim.drones if d["active"] and sim.agents[d["id"]].cbba is not None)
            else:                 # central / orchestrated: a NEW aircraft is sent to that incident
                taken = any(d["id"] not in crash["before"] for d in working_on(sim, crash["incident"]))
            if taken or (incident and incident["handed_over"]):
                crash["recovered_t"] = sim.t
    snap = sim.snapshot(include_world=False)
    delays = [i["drone_on_scene_at"] - i["created_at"] for i in sim.incidents if i.get("drone_on_scene_at") is not None]
    assessed = len(delays)
    supply = snap["metrics"]["supply_drops"]
    if mode == "decentralized":
        completed = len(sim.decentral.done_log)
        kinds = {}
        for _, _, j in sim.decentral.done_log:
            k = j.split("/")[1].split("-")[0]
            kinds[k] = kinds.get(k, 0) + 1
    else:
        completed, kinds = assessed + supply, {"assess": assessed, "supply": supply}
    return {"mode": mode, "incidents": len(sim.incidents), "assessed": assessed,
            "first_assessment_s": round(min(delays), 1) if delays else None,
            "mean_assessment_s": round(sum(delays) / len(delays), 1) if delays else None,
            "coverage_pct": round(100 * snap["metrics"]["covered_patches"] / max(1, snap["metrics"]["total_patches"]), 1),
            "subtasks_completed": completed, "by_kind": kinds,
            "crash": crash["drone"], "crash_job": crash.get("job"), "recovery_s": None if crash["recovered_t"] is None else round(crash["recovered_t"] - crash["t"], 1),
            "bus_messages": sim.bus._seq, "mesh_messages": snap["mesh"]["messages_sent"], "mesh_transmissions": snap["mesh"]["transmissions"],
            "mesh_dropped": snap["mesh"]["dropped"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fleet", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--scenario", default="rooftop_flood")
    parser.add_argument("--world", choices=("pune", "default"), default="pune")
    parser.add_argument("--loss", type=float, default=0.0, help="mesh packet loss in decentralized mode")
    parser.add_argument("--extra", type=int, default=2, help="extra random incidents (t = 30, 50, ... s)")
    parser.add_argument("--crash-at", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    rows = [run(mode, args) for mode in ("central", "decentralized", "orchestrated")]
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"scenario {args.scenario}, world {args.world}, seed {args.seed}, fleet {args.fleet}, {args.seconds} s, decentralized loss {args.loss:.0%} (simulation only)")
    cols = [("mode", 13), ("assessed", 9), ("first_assessment_s", 11), ("mean_assessment_s", 10), ("coverage_pct", 9), ("subtasks_completed", 9),
            ("recovery_s", 9), ("bus_messages", 8), ("mesh_messages", 8)]
    heads = {"first_assessment_s": "first_s", "mean_assessment_s": "mean_s", "coverage_pct": "cover%", "subtasks_completed": "subtasks",
             "recovery_s": "recover_s", "bus_messages": "bus_msg", "mesh_messages": "mesh_msg"}
    print("  ".join(heads.get(c, c).ljust(w) for c, w in cols))
    for r in rows:
        print("  ".join(str(r[c] if r[c] is not None else "-").ljust(w) for c, w in cols) + f"  {r['by_kind']}  crash {r['crash']}")


if __name__ == "__main__":
    main()
