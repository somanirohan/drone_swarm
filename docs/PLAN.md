# Plan: from a pretty demo to a defined, organised system

Written 2026-10-10. Reference DisruptionOps commit: `bd21179` (29 migrations; the clone at `4e87ade` is stale). Sources: the user's conversation history (`resume.md`), the current code, `CAPABILITIES.md`, and the DisruptionOps migration review in `integration/disruptionops-migrations.md`.

## 0. Why this plan exists

The user's complaint, in plain words: it looks good, but the logic is thin, the folder is a mess, nobody has said what the system must deliver, and the DisruptionOps data is not used properly. This plan fixes that in order: requirements first, deliverables with tests second, then phases.

## 1. Requirements (what the user asked for)

Status key: [x] done and verified, [~] partly, [ ] open, [ip] in progress by another agent (not verified).

| # | Requirement | Source | Status |
|---|---|---|---|
| R1 | Fleet sizes 5, 10, 20, 25, 40 and 50 all work and look right | user, "50 drones", UI | [~] engine allows 1-100; only 50 has recorded runs |
| R2 | Each simulation shows, per drone: the path flown, the area covered, and SOS signals as "cloud structures" | user | [ip] |
| R3 | Choose ambulance/agency-first or drone-first handling per run | user, "agency first or drone first" | [ ] behaviour is fixed, not selectable |
| R4 | Dock scenario: drones wait in docking stations; only 2-3 launch | user | [ip] code exists, unverified |
| R5 | Zoom to the exact location of an incident or drone | user | [ip] |
| R6 | No popping drones: smooth motion, no sudden appear/disappear | user | [ip] |
| R7 | Resource allocation across drones, ambulances and agencies in parallel | user (resume.md, 12:31 PM) | [~] parallel yes, greedy only |
| R8 | A drone arriving first briefs the agency; an agency arriving first lets the drone return; supplies by whoever is nearest | user | [x] simulated, see HANDLED-CASES |
| R9 | Worst edge cases handled (crash, low battery, link loss, duplicate commands, disconnect) | user | [~] listed gaps in CAPABILITIES row 17 |
| R10 | Use DisruptionOps migrated data properly (taxonomy, agencies, capabilities, events, overrides, pause; the PCMC zone of migration 029 with 32 `PC-*` units) | user | [~] catalog generated from 029; categories, events, overrides and pause not wired |
| R11 | Organised folder | user | [ ] target in `FOLDER-LAYOUT.md`, move not done |
| R12 | Defined deliverables and capabilities | user | [~] this document and `CAPABILITIES.md` |
| R13 | Reproducible, deterministic runs and a way to replay one | engineering | [~] seed yes, replay of the full state no |
| R14 | Honest labelling: simulation-only, kinematic, not hardware | user's own earlier requests for honesty | [x] banner and docs |
| R15 | Runs on the user's hardware; shared by a Cloudflare trial URL; view-only for others | user | [~] works locally; public launch was blocked once by the tool's policy |
| R16 | Realistic Pune replica with a river and flood twist, Apple-like clean UI | user | [~] data-backed buildings and terrain; flood is prescribed |

## 2. Deliverables and acceptance criteria

Each deliverable is done only when its acceptance test passes. "Recorded run" means a JSON result saved under `docs/implementation/` by a script in `tests/`.

