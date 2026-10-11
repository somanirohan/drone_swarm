# Algorithm glossary: formal names for every decision component

Purpose: describe the system with the correct literature terms, and say honestly which are really implemented. Where something is essentially a rule, it is called a rule-based / knowledge-based component. No decision here uses a language model: decisions are onboard, deterministic and offline.

Status key: **implemented** (code exists and is exercised by tests or runs), **partial** (a simplified form exists), **planned** (not in the code; do not claim it), **in progress** (another agent is building it; checked at the date below).

Checked against the working tree on 2026-10-10 (`swarm_ops/engine.py`, `planning.py`, `reasoning/params.py`, `reasoning/taxonomy.py`, `command_center.py`; `agents.py`, `traffic.py` and the rest of `reasoning/` did not exist at the first check, see "Re-check" at the end). Function names are real; search by name because line numbers drift. Details of each engine algorithm are in [ALGORITHMS.md](ALGORITHMS.md).

## Table

| Component | Formal name(s) | Where in code | Status | Why / honest note |
|---|---|---|---|---|
| Who does which task | Multi-Robot Task Allocation (MRTA), ST-SR-IA class (single-task robots, single-robot tasks, instantaneous assignment) | `engine._allocate` | implemented (centralised) | One process owns all state; drones do not negotiate. |
| Drone selection per incident | Market-based allocation; sequential single-item auction (SSI-like, one incident at a time, severity order); bid = ETA + energy share | `engine._allocate` (`offers`, `bid`) | partial | Auction is computed by the engine on behalf of drones; no messages between drones. |
| Command center to swarm exchange | Contract Net Protocol (task announce, bid or refuse, award, report) at the agency boundary | `command_center.CommandCenter.issue`, `engine._agency_request`, `ORCHESTRATOR_STATUS` events, `messages` | partial | Announce = `agency_request`; accept/decline with reason = answer; award implicit; reports = status events. No competitive bidding among several swarms. |
| Distributed consensus allocation | Consensus-Based Bundle Algorithm (CBBA, Choi/Brunet/How 2009) with asynchronous ACBBA-style rebroadcast, time-stamped information, partial replanning and lost-peer release | `swarm_ops/cbba.py` (`CBBAAgent`), `swarm_ops/agents.py` (`DroneAgent.coordinate`, `path_score`, `execute_head`), `swarm_ops/decentral.py` (scheduler + command link) | implemented (simulation only, opt-in `coordination_mode: decentralized`) | Each drone builds its own bundle (max 2) and resolves conflicts with the standard update/reset/leave table over a simulated mesh radio. Central auction stays the default. Scores are time-discounted and energy-gated, so the DMG convergence guarantee does not strictly hold; see docs/DECENTRALIZED.md. |
| Drone mesh radio | Range-limited broadcast radio, multi-hop flooding with TTL, distance-vector hop count to a gateway | `swarm_ops/mesh.py` (`Mesh`) | implemented (simulation only) | Range (600 m district / 1.5 km region), latency, packet loss; partitions emerge from geometry. No MAC/PHY model. |
| Ground unit choice (orchestrator side) | Greedy nearest-feasible assignment, kind-matched (nearest-neighbour dispatch) | `command_center._commit_unit`, `engine._allocate` (ground part) | implemented | Rule-based, distance only. |
| Ground replanning in DisruptionOps | Constraint Programming, CP-SAT assignment; greedy fallback | DO `solver/allocation.py` (reference) | implemented there, not in this repo | Swarm side has no CP-SAT. |
| Resource selection onboard | Knapsack-style greedy with marginal utility (submodular coverage of needs); optionally CP-SAT / MILP | `swarm_ops/reasoning` (optimiser) | in progress | Specified by the REASONER work; verify at re-check. |
| Situation assessment | Bayesian belief update / evidence fusion, in Gaussian form: scalar Kalman filters (inverse-variance weighting) per count and hazard, random-walk process noise, two-state (level, rate) Kalman filter for the threat trend | `swarm_ops/reasoning/belief.py` (`Gauss`, `TrendFilter`, `Belief`, `observations_from`); constants in `reasoning/params.py` | implemented (module present, pure Python, deterministic); wiring into the engine not verified | Gaussian approximation, not a particle or discrete Bayes filter. |
| Needs from report | Knowledge-based mapping from DisruptionOps taxonomy data (category to capability needs, harm rates, time constants) | `reasoning/taxonomy.py`, `assets/operations/disruptionops-taxonomy.json` | partial | A data-driven lookup (knowledge base), not learning. |
| Prioritisation of incidents | Rule-based severity ordering today; Multi-Criteria Decision Analysis (MCDA: weighted sum with expected-harm weights; TOPSIS/AHP-style weighting are alternatives) | `_allocate` sort by `(-severity, created_at)`; expected-harm weights in `reasoning/params.py` | partial | Current engine ordering is a rule. Weighted expected harm is the MCDA form being added. |
| Value-of-information reconnaissance | Value of Information (VoI) driven reconnaissance; belief variance triggers an assessing drone | variance in `reasoning/belief.py` (design intent in its docstring); launch decision not verified | partial / in progress | Variance exists; the explicit VoI computation in the planner is not verified. Today the engine sends a drone to assess every accepted incident by rule. |
| Escalation | Risk-based escalation with hysteresis (Schmitt-trigger style two thresholds) | `reasoning/` escalation | in progress | Not in `engine.py` today. |
| Replanning cadence | Receding-horizon (rolling-horizon) replanning, event-triggered | `engine._allocate` runs about once per simulated second and on events; `_maybe_reassign_nearer` | partial | Re-solves the current state; no forecast horizon. |
| Drone behaviour | Finite-state machine (states: ready, enroute, observing, returning, charging, holding, lost_link, crashed) in `step()`; `_behavior` maps state to a label | `engine.step`, `engine._behavior` | implemented as FSM; **not** a behaviour tree | `_behavior` classifies, it does not choose. Behaviour trees / utility AI planned with `agents.py`. |
| Behaviour trees, utility AI | BT (selector/sequence), utility AI | `swarm_ops/agents.py` | planned (file absent at first check) | The docstring "behaviour-tree node" in `_behavior` is a label; do not claim BT. |
| Search map | Probabilistic search map (occupancy/probability grid), multiplicative Bayes-style miss update (x0.35 per observation, relaxing to prior) | `_setup_search`, `_observe_cells`, `_raise_search_prior` | implemented (heuristic) | Prior is a heuristic, update is a fixed factor, not a calibrated sensor model. |
| Area partition | Voronoi partitioning of search cells (nearest-seed), farthest-point seeding; Lloyd-style relaxation not used | `_update_zones` | implemented (partial: Voronoi only, no Lloyd iterations) | Partition by distance, not by workload. |
| Coverage path | Boustrophedon (lawnmower) sweep of a cell; a full boustrophedon cellular decomposition is not used | `_next_search` | partial | Short two-lane sweeps per cell. |
| Flight path planning | Shortest any-angle path on a visibility graph (corners of inflated no-fly polygons), line-of-sight direct flight, coarse grid A* plus string pulling as fallback | `planning.FlightPlanner` (`horizontal`, `_visibility_path`, `_grid_path`, `string_pull`) | implemented | This is visibility-graph shortest path, not Theta* itself; same goal (any-angle). Older grid A* described in ALGORITHMS.md section 1 is the fallback. |
| Ground path planning | Dijkstra shortest path on the OSM road graph (distance cost, blocked edges removed); A* with straight-line heuristic in the cached variant | `engine._ground_path_uncached` (heap-based) | implemented | Distance only, no speed limits or turn costs. |
| Reachability | Energy-feasible reachability (round trip plus reserve within battery) | `_agency_request` check, `_allocate` (`required_Wh`) | implemented | Linear Wh per metre; constants illustrative. |
| Dock placement | Greedy k-center (farthest-point sampling) over road nodes, not MCLP or p-median | `engine._setup_docks`, `_spread` | implemented as k-center heuristic | Maximal Covering Location Problem / p-median would be a different objective: **planned**, not done. |
| Casualty triage | START triage (Simple Triage and Rapid Treatment) categories | none | planned | Needs and `critical_share_of_injured` in `reasoning/params.py` are population shares, not triage of individuals. |
| Agency control loop | MAPE-K (Monitor, Analyse, Plan, Execute over shared Knowledge) | snapshot + events (monitor), reasoning (analyse/plan), `step()` (execute), taxonomy/belief (knowledge) | partial (structure, not a formal MAPE-K component) | Useful framing for the agency; do not claim a separate K component. |
| Handoff | Incident Command System (ICS) aligned handoff / briefing with revision check (transfer of command) | `_drone_arrival_brief`, `_ack` (revision must match) | implemented (ICS-aligned in spirit) | Not a full ICS org chart; no span-of-control logic. |
| Faults | Failure handling by reallocation (crash releases incident), uncertain-delivery flag | `command("fault")` | implemented | Rule-based. |
| Command validation | Guard conditions / safety constraints (bounds, no-fly, caps, schema) | `_agency_request`, `server.apply_command` | implemented | Rule-based; the only veto layer. |
| Civilian traffic | Agent-based traffic microsimulation (not used for decisions) | `swarm_ops/traffic.py` | planned (file absent at first check) | Visual and obstacle realism only. |

