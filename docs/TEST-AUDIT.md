# Test audit

Audit of the cases the repository currently tests, and the gaps for a DisruptionOps-controlled swarm. Date of the full run: 2026-10-11, `python -m unittest discover -s tests -p "test_*.py"` with `.conda-drones\python.exe`: **183 tests, 182 pass, 1 fails** (`test_taxonomy.AssetTests.test_counts_and_source`: asserts `migration_count == 38`, the checked-in asset says 36; a data/test mismatch owned by the taxonomy work, not a swarm behaviour failure). Not part of that run: `tests/operations_acceptance.py`, `operations_clients_acceptance.py` and `operations-browser.cjs` (need a live server / Playwright; last results are in `docs/implementation/*results.json`, not re-run here). The `swarm_ops/reasoning/evals` suite (15 cases plus pairs) runs inside `test_reasoning.py::Behaviour::test_eval_suite_passes`.

Status key: pass / fail / not covered / partial (covered for the autonomous swarm only, not for the orchestrated case).

## 1. What is covered today

### 1.1 Engine behaviour (`tests/test_ops_simulation.py`, class `OperationsTests` unless noted)

| Case | What it proves | Test | Status |
|---|---|---|---|
| 20 drones, parallel dispatch | Snapshot schema; several incidents served at once | `test_twenty_drone_schema_and_parallel_dispatch` | pass |
| Delivery is not resolution; ack needs arrival | A drop does not close an incident; handoff only from a unit on site | `test_delivery_does_not_resolve_and_ack_requires_arrival` | pass |
| Ground crew arrives first | Own briefing, then drone recalled | `test_ground_first_produces_own_briefing_then_recalls` | pass |
| Ground first, drone still carries needed supply | Drone retasked to drop, then returns | `test_ground_first_retasks_drone_that_still_carries_a_needed_supply` | pass |
| Drone crash | Incident kept, replaced by another aircraft | `test_crashed_owner_is_replaced_without_losing_incident`, `test_repeated_fault_cannot_reanimate_crashed_aircraft` | pass |
| Link loss | No blind duplicate of an exclusive task; confirmed crash allows replacement | `test_link_loss_does_not_blindly_duplicate_exclusive_task`, `test_confirmed_crash_after_link_loss_allows_replacement` | pass |
| Low battery | Return and reassignment | `test_low_energy_returns_and_reassigns` | pass |
| Free ground unit dispatched while drone works | Nearest free unit takes the job | `test_free_ground_unit_dispatches_while_drone_works` | pass (reassign-to-nearer path itself not isolated) |
| No road, no ground teleport; no-fly rules | Ground units need roads; no-fly excludes aircraft only | `test_no_roads_means_no_ground_teleportation`, `test_no_fly_excludes_target_and_does_not_block_ground` | pass |
| Duplicate detection merges | Same-category repeat within 35 m merges and escalates | `test_repeated_detection_merges_and_escalates` | pass |
| Non-finite / invalid inputs | pause, speed, reset, scenario rejected without state change | `test_pause_speed_and_reset_reject_nonfinite`, `test_invalid_scenario_cannot_change_seed_or_run` | pass |
| Outside boundary | No aircraft sent outside the mission area | `test_outside_boundary_detection_does_not_send_aircraft_outside_mission` | pass |
| External authority | Handoff needs a fresh briefing revision; wrong run rejected | `test_external_authority_requires_explicit_fresh_ack`, `test_wrong_run_external_update_is_rejected` | pass |
| Determinism | Same seed, same motion and decisions | `test_seeded_motion_and_decisions_repeat` | pass |
| Search coverage | Unique patches, zones partition cells | `test_patrol_claims_unique_patches_and_accumulates_coverage`, `SnapshotContractTests::test_search_zones_partition_cells_and_observation_lowers_probability` | pass |
| Flood handling | Water rescue, unsafe roads closed | `test_flood_uses_water_rescue_and_closes_unsafe_roads` | pass |
| Payload logistics | Matching payload consumed and reloaded | `test_drone_payload_is_matching_and_consumed_then_reloaded` | pass |
| Boat capacity | Actual shore trips | `test_boat_capacity_requires_actual_safe_shore_trips` | pass |
| Multi-agency incident | Waits for every required service | `test_multi_agency_incident_waits_for_every_required_service` | pass |
| Dock fire | Launch cap holds, all resolve, drones return to docks | `DockFireTests::test_init_is_fast_cap_holds_and_everything_resolves_and_docks` | pass |
| Ground-first vs drone-first ordering | Seeds 42 and 91 | `DockFireTests::test_ground_first_ordering_seed_42`, `test_drone_first_ordering_seed_91_briefs_every_dispatched_agency` | pass |
| Real PCMC units | Catalog units used | `DockFireTests::test_units_are_real_pcmc_catalog_units` | pass |
| Random incident | Reproducible, full chain | `DockFireTests::test_random_incident_is_reproducible_and_runs_the_full_chain` | pass |
| People placement | Believable positions | `DockFireTests::test_people_placement_is_believable` | pass |
| Snapshot contract | Drone fields, fleet command, detect snaps and returns incident id | `SnapshotContractTests::*` | pass |
| Agency protocol | Accept, decline, idempotent `request_id` | `AgencyRequestTests::test_accept_decline_and_idempotent_request_id` | pass |
| Agency chain | Accept, assess, request resources, hand over, complete | `AgencyRequestTests::test_chain_accept_assess_request_resources_handover_complete` | pass |
| Random issue via agency path | Same path as orchestrator | `AgencyRequestTests::test_random_issue_uses_agency_path` | pass |