| ID | Deliverable | Acceptance criteria |
|---|---|---|
| D1 | Fleet-size support | A script runs every scenario at fleet 5, 10, 20, 25, 40, 50 with seeds 42 and 91. Every required service completes, no drone state is invalid, `p95` snapshot time under 100 ms at 50. Result file committed. |
| D2 | Visual evidence layer | For a selected drone the viewer draws its flown path and planned route; covered area is drawn as footprints (not just grid dots); each open SOS is drawn as a distinct structure and cleared on handoff. Browser test captures screenshots at fleet 5 and 50. |
| D3 | Dispatch policy | A run option `policy = ground_first | drone_first | parallel`. Same seed, three policies, three recorded outcomes (time to first briefing, time to first service, drones launched). Policy appears in the snapshot. |
| D4 | Dock scenario | With fleet N in docks, at most 3 are ever airborne on a dispatch; the best-placed docked drones are chosen; others never leave; returns re-dock and charge. Test asserts the cap at every step of a full run. |
| D5 | Exact-location zoom | Clicking an incident or drone, or a dispatch event in the log, moves the camera to it with a fixed target distance. Browser test verifies the camera target is within 5 m of the entity. |
| D6 | Smooth motion | Client interpolates between snapshots; server never removes a drone entity mid-run (crashed drones stay with state `crashed`). Browser test: entity count constant across 60 s at fleet 50 except explicit reset. |
| D7 | Modular engine | `swarm_ops` package with modules: world, fleet, dispatch, handoff, signals, energy, faults, scenarios, state/events. Old tests pass unchanged against the new import path. No module over 400 lines. |
| D8 | Event-sourced state | All state changes are events with `(run_id, seq)`; replaying a run's events reproduces the final snapshot bit for bit. Test compares hashes. |
| D9 | Scenario DSL | A scenario is a data file (JSON or YAML): map region, incident seeds with category and needs, faults at times, docks, fleet, policy. The four built-in scenarios are expressed in it. |
| D10 | DisruptionOps contract implemented (migration number 030; `PC-*` ids; `pccoe_pawana_night` replay with swarm sightings) | Section 3 of `integration/how-disruptionops-calls-the-swarm.md` implemented for Steps A and B; contract tests run against a stub that is verified to return what the real endpoints return (copied from the Python source, not invented). Autonomy pause test: no new launch while paused. |
| D11 | Organised repository | Target layout applied; all tests, scripts and docs links pass. |
| D12 | Real-database integration test | A separate, seeded Postgres with the DisruptionOps migrations applied; swarm report creates an incident via intake. Only then may the integration be called "tested". |

## 3. Phases

### Phase 1: UI and engine fixes (in progress, owned by BACKEND and FRONTEND agents)

- [ip] Dock scenario in the engine and viewer (R4).
- [ip] Path, coverage and SOS visuals (R2).
- [ip] Zoom to exact location (R5), smooth drones (R6).
- [ ] Fleet 5/10/20/25/40/50 run matrix (D1).
- Exit: D1, D2, D4, D5, D6 pass.

### Phase 2: Logic revamp

Goal: make the engine something that can be reasoned about and tested, not just watched.

| Step | Work | Notes |
|---|---|---|
| 2.1 | Split `swarm_ops/engine.py` (was `ops_simulation.py`) into modules: `world` (map, roads, flood), `fleet` (drones, docks, payloads), `dispatch` (candidate generation, auction, assignment), `handoff` (briefing revisions, acknowledgment), `signals` (SOS, links, relay), `energy` (budget and recharge), `faults` (crash, battery, link, reconciliation), `scenarios`, `events` | Pure move first; tests must pass before any behaviour change. |
| 2.2 | Event-sourced state: every mutation emits an event; snapshot is a projection | Matches how DisruptionOps works (append-only `events` with `causation_id`). |
| 2.3 | Deterministic seeds everywhere (one `Random` per module derived from the run seed) | Fixes R13. |
| 2.4 | Scenario DSL (D9) | Lets the user add a case without touching code. |
| 2.5 | Dispatch policy option (D3) and parallel allocation using the DisruptionOps capability/effectiveness idea: a need lists capabilities; units list capabilities with effectiveness; ETA divided by effectiveness | Same logic as `solver/allocation.py`; reuse the shape, not the code. |
| 2.6 | Fault fixes: release patrol claims on crash/link loss; link restoration with reconciliation; lease-style claims with expiry | Closes the gaps listed in `HANDLED-CASES.md` case 10. |
| 2.7 | Coverage by sensor footprint with quality and confidence | Closes CAPABILITIES row 18. |
| 2.8 | Optional: decentralised auction mode behind a flag, with a simulated lossy link model | From the research in `research/.../20-drone-decentralized-allocation.md`. Not default. |
| Exit | D7, D8, D9, D3, fault fixes, footprint coverage pass | |

