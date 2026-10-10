# Capabilities: what the system does and does not do

Written 2026-10-10 from the code in `ops_simulation.py`, `operations_server.py` (now `swarm_ops/engine.py`, `swarm_ops/server.py`), the tests, and `docs/implementation/validation.md` and `HANDLED-CASES.md`. Phase 1 fixes (UI and engine) were still being made by other agents when this was written, so lines marked *in progress* describe intent, not verified behaviour.

## 1. The honest summary

This is a **kinematic disaster-operations demonstrator** on a Pune map, not a flight-control or swarm-robotics system.

| It is | It is not |
|---|---|
| One Python process that moves points along paths, runs a severity-first allocator, and streams JSON snapshots 10 times a second | PX4, Gazebo, ROS, or any real flight software |
| Deterministic for a given seed and scenario | Decentralised: there is a central harness that reconciles every "bid" (`ops_simulation.py` header says so) |
| A 3D viewer of Pune buildings, terrain, a river and a synthetic flood corridor | A hydrodynamic flood model (the flood is a prescribed depth: `"model":"river corridor; prescribed depth, not hydrodynamics"`) |
| A safe place to test dispatch rules, handoffs and failures | Camera recognition, medical diagnosis, or a verified delivery; people counts and hazards come from the scenario state |
| Locally tested (`validation.md` counted 83 tests before the latest changes; `test_ops_simulation.py` now has 41 and `test_operations_server.py` 24 test methods) | Connected to DisruptionOps. The connection is a documented, disabled proposal. The earlier patch's migration number (029) now collides with upstream 029; use 030 |

Status words: **working** = implemented and covered by an automated test or a recorded run; **partial** = implemented with a known limit; **in progress** = being changed now; **planned** = not implemented.

## 2. Capability catalogue

