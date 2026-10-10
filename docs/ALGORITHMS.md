# Algorithms the engine really uses

Read from `ops_simulation.py`, now `swarm_ops/engine.py` (1,707 lines at the time of writing) on 2026-10-10. Function names are real; line numbers drift, so search by name. Each entry says what it does and its honest limits. The engine is a **single-process, centralised harness**: one `OperationsSimulation` object owns all state and "drone bids" are computed by it, not by independent agents.

## 1. Routing

| Algorithm | Where | What it does | Limits |
|---|---|---|---|
| Any-angle flight planner | `swarm_ops/planning.py` `FlightPlanner.route`, `_drone_path` | Straight line when clear of no-fly polygons; otherwise a visibility graph over the convex corners of the no-fly polygons, pushed out by 12 m, searched with A* (shortest any-angle path). Coarse-grid A* plus string pulling is the fallback. Starts at the drone's current xy (no snap-back). Incident targets end at a 20 m stand-off hover point on the approach side, with a 1:3 descent to an observation altitude. Transit altitude is `max(cruise, tallest building within 15 m of this path + 12, target + 12) + lane`. LRU-cached; buildings are spatially hashed. | Before (35 m grid A*): path/straight median 1.106, max 1.565; 39/79 missions had a >120° heading reversal (16 near the target); 6/79 circled the target >180°; 50/79 started by snapping back. After: 1.000/1.001 and 0 for each. No wind, no inter-drone avoidance beyond altitude lanes, polygons only (circles become 24-gons). |
| Dijkstra for ground units | `_ground_path_uncached` (cached by `_ground_path`) | Shortest path over the OSM road graph (`_build_roads`). Start and end are connected to the nearest road segment by overlay edges (`_road_connector`). Blocked edges (flooded, damaged bridge) are skipped. | Distance cost only: no speed limits, traffic, turn costs or one-way handling (not verified). Cache key includes only the number of blocked edges, so two different closure sets of equal size could in principle return a stale route (**UNVERIFIED**, noted as a risk). |
| Boat routing | `_water_path`, `_resource_path` | Boats follow the river corridor (`_river_projection`). | Synthetic corridor, not hydrodynamics. |
| Off-map approach | `_allocate` (`approach_m`, `approach_until`) | A real PCMC unit whose station is outside the 1.2 km map pays its real remaining distance divided by ground speed (9 m/s, boat 7) as a one-off delay at the map edge. | Straight remaining distance from the catalog, not a road route. |

## 2. Dispatch and bidding

| Algorithm | Where | Detail | Limits |
|---|---|---|---|
| Severity-first greedy allocation | `_allocate` | Incidents sorted by `(-severity, created_at, id)`. For each required kind, the nearest available unit by route length (plus off-map approach) wins. Runs about once a simulated second. | Not globally optimal; no preemption of committed units except `_maybe_reassign_nearer` (a much nearer freed unit may take over an en-route one). |
| Drone bid (single-item auction) | `_allocate`, the `offers` list | For each candidate drone: plan route and recovery route; require energy feasibility; `bid = travel/18 + 20 * required_Wh / energy_Wh` (ETA seconds plus a share of remaining energy). Offers sort by `(payload mismatch flag, bid, id)`; lowest wins. Payload match is a feasibility preference before cost. | The auction is computed centrally over at most 6 nearest candidates (plus 3 stocked ones). It is a bid-comparison, not a distributed protocol. |
| Confirm-after-detection | `_allocate` with `search_to_confirm` event | A drone already patrolling can win the bid for a new incident, leaves its sweep, keeps its zone, and confirms. | Same central bid. |
| Dock launch cap | `DOCK_DISPATCH_CAP = 3`, `_launched`, `_allocate`, `_agency_request` | At most 3 aircraft airborne on a dock scenario. Only the nearest 6 eligible docked drones are even planned. SOS requests are accepted and queued under the cap; `assess`, `supply`, `overwatch` requests are declined with a reason. | A fixed constant, not derived from an air-traffic or battery model. |
| Relaunch cooldown | `RELAUNCH_COOLDOWN = 20 s` | After an assessment, another drone is sent only for a missing supply, and not within 20 s. | Prevents churn; arbitrary value. |

## 3. Energy feasibility

`required_Wh = (outbound metres + recovery metres) * 0.018 + 7 + 22` (`_allocate`), where 7 Wh is work and 22 Wh is reserve. Acceptance of a request uses `capacity_wh * 0.9 >= (...) * 0.018 + 29`. Drones return at battery below 0.23 (`step`); charging adds `delta * 0.7` Wh per second at the dock (`step`). Limits: constants are illustrative; no wind, payload mass, battery ageing, landing queue or charger count.

## 4. Search: zones, probability map, sweeps

