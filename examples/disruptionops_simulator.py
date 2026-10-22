"""Pretend to be DisruptionOps and test the drone-swarm agency over its WebSocket.

DisruptionOps is the orchestrator; the swarm is an independent agency. This script plays the
orchestrator: it sends rescue requests (`agency_request`) shaped like DisruptionOps reports and
prints how the swarm reacts: accept/decline with a reason, drones launched, briefings, the
situation reports and alerts the swarm sends (it never asks for units), the dispatches the orchestrator
makes to the agency nodes, unit status, handoffs and completions.

Only dependency:  pip install websockets
Usage:
  # full test (needs the swarm's INTEGRATION token, from the person running the server)
  python disruptionops_simulator.py --url wss://HOST/ops/ws --token INTEGRATION_TOKEN --requests 4
  # operator token instead (uses random_incident, same pipeline inside the swarm)
  python disruptionops_simulator.py --url ws://localhost:8787/ops/ws --token OPERATOR_TOKEN --as-operator
  # no token: watch only (public share links are read-only)
  python disruptionops_simulator.py --url wss://HOST/ops/ws --watch-only
Runs for --seconds (default 180) of wall time, then prints a summary. Simulation only:
nothing here dispatches real units.
"""
import argparse
import asyncio
import json
import random
import time
import uuid

from websockets.asyncio.client import connect

# Report texts taken from DisruptionOps' scripted PCMC scenario (backend/app/demo/scenarios.py,
# pccoe_pawana_night); the swarm decides the response itself from these facts.
REPORTS = [
    {"category": "person_stranded", "text": "Pawana water entering houses near Ravet bridge, family on terrace",
     "people": 5, "vulnerable": ["family"], "hazards": ["rising_water"], "callers": 3},
    {"category": "person_stranded", "text": "River water at Morya Gosavi ghat lanes, elderly couple inside",
     "people": 2, "vulnerable": ["elderly"], "hazards": ["rising_water"], "callers": 2},
    {"category": "fire", "text": "Fire at a chemical unit in MIDC, strong gas smell spreading",
     "people": 6, "injured": 2, "hazards": ["fire", "gas", "chemical"], "callers": 2},
    {"category": "structural_damage", "text": "Compound wall collapsed on parked cars after rain, someone trapped",
     "people": 1, "injured": 1, "hazards": ["collapse"], "callers": 2},
    {"category": "power_line", "text": "Live wire fallen in waterlogged lane near PCCOE, sparks",
     "people": 0, "hazards": ["live_wire", "water"], "callers": 1},
    {"category": "supply_shortage", "text": "Ravet relief camp short of drinking water",
     "people": 150, "hazards": ["no_drinking_water"], "callers": 1},
    {"category": "shelter_full", "text": "S B Patil school shelter full, more families arriving",
     "people": 40, "vulnerable": ["family", "children"], "callers": 1},
]
STATUS_WORDS = ("accept", "declin", "launch", "brief", "dispatch_order", "request", "dispatch", "arriv",
                "handoff", "handed", "supply", "resolved", "complet", "recall", "retask")


def envelope(action, payload, run_id):
    return json.dumps({"type": "command", "id": str(uuid.uuid4()), "action": action,
                       "expected_run_id": run_id, "payload": payload})


def pick_point(state, rng):
    b = (state.get("world") or {}).get("bounds") or [-500, -500, 500, 500]
    margin = 0.15
    x = rng.uniform(b[0] + (b[2] - b[0]) * margin, b[2] - (b[2] - b[0]) * margin)
    y = rng.uniform(b[1] + (b[3] - b[1]) * margin, b[3] - (b[3] - b[1]) * margin)
    return [round(x, 1), round(y, 1), 0.0]


def request_payload(report, position, n):
    return {"request_id": f"do-sim-{uuid.uuid4().hex[:8]}", "kind": "sos", "position": position,
            "category": report["category"], "people": max(1, report.get("people", 1)),
            "priority": 5 if report.get("injured") or "elderly" in report.get("vulnerable", []) else 3,
            "requested_by": "disruptionops-simulator",
            "situation": {"report_text": report["text"], "disruptionops_category": report["category"],
                          "callers": report.get("callers", 1), "reported_people": report.get("people"),
                          "vulnerable": report.get("vulnerable", []), "injured_reported": report.get("injured", 0),
                          "hazards": report.get("hazards", []), "sequence": n}}


