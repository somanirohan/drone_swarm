# Local operations WebSocket contract

For a complete connection guide, client examples, proxy configuration, field meanings and all action options, see [the friend handoff](FRIEND-WEBSOCKET-HANDOFF.md). For implemented use cases and known gaps, see [handled cases](HANDLED-CASES.md).

Protocol: `disruptionops.swarm.v2`; snapshot schema: `2`. Default address: `ws://localhost:8787/ops/ws`. HTTPS viewers use `wss://<viewer-host>/ops/ws`.

## Session and authorization

`GET /ops/session` returns an exercise operator token only to a direct loopback request addressed to localhost/127.0.0.1/::1, without forwarding headers. Public tunnel requests return a view-only session. Pass an operator token as `?token=...` during WebSocket upgrade. Tokens are private and must not enter committed files, screenshots or shared logs. The application removes an explicitly supplied `#control=...` fragment and keeps it in session storage.

The server rejects cross-origin browser WebSocket handshakes. Viewers may receive snapshots and event replay but cannot change the exercise. A separate `OPS_INTEGRATION_TOKEN` can authorize only `external_resource_update` and `agency_request`; it cannot invoke operator actions. This integration channel is reserved and is not connected to DisruptionOps in the running demonstration. Ground updates, when enabled later, contain handoffs and briefing revisions together; there is no separate `external_handoff` action.

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
| `scenario` | `id`: rooftop_flood / medical_supply / blocked_bridge / dock_fire; optional integer `seed` |
| `fleet` | integer `value` 1–100 (UI offers 5/10/20/25/40/50); re-initialises the current scenario and seed with that fleet size (new `run_id`) |
| `detect` | `position` `[x,y]` or `[x,y,z]` (map click), `category` medical / flood / fire / trapped / blocked_route, optional `people`, `severity` 1–5, `needs` ⊂ first_aid/medical/food/assessment/rescue, `title`. Snapped (roof of the clicked building for fire/trapped/flood, nearest open road point for medical/blocked_route) and sent through the same `agency_request` path (`source:"orchestrator-sim"`). Ack carries `incident_id`; a declined request is `ok:false` with the reason. |
| `random_incident` | optional `category` (as above). Picks a valid location reproducibly from (seed, per-run counter): a real building roof for fire/trapped, a roof inside the flood corridor for flood (boat case), a dry reachable road point for medical/blocked_route; never in the no-fly zone. Same agency path; ack carries `incident_id` and `result.category`. At most 12 open incidents. |
| `random_scenario` | `{}` |
| `reset` | `{}`; optional integer seed |
| `pause`, `resume` | `{}` |
| `speed` | finite `value` from 0.25 to 16 |
| `fault` | existing `drone_id`; `kind`: crash / battery / link |
| `release_resource` | existing `resource_id`; cannot release a committed responder |
| `ack_handoff` | existing `incident_id`, matching physically arrived `resource_id` |

Use a fresh ID for each intended action and include the current `expected_run_id`. The server caches 2,000 acknowledgments per process, with operator/integration identity separation. Retrying the exact command returns the original acknowledgment across reconnects. Reusing an ID with different action/payload/run expectation is rejected. After an ambiguous timeout, reuse the same ID if retrying within the same process; after a restart, read the new state before issuing any action. The browser does not automatically retry timed-out mutations.

## Snapshot additions (schema 2, additive)

Per drone, in addition to the existing fields:

| Field | Meaning |
|---|---|
| `sensor_radius_m` | ground footprint radius = altitude × tan 30° when above 3 m, else 0 |
| `activity` | docked / launching / transit / scanning / assessing / delivering / returning / charging / holding / failed |
| `behavior` | behaviour-tree node: search / confirm / assess / relay / return / charge / idle / hold / failed |
| `speed_mps` | measured speed over the last sub-step (0 when parked) |
| `zone` | search-cell ids owned by this aircraft (Voronoi partition, recomputed only when an aircraft joins/fails; empty for docked fleets) |
| `target` | `{type: cell|incident|dock|base, id, position}` or `null` |
| `search_target`, `dock_id`, `home`, `retasked` | current sweep cell; dock and own landing pad (dock scenarios); `"supply"` when a ground-first crew let it finish only a drop |

Top level:

- `signals`: communication pulses from the last 6 simulated seconds: `{id:int, t, kind, from_type: drone|resource|incident, from_id, to_ids[], position[x,y,z], incident_id, text}`. Kinds: `sos`, `dispatch`, `arrival`, `briefing`, `ack`, `recall`, `supply_request`, and **`resource_request`** (swarm → orchestrator, `to_ids:["orchestrator"]`). Recipients may include the pseudo-ids `control` and `orchestrator`.
- `docks`: `{id, name, position, drone_ids, docked, launched}`; `metrics.launched`, `metrics.dispatch_cap` (3) in dock scenarios, else `null`.
- `search_map`: `{id, center[x,y,0], size_m:100, probability, prior, last_observed, status: unsearched|searched|cleared|blocked}`. Prior is higher near water, dense buildings and reported incidents; it drops ×0.35 when a sensor footprint covers the cell and slowly relaxes back. `coverage` is kept for older clients.
- `people`: one entry per person in need `{id, incident_id, position, on: roof|ground|boat_site, building_id, status, need}`; status ∈ waiting / assessed / supplied / being_rescued / evacuated / hospitalised. Positions are deterministic from the seed (inside the roof footprint, beside the road, or at the boat pickup).
- Incidents add `building_id`, `first_on_scene` (drone|ground|null), `drone_on_scene_at`, `briefed_at`, `request_id`, `requested_by`, `resource_requests[]`, `supply_requests[]`, `hospitalised_people`.
- Resources in `dock_fire` are the real PCMC units from `assets/operations/agency-catalog.json` (DisruptionOps migration 029): `id` like `PC-FIRE-01`, `name`, `agency` (operator), `station`, `real_position`, `off_map`, `approach_m`, `approach_until`. Off-map units wait at the nearest map-edge road node and pay a one-off approach delay (real distance / ground speed).
- `metrics.agency`: `requests_received`, `accepted`, `declined`, `median_time_to_first_drone_s`, `median_time_to_briefing_s`, `resource_requests_raised`, `resource_requests_fulfilled`, `supplies_delivered`, `handoffs`, `drone_first`, `ground_first`.

## The swarm as an agency (orchestrator contract)

DisruptionOps is the orchestrator; the swarm is an agency that decides for itself.

**Inbound** (integration token only): `agency_request` `{request_id (idempotent, 1–128 chars), kind: sos|assess|supply|overwatch, position [x,y(,z)], category, people, needs, priority 1–5, requested_by, sim_run_id?}`. The ack `result` is `{request_id, accepted, reason, incident_id, merged, eta_s, assigned_drones[], plan[], position, source}`; a repeated `request_id` returns the stored decision with `duplicate:true` and creates nothing. Declines: outside the operating area, inside a no-fly zone, 12 open incidents, no energy-feasible aircraft, or (non-SOS kinds) the dock launch cap. An SOS under the cap is accepted and queued.

**Outbound**: after accepting (from the report) and after the first aerial assessment (e.g. more people than seats on the way), the swarm raises `resource_request` `{request_id, incident_id, needed_kind, qty, urgency, position, rationale, briefing_revision}` as an event (`status:"resource_request"`, details in `data`) and a signal. Standalone, the local ground units answer as a mock orchestrator (`resource_assigned` events); with `external_ground` they are answered by `external_resource_update`.

**Status back**: every event with an orchestrator meaning carries `status` (and `request_id`, `data` where useful): `accepted`, `declined`, `launched`, `on_scene`, `briefing` (`data.revision`), `resource_request`, `resource_assigned`, `supply_delivered`, `ground_arrived` (`data.first`), `retasked`, `recalled`, `handed_over`, `completed`. They are journalled on `/ops/events` and sent in snapshots. `random_incident` and `detect` use exactly this path (`source:"orchestrator-sim"`).

## Agents, plans, traffic and control (additive)