## Literature

- Contract Net for multi-UAV task allocation: <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9230986/>
- Event-driven CBBA and MILP scalability: <https://arxiv.org/pdf/2509.06481>
- Decentralized MRTA for disaster response: <https://arxiv.org/pdf/1907.04394>
- Multi-criteria EMS assignment: <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10170167/>
- MCDA triage priority: <https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8100937/>
- Voronoi coverage in disaster environments: <https://arxiv.org/pdf/2403.01710>
- Multi-robot boustrophedon coverage: <https://web3.arxiv.org/pdf/1202.2261>
- Maximal covering for drone AED delivery: <https://pubsonline.informs.org/doi/10.1287/msom.2024.1027>
- p-median drone base placement: <https://ar5iv.labs.arxiv.org/html/1908.00149>

## What the system is, in one honest paragraph

A deterministic multi-robot simulation. By default allocation is a greedy sequential auction computed centrally (MRTA, market-based form); an opt-in decentralized mode runs CBBA in every drone over a simulated mesh radio (docs/DECENTRALIZED.md), and an orchestrated mode lets DisruptionOps assign drones and roles. Routing is visibility-graph any-angle planning for aircraft and Dijkstra for ground units. Search uses a probability grid, Voronoi zones and short boustrophedon sweeps. Drone behaviour is a finite-state machine. The agency boundary follows a Contract-Net-like announce, answer, report pattern with an ICS-style revisioned handoff. The onboard reasoning layer (Bayesian belief, taxonomy-driven needs, marginal-utility optimisation, hysteresis escalation) is being added and is marked in progress until its code is present.

## Re-check (end of this task)

`swarm_ops/reasoning/belief.py` appeared during the task and is reflected above. Still absent: `swarm_ops/agents.py`, `swarm_ops/traffic.py`, any optimiser or escalation module under `reasoning/`. Those rows stay planned / in progress and must be re-verified by whoever lands them (grep the file, then change the status column).

## Multi-node update

| Aspect | Now |
|---|---|
| Situation assessment (belief.py) | runs in the `SwarmNode` to produce `situation_report` (people estimate/low/high, injuries, hazards, trend, confidence); hysteresis on significant change |
| Needs, optimizer, planner (reasoning/) | run in the `OrchestratorNode`, a stand-in for DisruptionOps' planner; they no longer drive any swarm output |
| Agency control loop (MAPE-K) | per node: monitor = inbox, plan = node decision (drone bid, unit choice, allocation), execute = world port; nodes interact only through messages |
| Unit choice | each agency node picks the nearest feasible one of ITS units for a `dispatch` (exact kind first), with nearer-unit takeover |