### 1.2 Transport and security (`tests/test_operations_server.py::TransportTests`, `JournalTests`)

| Case | What it proves | Test | Status |
|---|---|---|---|
| Journal | Restart-safe dedup, ordered replay | `JournalTests::test_restart_dedup_and_ordered_replay`, `test_event_http_replay_is_ordered` | pass |
| First frame has world | World then bounded frames; reset frame includes world; slow client keeps world | `test_initial_world_then_bounded_frame`, `test_reset_frame_includes_world`, `test_slow_client_retains_world_until_reset_frame_delivered` | pass |
| View-only client | Cannot mutate | `test_viewer_cannot_mutate`, `test_unicode_token_is_read_only` | pass |
| Wrong token | Cannot mutate | `test_wrong_token_cannot_mutate` | pass |
| Operator controls | Pause/resume round trip | `test_pause_resume_round_trip` | pass |
| Idempotent command id | Duplicate reset across reconnect; same id different payload rejected | `test_duplicate_reset_is_idempotent_across_reconnect`, `test_command_id_payload_collision_rejected` | pass |
| Stale run | Command for an old run rejected | `test_stale_run_command_rejected` | pass |
| Malformed input | Malformed, unknown action, invalid speed, non-finite/overflow | `test_malformed_commands_and_unknown_action_rejected`, `test_invalid_speed_rejected_without_state_change`, `test_nonfinite_and_overflow_commands_rejected_without_disconnect` | pass |
| Rate limit, size limit | Disconnect on more than 10 commands/s or oversize message | `test_command_rate_limit_disconnects`, `test_oversize_message_disconnects` | pass |
| Origin, path, session | Cross-origin WS rejected; traversal rejected; token not leaked via forwarded requests | `test_cross_origin_ws_rejected`, `test_http_path_traversal_rejected`, `test_http_session_does_not_leak_token_to_forwarded_requests` | pass |
| Integration vs operator | `agency_request` requires the integration token | `test_agency_request_requires_integration_token` | pass |
| Two viewers | Same run, monotonic sequence | `test_two_viewers_receive_same_run_and_monotonic_sequence` | pass |
| Premature handoff | Rejected | `test_premature_handoff_rejected` | pass |
| Telemetry | Recorder validity, rotation, no secrets in logs, runs endpoints need auth | `tests/test_telemetry.py` (6 tests) | pass |

### 1.3 Command center (`tests/test_command_center.py`) and agency bridge

| Case | What it proves | Test | Status |
|---|---|---|---|
| Orchestrator run | Tasks accepted, resource requests answered, one completed, timeline written | `CommandCenterTests::test_orchestrator_runs_tasks_answers_requests_and_completes` | pass |
| Idempotent `request_id` over WS | Same id gives `duplicate`, one incident | `test_request_id_is_idempotent` | pass |
| Standalone mode | Local mock answers; no external ground | `test_standalone_mode_commits_nothing_and_local_mock_answers` | pass |
| Wrong token | Nothing accepted | `test_no_token_cannot_issue_requests` | pass |
| Messages endpoint | Paged JSON | `test_messages_endpoint_is_paged_json` | pass |
| Situation + catalog | Rich situation from DO data; nearest catalog unit, no double commit | `SituationAndCatalogTests` (2) | pass |
| Real adapter mapping | Dry run, nothing sent | `RealAdapterTests::test_dry_run_maps_without_sending` | pass |
| DisruptionOps-side bridge | Request sent and recorded; stop/officer hold; wrong token; orchestrated mode sends incidents only; resource request honours stop | `tests/test_swarm_agency_bridge.py` (8) | pass |
| Outbound bridge, client, contract | Outbox, retries, ids, geo, auth, taxonomy visibility | `test_disruptionops_bridge.py` (13), `test_disruptionops_client.py` (9), `test_disruptionops_contract.py` (6) | pass |