- `decisions` (last 100): `{id: "dec-N", t, agent, decision, kind, reason, summary, options:[{id, score}], incident_id, drone_id, fallback, source: reasoner|fallback|swarm|rule, ...}`. `plan_response` entries add `latency_ms`, `inputs`, `factors`, `plan_source`, `applied`.
- `messages` (last 200): `{id, t, from, to, kind, incident_id, request_id, text, body}`. Kinds: `task|accept|decline|report|status|resource_request|ack|cancel|handoff`. Parties: `command_center` (DisruptionOps), `swarm` (SwarmCoordinator), drone ids. Inbound `agency_request` is a `task` from `command_center`, and the answer is `accept` or `decline`. Raised requests are `resource_request`, and orchestrator status events are `status`, `ack` or `handoff`. Plan stand-downs are `cancel`.
- Per drone: `last_decision {t, decision, reason}`. While tasked to an incident, `standoff [x,y,z]` is the hover point, 20 m from the target on the approach side.
- Per incident: `situation` (the planner's input: people, vulnerable, injured, crowd_density_per_100m2, hazards, trend, report, briefing, observations, units with `eta_s`, lifelines, drones_available, open_requests) and `plan` (the applied plan: source, rationale, resource_requests, drones, severity, escalation).
- `traffic` (≤ 80): `{id, kind: car|auto|bike|bus, position, heading (radians, atan2(dy,dx)), speed_mps}`. Traffic is visual only; responders are never blocked.
- `control`: `{autonomy: running|paused, reason, since, by}`. Command `control` `{autonomy, reason}` pauses new launches, searches and requests. Aircraft in flight finish or return, and `agency_request` is declined while paused. It is an engine command; the server decides who may send it.
- Resources carry `model` (viewer hint: fire_engine, ambulance, rescue_van, boat, jcb, pump_truck, water_tanker, supply_truck, bus, police). `scenario.data_source` is `live|assets` (reference data via `swarm_ops.disruptionops_client`).
- Categories accepted by `detect`/`random_incident`/`agency_request` also include the DisruptionOps ids person_stranded, flooded_road, waterlogging, fallen_tree, structural_damage, evacuation, supply_shortage, shelter_full and air_quality. For orchestrator reports, the unit kinds come from `swarm_ops.reasoning.plan_response`; the response is not a fixed table. A fallback traced as `fallback:true` uses DisruptionOps taxonomy needs, then the engine template.

## Scenario and world registry (engine plug-in hook)

Scenarios beyond the four built-ins are registered in Python, not over the socket:

```python
from swarm_ops.engine import register_scenario, register_world
register_world("pcmc", load_pcmc_world)          # dict, or a callable returning the world dict (lazy, cached)
register_scenario("pcmc_monsoon", "PCMC monsoon", "…", build, world_id="pcmc")
```

`build(sim)` runs on every (re)initialisation and may only use the public helpers: `sim.rng`, `sim.bounds`, `sim.buildings()`, `sim.add_incident(position, category, title=None, severity=None, people=None, needs=None, required=None, building_id=None)`, `sim.add_dock(position, name=None)`, `sim.add_resource(kind, position, name=None, id=None, agency=None, capacity=None, transport=None, **extra)`, `sim.place_on_roof(building_id_or_xy)` → `([x,y,height], building_id)`, `sim.nearest_road_point(xy)` → `[x,y,0]` or `None`, `sim.road_path(a, b)`. If the builder adds docks the fleet starts docked under the launch cap; otherwise it starts at the world's first base and searches.

Once registered, the `scenario` command accepts the new `id`. Selecting it loads the scenario's world. Built-in scenarios use the server's constructor world. A new `run_id` is issued, so the next snapshot carries the new `world`. `state.scenario.world_id` names the active world, and `swarm_ops.engine.scenario_catalog()` lists `{id, name, description, world_id}`. Worlds use the same shape as `assets/operations/pune-world.json` with generic `bounds` (regions up to about 14 km × 14 km). On large worlds the search map coarsens (≤ 48 × 48 cells; `search_map[].size_m` gives the cell size) and so does the flight planner.

## Transport limits and recovery

JSON text only; input maximum 16 KiB. Incoming queue maximum 8 messages. More than 10 commands in a rolling second closes with code 1008; oversized input closes with 1009; binary input closes with 1003. Ping interval is 20 s, ping timeout 30 s. Each client's outgoing telemetry queue is bounded to 2 frames, dropping older snapshots under pressure. That prevents an idle viewer from accumulating an unbounded telemetry backlog.

The browser reconnects with bounded exponential delay and marks old telemetry stale after 7 s. It disables exercise controls while disconnected. This is snapshot recovery, not guaranteed event delivery. To recover events:

```text
GET /ops/events?after=123&limit=100
```

Response contains `events` with increasing durable SQLite `cursor` values and `next_cursor`. Store the last processed cursor, deduplicate events using `(run_id,id)`, and request subsequent pages (maximum 500). SQLite events survive server restart; simulation state and command acknowledgment cache do not. There is currently no automatic journal retention policy.

Other endpoints: `GET /health` reports protocol/client/integration status; `GET /ops/state` provides a current full snapshot. `/`, `/web/` and `/assets/` serve only allowlisted UI/assets (`/` is `web/ops/operations.html`, `/legacy` is `web/legacy/index.html`; old `/web/operations.*` URLs are aliased); traversal outside those roots is rejected. The default bind is loopback. Cloudflare terminates public TLS and forwards the local HTTP/WS service; it does not run the simulation.

## Future DisruptionOps boundary

Use the optional connector's durable outbox and scoped import validation rather than browser controls for authoritative agency data. Imported observations need stable source IDs, incident mappings and verified coordinates. A ground handoff must match the active assignment and current briefing revision. Connecting this requires the reviewed adapter described in `disruptionops-integration.md`; the local exercise does not claim production interoperability or external dispatch.