| Algorithm | Where | Detail | Limits |
|---|---|---|---|
| Probability map | `_setup_search`, `_observe_cells`, `_raise_search_prior` | 100 m cells with a prior from water proximity (+0.35), building density (+0.3 scaled) and a base 0.15. A sensor footprint (radius = altitude * tan 30 degrees, plus 30 m reach) multiplies a cell's probability by 0.35 at most every 15 s. Incidents raise nearby priors by 0.3. | Heuristic prior, not a Bayesian filter; probability slowly relaxing back is described in the protocol doc (**not re-verified** in code). |
| Zone partition | `_update_zones` | Farthest-point sampling picks `k` seed cells (k = number of usable aircraft); each seed is assigned to the nearest free drone by its home; every cell joins its nearest seed (a Voronoi partition). Recomputed only when the set of usable aircraft changes. | Voronoi by distance, not by workload or energy. Docked fleets do not search. |
| Boustrophedon sweep | `_next_search` | The drone takes the most probable cell of its own zone (distance breaks ties), flies there by A*, then a four-point lawnmower leg set at 0.3 cell offsets. Unreachable cells are marked blocked and the zones recomputed. | Short sweeps per cell, not a full coverage-path planner. |

## 5. Handoff: ground-first vs drone-first

`_drone_arrival_brief` / `_brief` build a versioned briefing (revision increases only when content changes). `_ack` is the ground acknowledgment: it accepts only a unit that is `awaiting_handoff` or `on_scene` at that incident. If a ground crew arrives first with no briefing, `_ack` generates a ground-crew briefing. If a drone is still en route when the ground crew acknowledges, it is recalled (`drone_recall` + `recall` signal) unless it carries a payload that matches an outstanding need; then it is retasked to supply only (`drone_retask`). Limits: acknowledgment is simulated by the engine after 4 s unless `ack_handoff` is sent; there is no user-selectable policy yet; `first_on_scene` records which side arrived first.

## 6. Agency protocol (swarm as an agency)

| Piece | Where | Detail |
|---|---|---|
| `agency_request` | `_agency_request` | Idempotent by `request_id` (`_agency_requests` stores the first decision; repeats return it with `duplicate: true`). The swarm decides itself: declines outside the area, in a no-fly zone, at 12 open incidents, when no energy-feasible aircraft exists, or at the dock cap (non-SOS). Accepted requests create or merge an incident (`_incident` merges same-category points within 35 m) and reply with an explained plan. |
| `resource_request` (outbound) | `_raise_resource_request`, `_fulfil_request` | After acceptance and after the first assessment, the swarm asks the orchestrator for a ground kind and quantity. Standalone, the local ground units answer in `_allocate` as a mock orchestrator (event `resource_assigned`); with `external_ground` they are answered by `external_resource_update`. |
| `supply_request` signal and `_supply_holder` | `_supply_holder` | If the swarm has no stocked aircraft, it names the nearest holder of the needed item (a ground unit carrying first aid, per `GROUND_SUPPLIES`). |
| Status events | `ORCHESTRATOR_STATUS` | accepted, declined, launched, on_scene, briefing, resource_request, resource_assigned, supply_delivered, ground_arrived, retasked, recalled, handed_over, completed. |

Limits: not connected to DisruptionOps; the local mock orchestrator is nearest-available-wins.

## 7. Faults

`command("fault")`: `crash` deactivates the drone and releases the incident (another aircraft may win the next bid); `battery` forces the low-energy return path; `link` sets `lost_link`, keeps ownership and flags `uncertain_delivery` so no second aircraft repeats a possibly-done delivery. Limits: no link restoration, no reconciliation, no lease expiry; a failed drone's search zone is redistributed only when zones are recomputed (membership changes do trigger `_update_zones`).

## 8. Signals

`_signal` / `_prune_signals`: short-lived records (6 s window) for sos, dispatch, arrival, briefing, ack, recall, supply_request, resource_request, with `from_type`, `from_id`, `to_ids`, position and text. They are a **visualisation and audit device**: there is no radio model, range, loss or delay, so a "signal" always arrives.

## 9. Not used (do not claim these)

- No CBBA, CBAA or any distributed consensus. Agents (`swarm_ops/agents.py`: DroneAgent behaviour tree and self-evaluated bids, SwarmCoordinator, MessageBus, DecisionLog) run in one process and one deterministic loop; messages always arrive.
- Response planning goes through `SwarmCoordinator.plan_response` -> `swarm_ops.reasoning.plan_response` (offline, deterministic). The taxonomy/template fallback is traced as `fallback`. No LLM in any decision path.
- No ORCA/velocity-obstacle collision avoidance; separation is by altitude lane only.
- No PX4, ArduPilot, Gazebo, ROS or flight dynamics; motion is kinematic (`_move`).
- No camera model, detection model or SLAM; "observation" is a state change.
- No wind, weather, battery ageing, charger queue or communication-loss model.
- No global optimiser (no CP-SAT, no MILP); DisruptionOps uses CP-SAT for its own ground allocation.
- The older PyBullet exercise in `agentic_swarm.py` has its own, separate auctions; it is legacy.

## 10. Where each decision is made, one line

Route: A* or Dijkstra. Who goes: lowest bid among energy-feasible candidates. How many may fly: dock cap 3. Where to search: own Voronoi zone, highest remaining probability. When to leave: ground crew acknowledged, or battery under 0.23. What to ask for: `resource_request` on report and after first assessment. Whether to accept a task: the swarm's own checks in `_agency_request`.