### Phase 3: Integration contract with DisruptionOps

Order matters (see the migrations doc, section 5).

0. Rename the old patch migration from 029 to 030 (upstream 029 is `pcmc_pccoe_zone`); apply 029 then 030 to the demo database.
1. Seed rows only (capabilities, `drone` kind, `swarm` agency, `medical_emergency`) in a separate demo database. Nothing changes in planning.
2. Swarm reports through existing doors (intake `source='sensor'`, `road_blocks`, `mesh_nodes`, `events`).
3. DisruptionOps requests (Option A or B) and the autonomy pause is honoured.
4. Remove or disable the old PyBullet listener in DisruptionOps (`DRONE_SWARM=0`).
5. Only after an upstream change: drones as real `resources` with a flight travel mode.
- Exit: D10, D12.

### Phase 4: Fidelity (optional)

- PX4 SITL with Gazebo for 3-5 vehicles, using the same mission interface, to test the allocator against real flight dynamics. The kinematic engine remains the scale test.
- Wind, payload mass, battery ageing, landing queues.
- Map import from the user's Google Maps data if the user supplies it (legal terms to check).

## 4. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Off-map ground units: 29 of 32 `PC-*` units are 1.2-7.9 km from the map | Units appear late or teleport | Model an approach leg from the station; label it; consider widening the map or choosing a denser anchor |
| Moving files while other agents edit `ops_simulation.py`, `web/operations.*` | Lost work | Move only after Phase 1 agents finish; the lead runs the move in one commit (see `FOLDER-LAYOUT.md`). |
| The DisruptionOps baseline schema is not in the repo | Cannot reproduce a database | Dump the hosted schema into the demo repo before D12 (UNVERIFIED how). |
| Drones registered as `resources` get road-routed by the solver | Wrong ETAs and assignments | Keep drones outside `resources` until upstream supports flight (Step C). |
| Simulated runs move shared `resources` rows | Contaminates live data | Separate database only. |
| Old PyBullet listener in DisruptionOps conflicts | Confusing double ingestion | Disable it when the new connector is enabled. |
| The report claims more than it proves | Loss of trust | Keep the CAPABILITIES "must not assume" list and the `simulation_only` flag. |
| Public tunnel launch blocked by tool policy once | Cannot demo remotely | User runs `scripts\share-with-friend.ps1` themselves. |
| Performance in dense scenes (about 90 airborne over 100 ms) | Stutter | Keep the telemetry budget as a test (D1). |
| A real fleet is a safety issue | Misuse | The exercise stays simulation only; any hardware move needs its own safety review. |

## 5. Status

- [x] Research on drone swarms (`research/100-drone-disaster-response/`)
- [x] Pune viewer, WebSocket server, replay journal, 83 tests (per `validation.md`)
- [x] Capability and requirement documents (this folder)
- [x] Migration interpretation and call contract (`integration/`)
- [ip] Phase 1 fixes
- [ ] Phase 2 revamp
- [ ] Repository move
- [ ] Phase 3 integration
- [ ] Phase 4 fidelity

## 6. Definition of done

The work is done when all of this is true, and a person who did not build it can check each line:

1. Every requirement R1-R16 is marked [x] with a test name or recorded result next to it.
2. Fleet sizes 5, 10, 20, 25, 40, 50 each have a recorded run for every scenario.
3. The viewer shows path, covered area and SOS structures for the selected simulation, with no entity popping.
4. Ground-first, drone-first and parallel policies produce different, explained, recorded outcomes.
5. Replaying a run's events reproduces its final state.
6. The folder matches `FOLDER-LAYOUT.md`; one command runs the server, one command runs all tests.
7. The DisruptionOps contract is implemented for Steps A and B and verified against a real seeded database, and the pause and hold behaviours are tested.
8. `CAPABILITIES.md` matches reality: nothing marked working that has no test.