| # | Capability | Status | Detail | How verified |
|---|---|---|---|---|
| 1 | Deterministic simulation (seed, scenario, run id) | working | `OperationsSimulation(seed=...)`, new `run_id` per start. Same seed gives the same scenario placement. | `test_ops_simulation.py` (27 tests); nine 50-drone replays in `acceptance-results.json` |
| 2 | Scenario: rooftop flood | working | Families on a roof in the flood corridor plus an injured person on dry road. | tests + replays |
| 3 | Scenario: medical supply | working | Urgent first-aid delivery plus residents needing food and transport. | tests + replays |
| 4 | Scenario: blocked bridge | working | Damaged crossing removed from the ground graph; needs both engineering and rescue. | tests + replays |
| 5 | Scenario: dock fire (drones wait in docks, only 2-3 launch) | working | `dock_fire`, `DOCK_DISPATCH_CAP = 3`, `_setup_docks`; real PCMC units; test `test_init_is_fast_cap_holds_and_everything_resolves_and_docks`. | `test_ops_simulation.py` |
| 6 | Random scenario button | working | `random_scenario` picks a different scenario and a new seed. | server tests |
| 7 | Fleet size | working (command), partial (evidence) | `fleet` command (1-100) re-initialises the scenario; UI offers 5/10/20/25/40/50. Recorded full-scenario runs exist for 50 only; a 5-drone dock_fire run was exercised manually on 2026-10-10. | `test_fleet_command_reinitialises_and_validates`, `test_fleet_detect_and_random_incident_commands`; run matrix D1 still open |
| 8 | Launch from docks with a cap | working | At most 3 airborne; nearest 6 eligible docked drones are planned; SOS requests queue under the cap, other kinds are declined with a reason. | tests above |
| 9 | Aerial field assessment | working (simulated) | Drone reaches an observation point, state `observing`, builds a briefing: people, hazards, needs, supplies, source drone, time, confidence, revision. Refreshes about every 4 simulated seconds. | tests |
| 10 | Aerial briefing handed to agencies | working (simulated) | Shared, versioned incident briefing. Ground acknowledgment must match the current revision. | tests; `HANDLED-CASES.md` cases 3-5 |
| 11 | Ground-first vs drone-first handling | partial | Both orders work: drone first gives a briefing; ground first gets a fallback ground briefing and recalls the drone after acknowledgment. **There is no selectable policy**; behaviour is fixed. | tests |
| 12 | Parallel dispatch of drone and ground units | partial | One pass assigns an aircraft and a ground unit independently. Severity-first greedy, one drone per incident, no preemption of committed units, no global optimisation. | tests; known limit in `HANDLED-CASES.md` case 1 |
| 13 | Ground routing around flooded and damaged roads | working (synthetic) | Road graph from OSM; flooded edges forbidden to vehicles; boats use the river corridor. | tests |
| 14 | Evacuation trips with capacity | partial | Load up to capacity, travel to hospital/shelter/shore point, count people only on arrival. No treatment, no shelter occupancy. | tests |
| 15 | Supply drop | working (simulated) | Drops only a payload that matches an outstanding need and has stock; stock decrements; reload at base. Incident stays open. | tests |
| 16 | Energy feasibility and low-battery return | partial | `required Wh = (out+back metres) x 0.018 + 7 + 22`. Illustrative constants; no wind, payload mass or battery ageing. | tests |
| 17 | Fault handling: crash, battery, link loss | partial | Crash releases the task; link loss keeps ownership and sets `uncertain_delivery`; battery triggers return. **Known gaps:** a crashed or lost drone's patrol patch can stay claimed; link restoration/reconciliation is missing. | tests for incident replacement only |
| 18 | Coverage scanning | partial | 100 m probability map with a footprint model (radius = altitude x tan 30 degrees), per-drone Voronoi zones, boustrophedon sweeps, `status` unsearched/searched/cleared/blocked. Not a camera model. | `test_search_zones_partition_cells_and_observation_lowers_probability` |
| 19 | Highlight of path, covered area and SOS signals | in progress | Requested by the user. Path is a drone `route` already in the snapshot; area and SOS "cloud" visuals are Phase 1 UI work. | **unverified** |
| 20 | Exact-location zoom to an incident | in progress | Phase 1 UI work. | **unverified** |
| 21 | No popping drones (smooth appear/disappear and movement) | in progress | Phase 1 UI work; snapshots are coalesced to 2 frames, so the client must interpolate. | **unverified** |
| 22 | Signal relay between drones | planned | Signals (sos, dispatch, arrival, briefing, ack, recall, supply_request, resource_request) exist as an event/visual layer with no range, loss or delay. No relay behaviour. | `test_drone_signal_search_and_people_fields` (fields only) |
| 23 | Decentralised allocation (auctions, no coordinator) | planned | Research in `docs/research/100-drone-disaster-response/`; engine is centralised. | n/a |
| 24 | WebSocket + HTTP API | working | See `docs/implementation/websocket-protocol.md`. Bounded queues, rate limit, origin check, command id dedup, event replay. | 22 server tests; 20 client acceptance checks |
| 25 | Operator/viewer separation and public tunnel | working | Operator token only to loopback; tunnel viewers read-only. Public launch was **not** achieved (policy block recorded in `validation.md`). | tests |
| 26 | DisruptionOps integration | partial (inside the simulator), planned (the live link) | The swarm implements the agency side: `agency_request` in, `resource_request` and status events out, `external_resource_update` in. No live call to DisruptionOps has been made. The old patch's migration number collides with upstream 029 (use 030). | `test_accept_decline_and_idempotent_request_id`, `test_chain_accept_assess_request_resources_handover_complete`, `test_agency_request_requires_integration_token` |
| 27 | Resource allocation using DisruptionOps data | working (catalog), partial (rest) | Ground units are the 32 real `PC-*` units of DisruptionOps migration 029 (`bd21179`) with station coordinates; 29 are off-map and pay an approach delay. Capability/effectiveness ranking from the migrated tables and the live DB are not used. | `test_units_are_real_pcmc_catalog_units` |
| 28 | Legacy PyBullet 12-drone exercise | working, separate | Crazyflie CF2X, DSL-PID, rule or AI agents, optional OpenAI/Ollama. Kept for reference. | 15 regression tests |
| 29 | Random and clicked incidents (`detect`, `random_incident`) | working | Snapped to a roof or reachable road point, reproducible from seed, up to 12 open incidents, go through the agency path. | `test_random_incident_is_reproducible_and_runs_the_full_chain`, `test_detect_snaps_and_returns_incident_id_with_open_cap` |
| 30 | Agency protocol: accept/decline with reason, idempotent `request_id`, status events | working (simulated) | See `ALGORITHMS.md` section 6. | `test_accept_decline_and_idempotent_request_id` |
| 31 | Swarm raises `resource_request` and local units answer as a mock orchestrator | working (simulated) | Fire engine, ambulance or rescue requested on report and again after first assessment. | `test_chain_accept_assess_request_resources_handover_complete` |
| 32 | Dispatch of an EMS/fire/rescue unit visible to clients | working | event `ground_dispatch`, resource `enroute` with route and ETA, signals. How to read it: friend handoff, section A.4. | real run captured 2026-10-10 |

## 2b. Framing: the swarm is an agency, DisruptionOps is the orchestrator

DisruptionOps owns incidents, ground units, the emergency stop and the audit log. The swarm is an agency: it receives requests (`agency_request`), decides itself whether to accept, flies its own search and assessment, and asks the orchestrator for ground units (`resource_request`). It never commands ambulances, and a drone arriving never resolves an incident. Algorithms and their limits: `ALGORITHMS.md`. Call contract: `integration/how-disruptionops-calls-the-swarm.md`.

## 3. What a user can rely on today

- Press a scenario or random button and watch drones and ground units respond to incidents on the Pune map.
- Pause, change speed, inject a drone fault, and read the event log, which is replayable by cursor.
- Connect any client over WebSocket and receive the same state the viewer shows.

## 4. What a user must not assume

- That a drone "saw" anything: observation is a state transition, not image analysis.
- That the allocation is optimal, fair, or safe for real use.
- That a supply drop equals a delivery to a person.
- That DisruptionOps knows about any of this. Today it does not.
- That the fleet sizes other than 50, or the dock scenario, have been exercised end to end.

## 5. Where the user's expectations are not met yet

See `PLAN.md` section 1 for the numbered requirements and which ones are open. The largest gaps: logic is one 800-line module; no selectable ground-first/drone-first policy; no footprint-based coverage; no relay; no DisruptionOps data; no recorded runs for fleet sizes below 50.
