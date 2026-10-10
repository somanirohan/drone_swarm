# Local operations WebSocket contract

For a complete connection guide, client examples, proxy configuration, field meanings and all action options, see [the friend handoff](FRIEND-WEBSOCKET-HANDOFF.md). For implemented use cases and known gaps, see [handled cases](HANDLED-CASES.md).

Protocol: `disruptionops.swarm.v2`; snapshot schema: `2`. Default address: `ws://localhost:8787/ops/ws`. HTTPS viewers use `wss://<viewer-host>/ops/ws`.

## Session and authorization

`GET /ops/session` returns an exercise operator token only to a direct loopback request addressed to localhost/127.0.0.1/::1, without forwarding headers. Public tunnel requests return a view-only session. Pass an operator token as `?token=...` during WebSocket upgrade. Tokens are private and must not enter committed files, screenshots or shared logs. The application removes an explicitly supplied `#control=...` fragment and keeps it in session storage.

The server rejects cross-origin browser WebSocket handshakes. Viewers may receive snapshots and event replay but cannot change the exercise. A separate `OPS_INTEGRATION_TOKEN` can authorize only `external_resource_update`; it cannot invoke operator actions. This integration channel is reserved and is not connected to DisruptionOps in the running demonstration. Ground updates, when enabled later, contain handoffs and briefing revisions together; there is no separate `external_handoff` action.

## Server → client

```json
{
  "type": "snapshot",
  "protocol": "disruptionops.swarm.v2",
  "seq": 120,
  "server_time": 1791590400.0,
  "state": {
    "schema_version": 2,
    "run_id": "opaque-run-identifier",
    "revision": 3,
    "t": 12.0,
    "paused": false,
    "speed": 1,
    "scenario": {"id": "rooftop_flood", "seed": 42},
    "drones": [], "resources": [], "incidents": [],
    "events": [], "coverage": [], "metrics": {},
    "flood": {}, "road_conditions": []
  }
}
```

Actual collections contain full entities. Local position vectors are `[east_m, north_m, height_above_local_ground_m]` around the shared geographic anchor. The visualizer adds interpolated terrain elevation; no flight-qualified vertical datum is implied. Headings are radians. Battery is a 0–1 fraction; energy/capacity are Wh. Simulation time and ETA are seconds; `server_time` is Unix time.

`world` is included on the first snapshot and whenever `run_id` changes. A slow client's reset snapshots retain it until a send completes. Subsequent snapshots omit it; preserve your world cache. Within a run, accept increasing `seq`; a new run resets client selection/state and sequence tracking. Sequence restarts with the server; the fresh run identifier distinguishes a restart.

Entity fields describe their actual state: aircraft contain task, payload stock, link, active flag, energy and planned route; resources contain transport, route, agency assignment, capacity and service/arrival state; incidents contain needs, briefing, required/completed agency kinds, supply and evacuation progress. The `simulation_only` metric must remain visible to API consumers.

```json
{"type":"ack","id":"request-uuid","ok":true,"result":{"ok":true,"action":"pause","run_id":"opaque-run-identifier","revision":4}}
```

Rejected commands return `ok:false` and an `error` string. Invalid JSON may return `id:null`. An acknowledgment confirms a command was accepted by the exercise engine; it does not mean an incident was resolved or an aircraft executed a real maneuver.

## Client → server

```json
{
  "type": "command",
  "id": "request-uuid",
  "expected_run_id": "opaque-run-identifier",
  "action": "fault",
  "payload": {"drone_id": "D01", "kind": "battery"}
}
```

| Action | Payload |
|---|---|
| `scenario` | `id`: rooftop_flood / medical_supply / blocked_bridge; optional integer `seed` |
| `random_scenario` | `{}` |
| `reset` | `{}`; optional integer seed |
| `pause`, `resume` | `{}` |
| `speed` | finite `value` from 0.25 to 16 |
| `fault` | existing `drone_id`; `kind`: crash / battery / link |
| `release_resource` | existing `resource_id`; cannot release a committed responder |
| `ack_handoff` | existing `incident_id`, matching physically arrived `resource_id` |

Use a fresh ID for each intended action and include the current `expected_run_id`. The server caches 2,000 acknowledgments per process, with operator/integration identity separation. Retrying the exact command returns the original acknowledgment across reconnects. Reusing an ID with different action/payload/run expectation is rejected. After an ambiguous timeout, reuse the same ID if retrying within the same process; after a restart, read the new state before issuing any action. The browser does not automatically retry timed-out mutations.

## Transport limits and recovery

JSON text only; input maximum 16 KiB. Incoming queue maximum 8 messages. More than 10 commands in a rolling second closes with code 1008; oversized input closes with 1009; binary input closes with 1003. Ping interval is 20 s, ping timeout 30 s. Each client's outgoing telemetry queue is bounded to 2 frames, dropping older snapshots under pressure. That prevents an idle viewer from accumulating an unbounded telemetry backlog.

The browser reconnects with bounded exponential delay and marks old telemetry stale after 7 s. It disables exercise controls while disconnected. This is snapshot recovery, not guaranteed event delivery. To recover events:

```text
GET /ops/events?after=123&limit=100
```

Response contains `events` with increasing durable SQLite `cursor` values and `next_cursor`. Store the last processed cursor, deduplicate events using `(run_id,id)`, and request subsequent pages (maximum 500). SQLite events survive server restart; simulation state and command acknowledgment cache do not. There is currently no automatic journal retention policy.

Other endpoints: `GET /health` reports protocol/client/integration status; `GET /ops/state` provides a current full snapshot. `/`, `/web/` and `/assets/` serve only allowlisted UI/assets; traversal outside those roots is rejected. The default bind is loopback. Cloudflare terminates public TLS and forwards the local HTTP/WS service; it does not run the simulation.

## Future DisruptionOps boundary

Use the optional connector's durable outbox and scoped import validation rather than browser controls for authoritative agency data. Imported observations need stable source IDs, incident mappings and verified coordinates. A ground handoff must match the active assignment and current briefing revision. Connecting this requires the reviewed adapter described in `disruptionops-integration.md`; the local exercise does not claim production interoperability or external dispatch.
