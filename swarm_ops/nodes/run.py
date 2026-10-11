"""Run one agency node as a separate process over the WebSocket.

    python -m swarm_ops.nodes.run --node ambulance_service --url ws://127.0.0.1:8787/ops/ws --token $OPS_INTEGRATION_TOKEN

The process attaches (the in-process node stands down), reads ``dispatch`` messages addressed to it from the
snapshots, picks the nearest of its own available units, orders it (``node_order``) and reports ``unit_status``
(``node_post``). Needs only ``websockets``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import urllib.parse
import uuid

KINDS = {"ambulance_service": ("ambulance",), "fire_service": ("fire_engine",), "rescue_service": ("rescue", "boat"),
         "police_service": ("police",), "public_works": ("engineering", "pump", "water_tanker", "supply_truck", "bus")}


async def run(node, url, token, seconds=None):
    from websockets.asyncio.client import connect
    seen, pending, seq = 0, [], 0

    async def command(ws, action, payload):
        nonlocal seq
        seq += 1
        await ws.send(json.dumps({"type": "command", "id": f"{node}-{uuid.uuid4().hex[:8]}-{seq}", "action": action, "payload": {"node": node, **payload}}))

    async with connect(f"{url}{'&' if '?' in url else '?'}token={urllib.parse.quote(token)}", max_size=None) as ws:
        await command(ws, "node_attach", {"lease_s": 120})
        loop_end = None if seconds is None else asyncio.get_running_loop().time() + seconds
        last_attach = asyncio.get_running_loop().time()
        async for raw in ws:
            packet = json.loads(raw)
            if packet.get("type") != "snapshot":
                continue
            state = packet["state"]
            for message in state.get("messages", []):
                if message["id"] > seen and message["to"] == node and message["kind"] == "dispatch" and not (message.get("body") or {}).get("executed"):
                    pending.append(dict(message["body"], incident_id=message["incident_id"], qty=int(message["body"].get("qty") or 1)))
                seen = max(seen, message["id"])
            incidents = {i["id"]: i for i in state.get("incidents", [])}
            for order in list(pending):
                incident = incidents.get(order["incident_id"])
                if incident is None or incident["status"] == "resolved":
                    pending.remove(order)
                    continue
                own = [r for r in state.get("resources", []) if r["kind"] in KINDS[node] and r["state"] == "available"]
                if not own:
                    continue
                best = min(own, key=lambda r: math.dist(r["position"][:2], incident["position"][:2]))
                await command(ws, "node_order", {"unit_id": best["id"], "incident_id": order["incident_id"], "kind": order["kind"], "dispatch_id": order["dispatch_id"]})
                await command(ws, "node_post", {"kind": "unit_status", "to": "command_center", "incident_id": order["incident_id"], "text": f"{best['id']} dispatched by remote {node}.",
                                                "body": {"unit_id": best["id"], "kind": best["kind"], "state": "en_route", "phys_state": "enroute", "incident_id": order["incident_id"],
                                                         "dispatch_id": order["dispatch_id"]}})
                order["qty"] -= 1
                if order["qty"] <= 0:
                    pending.remove(order)
                await asyncio.sleep(0.2)
            now = asyncio.get_running_loop().time()
            if now - last_attach > 60:
                last_attach = now
                await command(ws, "node_attach", {"lease_s": 120})
            if loop_end is not None and now > loop_end:
                break
        await command(ws, "node_detach", {})


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run one agency node as a separate process.")
    parser.add_argument("--node", required=True, choices=sorted(KINDS))
    parser.add_argument("--url", default="ws://127.0.0.1:8787/ops/ws")
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args.node, args.url, args.token))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