async def run(args):
    rng = random.Random(args.seed)
    url = args.url + (("&" if "?" in args.url else "?") + "token=" + args.token if args.token else "")
    seen_events, my_incidents, acks, summary = set(), {}, {}, {"sent": 0, "accepted": 0, "declined": 0, "errors": 0}
    state = None
    async with connect(url, max_size=None) as ws:
        async def next_snapshot():
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "ack":
                    acks[msg.get("id")] = msg
                    show_ack(msg)
                elif msg.get("type") == "snapshot":
                    return msg["state"]

        def show_ack(msg):
            if not msg.get("ok"):
                summary["errors"] += 1
                print(f"  x rejected: {msg.get('error')}")
                return
            result = msg.get("result", msg)
            incident = result.get("incident_id")
            if incident:
                my_incidents[incident] = result.get("request_id")
            accepted = result.get("accepted")
            if accepted is True:
                summary["accepted"] += 1
            elif accepted is False:
                summary["declined"] += 1
            print(f"  -> swarm: {'ACCEPTED' if accepted else 'DECLINED' if accepted is False else 'ok'}"
                  f" incident={incident} eta={result.get('eta_s')} drones={result.get('assigned_drones')}"
                  f" reason={result.get('reason')}")
            for step in result.get("plan") or []:
                print(f"      plan: {step}")

        state = await next_snapshot()
        print(f"Connected. scenario={state['scenario'].get('id')} fleet={len(state['drones'])} "
              f"world={(state.get('world') or {}).get('id', 'pune-district')}")
        for e in state.get("events", []):
            seen_events.add(e["id"])
        seen_messages = {m["id"] for m in state.get("messages", [])}
        start, n = time.monotonic(), 0
        next_send = start
        while time.monotonic() - start < args.seconds:
            if not args.watch_only and n < args.requests and time.monotonic() >= next_send:
                report = REPORTS[n % len(REPORTS)]
                n += 1
                summary["sent"] += 1
                if args.as_operator:
                    print(f"\n[DisruptionOps -> swarm] #{n} random_incident ({report['category']})")
                    await ws.send(envelope("random_incident", {}, state["run_id"]))
                else:
                    position = pick_point(state, rng)
                    print(f"\n[DisruptionOps -> swarm] #{n} agency_request: \"{report['text']}\" at {position[:2]}")
                    await ws.send(envelope("agency_request", request_payload(report, position, n), state["run_id"]))
                next_send = time.monotonic() + args.interval
            state = await next_snapshot()
            for m in state.get("messages", []):
                if m["id"] in seen_messages:
                    continue
                seen_messages.add(m["id"])
                if m["kind"] == "situation_report":
                    b = m["body"]
                    print(f"  SITUATION r{b.get('revision')} {m['incident_id']}: {b.get('text')} (confidence {b.get('confidence')})")
                elif m["kind"] == "alert":
                    print(f"  ALERT [{m['body'].get('severity')}] {m['body'].get('alert')}: {m['text']}")
                elif m["kind"] == "dispatch":
                    print(f"  DISPATCH {m['from']} -> {m['to']}: {m['text']}")
                elif m["kind"] in ("unit_status", "handoff") and m["incident_id"] in my_incidents:
                    print(f"  UNIT {m['from']}: {m['text']}")
            for e in state.get("events", []):
                if e["id"] in seen_events:
                    continue
                seen_events.add(e["id"])
                text = e.get("message", "")
                mine = e.get("incident_id") in my_incidents
                if args.verbose or mine or any(w in e.get("kind", "") for w in STATUS_WORDS):
                    print(f"  t={e.get('time', 0):7.1f} [{e.get('kind')}] {text[:140]}")
        print("\n=== summary ===")
        print(json.dumps(summary))
        for inc in state.get("incidents", []):
            if inc["id"] in my_incidents:
                reqs = [(q.get("needed_kind"), q.get("qty"), q.get("status")) for q in inc.get("resource_requests", [])]
                print(f"{inc['id']}: status={inc['status']} first_on_scene={inc.get('first_on_scene')} "
                      f"units={inc.get('resource_ids')} orchestrator_dispatched={reqs}")
        metrics = state.get("metrics", {}).get("agency")
        if metrics:
            print("agency metrics:", json.dumps(metrics))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="ws://127.0.0.1:8787/ops/ws")
    p.add_argument("--token", default="", help="integration token (or operator token with --as-operator)")
    p.add_argument("--as-operator", action="store_true", help="use random_incident (operator token)")
    p.add_argument("--watch-only", action="store_true")
    p.add_argument("--requests", type=int, default=4)
    p.add_argument("--interval", type=float, default=20.0, help="seconds between requests")
    p.add_argument("--seconds", type=float, default=180.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--verbose", action="store_true", help="print every swarm event")
    args = p.parse_args()
    if not args.token and not args.watch_only:
        p.error("give --token (integration, or operator with --as-operator), or use --watch-only")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