### 1.4 Agents, reasoning, region, planning

| Case | What it proves | Test | Status |
|---|---|---|---|
| Decisions and messages in snapshot | Per-drone last decision; command-centre traffic on the bus | `test_agents.py::AgentSnapshotTests` | pass |
| Reasoner plan replaces required kinds; no double request; fallback traced | Plan wiring, idempotent deltas, labelled fallback | `PlanResponseTests` (5) | pass |
| Control pause | Pause blocks new launches and requests | `ControlAndTrafficTests::test_control_pause_blocks_new_launches_and_requests` | pass |
| Traffic | Bounded, deterministic, avoids closures | `test_traffic_is_bounded_deterministic_avoids_closures_and_stops_near_incidents` | pass |
| Reasoning engine | Schema, guardrails (repair, boat on dry street, unsafe), 15 eval cases + pairs, determinism, under 50 ms, hysteresis, scarce unit to worse incident, greedy close to CP-SAT, explanations, bad input returns none, no LLM or network code, built-in taxonomy fallback | `test_reasoning.py` (17) | pass |
| Region world | 32 units at catalog positions, wards tile envelope, river, flood zones, lifelines, roads; Pawana scenario mirrors DO reports, sites in named wards, needs from taxonomy, docks real | `test_pcmc_region.py` (10) | pass |
| Planner | Direct vs round no-fly, altitude from path, no route from no-fly, region-scale speed and cache, direct rescue flights | `test_planning.py` (6 planner + 3 registry) | pass |
| Taxonomy | Replay, row width, unicode, later migrations, references, asset matches migrations | `test_taxonomy.py` | 6 pass, **1 fail** (`test_counts_and_source`: 38 vs 36) |

## 2. Gap list: a DisruptionOps-controlled swarm

"Orchestrated" means DisruptionOps (or the command center standing in for it) assigns the incident, drones and ground units itself with `orchestrate_incident`, and the swarm obeys instead of choosing by itself. Existing tests cover the autonomous path only. The matrix in `tests/test_command_center.py::OrchestratedMatrix` targets each row; its result column is filled in section 3.

| # | Case that must be covered | Today | Matrix test |
|---|---|---|---|
| G1 | Orchestrated incident with a DO-assigned drone and ambulance | not covered | `test_g01_*` |
| G2 | DO assigns an ambulance only: the swarm must not self-launch a drone | not covered | `test_g02_*` |
| G3 | DO adds an assignment later (`assign`) | not covered | `test_g03_*` |
| G4 | DO recalls a drone (`recall_drone`) | not covered | `test_g04_*` |
| G5 | DO cancels or closes an incident | not covered | `test_g05_*` |
| G6 | DO updates people or severity mid-mission (`update_incident`) | not covered | `test_g06_*` |
| G7 | Autonomous and orchestrated incidents concurrently | not covered | `test_g07_*` |
| G8 | Ambulance arrives first / drone arrives first, under orchestration | partial (autonomous only) | `test_g08_*` |
| G9 | Unit freed and a nearer unit reassigned | partial (`_maybe_reassign_nearer`, not isolated) | `test_g09_*` |
| G10 | Duplicate `request_id` | pass (autonomous, and `orchestrate_incident` idempotency not covered) | `test_g10_*` |
| G11 | Request outside bounds: `decline_jurisdiction` | partial (decline covered, `reply_kind` not) | `test_g11_*` |
| G12 | Missing position: `need_info` | not covered | `test_g12_*` |
| G13 | Capacity reached: `decline_capacity` | partial (cap declines covered, `reply_kind` not) | `test_g13_*` |
| G14 | Partial accept (`accept_partial`) | not covered | `test_g14_*` |
| G15 | Autonomy paused and resumed via `control` | partial (`test_control_pause_blocks...` in-engine, not over the integration channel) | `test_g15_*` |
| G16 | Drone crash / link loss / low battery in the middle of an orchestrated mission | partial (autonomous only) | `test_g16_*` |
| G17 | WebSocket reconnect mid-mission: state and events replay | partial (idempotent reset and journal replay covered; no mid-mission resume) | `test_g17_*` |
| G18 | View-only client cannot command | pass (`test_viewer_cannot_mutate`) | `test_g18_*` |
| G19 | Integration vs operator permissions for the new commands | partial (only `agency_request`) | `test_g19_*` |
| G20 | Malformed payloads for the new commands | partial (generic malformed only) | `test_g20_*` |
| G21 | Rate limit with an integration session | partial (operator session tested) | `test_g21_*` |

Not in the matrix, still open: browser UI checks (`operations-browser.cjs`) were not re-run; no test of the `RealModeAdapter` against a live DisruptionOps; the unrelated taxonomy failure above.

## 3. Matrix results

Run 2026-10-11 against ENGINE commit 3182216 (orchestrated mode), `tests/test_command_center.py::OrchestratedMatrix`, real server on port 0, integration token over WebSocket, engine at 16x. `tests.test_command_center`: 30 tests, all pass (about 150 s, of which the scripted runs are most).

| # | Result | Test and how it was tested; caveats |
|---|---|---|
| G1 | pass | `test_g01_*`: drone (count 1) + external ambulance; reply `accept_full`, `control_mode` orchestrated, ambulance dispatched, drone tasked, handoff reached. |
| G2 | pass | `test_g02_*`: ambulance only; after ~40 simulated seconds no drone tasked, `assigned_drones` empty, no `resource_requests`. |
| G3 | pass | `test_g03_*`: `assign` after creation dispatches the late unit. |
| G4 | pass | `test_g04_*`: `recall_drone` releases the drone, which goes to `returning`; removed from `assigned_drones`. |
| G5 | pass | `test_g05_*`: `cancel_incident` gives outcome `cancelled`, `close_incident` gives `completed`; drone released both times. |
| G6 | pass | `test_g06_*`: people, severity and situation updated. |
| G7 | pass | `test_g07_*`: autonomous and orchestrated incidents side by side; the swarm added no drones or resource requests to the orchestrated one. |
| G8 | pass (with caveat) | `test_g08_*`: ground-first and drone-first both observed, but with external units placed near/far on purpose rather than catalog units on real roads. |
| G9 | pass (reinterpreted) | `test_g09_*`: `unassign` frees the far unit (available) and a later `assign` sends the nearer one. This is the orchestrated equivalent; the autonomous `_maybe_reassign_nearer` is still not isolated by a test. |
| G10 | pass | `test_g10_*`: same `request_id` on two command ids returns `duplicate`, one incident. |
| G11 | pass | `test_g11_*`: `decline_jurisdiction` for both `orchestrate_incident` and `agency_request`. |
| G12 | pass | `test_g12_*`: `need_info`, no incident created. Only for `orchestrate_incident`; `agency_request` without a position is a command error, not `need_info`. |
| G13 | pass | `test_g13_*`: after 12 open incidents the next is `decline_capacity`. |
| G14 | pass (dock scenario) | `test_g14_*`: needs a docked fleet for a launch cap, so it runs on `dock_fire`; 6 requested, fewer launched, `accept_partial`. Without docks all 6 launch. |
| G15 | pass | `test_g15_*`: `control paused` makes `orchestrate_incident` `decline_capacity` and declines `agency_request`; `running` accepts again. |
| G16 | pass (weak assertion) | `test_g16_*`: crash, link loss and battery on the assigned drone: incident stays open and no replacement is self-assigned. It does not assert what DisruptionOps should be told or that the unit is re-offered. |
| G17 | pass | `test_g17_*`: a new session sees the orchestrated incident in its first snapshot, a repeated `request_id` is a duplicate, and `/ops/events` replays its events. |
| G18 | pass | `test_g18_*`: a view-only client cannot orchestrate, control or cancel. |
| G19 | pass | `test_g19_*`: operator token rejected for integration commands, integration token rejected for `reset`; operator `pause` still works. |
| G20 | pass | `test_g20_*`: malformed positions, empty ids, unknown incident or drone, bad autonomy, bad update values are rejected and the session stays usable. |
| G21 | pass | `test_g21_*`: 14 commands in a burst on an integration session close the socket. |
| extra | pass | `test_scripted_orchestrated_run_completes`: `CommandCenter.run()` in `orchestrated` mode, tasks accepted, zero swarm resource requests, at least one completed. |

Still not covered: reconnect of a session during a long mission with in-flight commands (only state and replay are checked), concurrent orchestration from two integration clients, `delayed` reply (no test forces zero launches), DisruptionOps-side behaviour (the real adapter was not run against a live backend), browser UI.
