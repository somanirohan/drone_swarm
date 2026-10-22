# How twenty drones divide disaster-search work without a single coordinator

Research and code inspection: 10 October 2026. Three parallel research agents checked allocation papers, public implementations and this repository. This is a design recommendation for the simulator; application and flight code have not been changed.

## Recommendation

Run **task generation, a local task ledger, an energy feasibility check and a peer-auction allocator on every drone**. Give the fleet one mission definition, rather than twenty personal regions. Keep flight planning and stabilization independent. Treat the dashboard as a subscriber to mission events, not the machine that must remain alive for every assignment.

Use a published CBAA/CBBA-family algorithm as the allocation baseline. Start with one near-term task per aircraft to make correctness easier to inspect. Move to short ordered bundles and the published asynchronous deconfliction rules when route efficiency and a real peer transport justify them. Do not describe the existing claim implementation as full CBBA.

For disconnected operation, choose the policy deliberately: continue previously agreed work; permit measured duplicate observation where appropriate; defer new exclusive actions whose ownership cannot be established. Decentralization removes one decision-making failure point, but it does not make communication failures disappear.

The mechanisms below are an engineering synthesis. Research guarantees apply only under each paper's stated scoring, topology, timing and fault assumptions.

## A concrete flood example

Assume a hypothetical 2 km by 2 km flood-search boundary near Morigaon, Assam, and twenty aircraft. This is illustrative geometry, not an actual deployment or an assertion that these aircraft can cover it in a particular time.

All aircraft receive and retain the same mission package:

```text
mission_id, mission_revision, permitted_polygon, shared_coordinate_frame
restrictions, search_objective, observation_quality_requirements
task_generator_version, authorized_aircraft_ids, recovery_sites
```

This can be loaded before takeoff. A ground device can disappear afterward without erasing these copies. Mission changes still need an authenticated, versioned update policy; two different mission revisions must not silently compete.

Each aircraft independently divides the boundary into, for example, one hundred 200 m by 200 m patches and generates camera sweep strips within each patch. Actual strip spacing must come from the camera footprint, altitude, overlap, terrain and required detection resolution. Obstacle-clipped fragments need stable identities as well.

Task identity is deterministic, such as `(mission_revision, tile_x, tile_y, strip_index, task_revision)`. The same work should have the same ID on different aircraft. Approximate victim detections and independently mapped frontiers need spatial association; rounding coordinates alone does not solve noisy-map deduplication.

No drone has been told to take the left or right side. It calculates what work it can do well and negotiates through messages.

### One patch, three eligible candidates

Illustrative values for a single search task T37:

| Aircraft | Travel + search time | Usable energy | Estimated energy including recovery/reserve | Decision |
|---|---:|---:|---:|---|
| D04 | 6 minutes | 80 Wh | 65 Wh | Eligible; cost 6 |
| D11 | 4 minutes | 45 Wh | 55 Wh | Ineligible; no bid |
| D18 | 8 minutes | 95 Wh | 70 Wh | Eligible; cost 8 |

For this one-task illustration, lower feasible time cost wins. D11 is physically closest but cannot bid because the recovery energy requirement fails. D04's claim beats D18's. For a complete multi-task auction, bids must include the effect of already selected work; choosing the nearest drone independently for every patch could give all patches to one aircraft.

D04 shares `(T37, revision, bid, bidder_id, freshness)` with its neighbors. Neighbors forward the relevant winner information. If D18 had initially believed itself the winner, it changes that belief after receiving D04's valid better bid and looks for other work. Equal bids use the same deterministic ID rule on every node.

The fact that every aircraft runs the same comparison is what replaces a single dispatcher. The example is explanatory, not a complete asynchronous protocol: real CBBA includes task release/reset rules and information-freshness logic, not just a maximum/minimum operation.

### Why twenty drones do not all chase the same patch

Each aircraft maintains its own current task/winner beliefs. It bids for feasible, useful work subject to its own capacity. Losing aircraft release contested work and consider other tasks. In CBBA, an ordered bundle captures the dependence of later tasks on earlier ones; losing an earlier entry requires releasing dependent later entries before rebuilding. [Original CBAA/CBBA paper](https://dspace.mit.edu/entities/publication/b0bf0a05-be3b-433b-9f4b-ce314ed5178b), [partial replanning paper](https://arxiv.org/html/1806.04836).

The resulting division need not be twenty equal areas. Aircraft with better endurance can accept more work, a thermal-equipped aircraft may be uniquely eligible for some work, and a suspected survivor location may justify several distinct verification tasks. Minimum coverage and revisit requirements prevent other patches being neglected.

### What is shared over the radio

Maintain separate message families, with bounded sizes and priorities:

| Message | Useful fields | Purpose |
|---|---|---|
| Peer health | aircraft ID, boot/session ID, sequence, state, energy, measurement age | Assess known capability and freshness; absence means uncertainty |
| Task definition | mission/task revision, task ID, geometry, objective, quality requirements | Give peers the same identifiable work |
| Allocation delta | task ID/revision, bid, believed winner, protocol freshness/reset metadata | Resolve competing tentative assignments |
| Execution event | assignment revision, started/released/partial/completed, evidence ID | Preserve work progress and reject old state changes |
| Motion intent | coordinate frame, time validity, position uncertainty, short trajectory | Support independent local collision planning |
| Observation delta | observed footprint, observation time, confidence, evidence reference | Maintain actual coverage and revisit state |

Drones need communication paths through the peer graph, not a permanent direct link to all nineteen others. A three-hop path needs successive forwarding opportunities; allocation latency depends on topology, delay, congestion and the selected protocol. Research bounds in communication rounds must not be presented as fixed seconds. ACBBA specifically addresses asynchronous messages and replanning; simply attaching a timestamp to a bid is insufficient. [ACBBA paper](https://dspace.mit.edu/entities/publication/8027006a-fddf-44a7-b5b9-3475099d7e6c).

Implement sender sequence/session checks and protocol freshness consistently. Receipt time is not generation time. A reboot requires a new incarnation/session identity so an old packet cannot appear to belong to the new process. Avoid relying on perfectly synchronized wall clocks; explicitly model clock uncertainty if expiry is part of the protocol.

## If the area itself is unknown

An unassigned area and an unknown map are different problems.

For a known boundary, generate sweep tasks locally from the common geometry. For an unknown interior, each drone senses and builds a local map. The edge between explored navigable space and unknown space produces **frontier tasks**. Aircraft exchange map/task deltas, reconcile equivalent frontiers and allocate candidate observations. An observation changes the map and generates the next frontiers.

Thus the task loop is `sense -> discover candidate work -> share -> allocate -> move -> sense again`. RACER is a particularly relevant decentralized exploration reference; it combines workload allocation with exploration and trajectory planning. It is not proof of twenty disaster aircraft operating under arbitrary partitions. [RACER paper](https://arxiv.org/abs/2209.08533), [author repository](https://github.com/Robotics-STAR-Lab/RACER).

Short-range connectivity can also be maintained by cohesion or allocated relay work, at the cost of some exploration capacity. [Frontier-led Swarming](https://arxiv.org/abs/2111.14295), [MIT CBBA relay variant](https://acl.mit.edu/projects/consensus-based-bundle-algorithm).

Even unknown-map exploration needs an objective, permitted operating boundary or exploration limits, localization and recovery rules. An algorithm cannot infer the intended mission from the absence of any instructions.

## Alternative: let geometry define responsibility

In Voronoi coverage, each point belongs to the nearest drone, with a common tie rule. Every drone can compute its cell from its position and sufficient neighboring position information. Risk-weighted density can bias where drones spread. This provides an understandable answer to how territory can emerge without manually labeling left/right sectors. [Cortés et al., coverage control](https://arxiv.org/abs/math/0212212).

However, a nearest-drone region does not prove that its camera scanned the region, does not account automatically for feasible recovery, and can change as drones move. Different membership views during a radio partition can produce inconsistent cells. Flood regions with obstacles and uneven work also need more than equal geometric areas.

Use Voronoi as an optional dispersion/local-neighborhood aid or comparison baseline. For this project, search-strip auctions plus an observation ledger are the more useful mission core. Do not assume arbitrary battery-weighted Voronoi cells inherit a published theorem.

## The failure case that must be designed explicitly

Suppose the twenty aircraft split into a group of twelve and a group of eight. D04 is in the first group, and D18 is in the second. Both groups can have a plausible local explanation for why T37 is unfinished. Neither can determine from silence whether the other group is still executing it.

A better tie-break cannot solve missing information. A missed heartbeat is not proof of death. General distributed-system consistency/availability results explain why a system that coordinates exclusive mutable ownership cannot both accept every new assignment across arbitrary partitions and always maintain one global owner. This is an application of the result to assignment state, not a claim that every aspect of swarm motion requires strong consensus. [Gilbert and Lynch, perspectives on CAP](https://groups.csail.mit.edu/tds/papers/Gilbert/Brewer2.pdf).

Recommended policies:

| Work type | While disconnected | After reconnection |
|---|---|---|
| Previously agreed search strips | Continue if flight/energy conditions permit; avoid stealing another component's known active work | Merge observed coverage and reallocate unfinished portions |
| New ordinary aerial observations | Allow explicitly measured duplicated observations if operationally acceptable, or defer contested claims | Deduplicate evidence and stop redundant unfinished scans |
| Delivery or exclusive landing slot | Require valid exclusive authorization/handover; no new action when authority is uncertain | Reconcile execution evidence before retrying |
| Immediate flight safety | Act onboard using local sensing and validated contingency | Resume coordination only when safe |

Leases can be useful but are not a universal fix. A replacement must not treat a locally expired record as proof the original disconnected aircraft has stopped. Any exclusive permission must also be enforced at the executor, with conservative time uncertainty and a safe withdrawal procedure. Physical completion is not an atomic database update: losing telemetry while a package is released creates an uncertain outcome requiring verification.

A replicated authority is an alternative for a small set of exclusive decisions. For example, three stable field nodes can commit permissions with a majority and replace a failed leader. This removes a single leader as the only surviving state copy, but a group without a reachable majority cannot commit new permissions. It is a separate protocol from auction consensus, and it does not automatically fence an already acting physical aircraft. [Raft paper](https://raft.github.io/raft.pdf).

## Drawbacks and proposed mitigations

These are implementation requirements and residual risks, not properties automatically provided by base CBBA.

| Failure or drawback | Proposed mitigation | What still cannot be promised |
|---|---|---|
| No shared frame or inconsistent task IDs | Versioned mission package; common frame; deterministic strip generation; map association for discovered tasks | Correct partitioning from incompatible maps |
| All drones launch from the same point and see the same best task | Deterministic conflict rules, own task capacity, workload cost; launch separation handled by local safety | Allocation alone prevents takeoff collisions |
| A drone dies midway through a strip | Replicate observed segments/evidence; reopen unfinished or uncertain portions | Missing telemetry identifies a crash with certainty |
| Radio splits the team | Preserve agreed work, relay/rendezvous where feasible, explicit duplicate-search policy, defer uncertain exclusive actions | Simultaneous availability and globally unique new ownership under arbitrary partitions |
| Old bid arrives after reassignment | Session/sequence and task/auction revisions; published asynchronous deconfliction rules | A received packet is fresh merely because it arrived now |
| Old release overwrites completion | Idempotent, revision-aware transitions; immutable completion evidence; explicit new revisit tasks | `done` stays permanent when a later legitimate revisit is required |
| Bid oscillation as drones move | Freeze evaluated near-term plans; rebid threshold and commitment policy; respect algorithm reset rules | Instant global optimum in a constantly changing environment |
| One drone accumulates too much work | Bounded bundle horizon; marginal route/work cost; feasibility checked for the entire proposed plan | Independently picking the nearest drone for each task is balanced |
| All aircraft chase a hotspot | Distinct verification subtasks, marginal information value, coverage/revisit service obligations | Equal area shares are always the correct response |
| Custom score invalidates convergence assumptions | Use analyzed score structures first; energy/capability hard constraints; document and test later heuristics | Every nonlinear risk/energy reward inherits CBBA guarantees |
| Video/map traffic blocks intent and bids | Prioritize bounded control/intent/health messages; map deltas and media queues; account for radio MAC contention | Mesh radio provides unlimited bandwidth |
| Moving topology slows agreement | Measure convergence per scenario; local replan subsets; relay roles/rendezvous when needed | A fixed universal reassignment time |
| Auction reaches agreement but routes collide | Separate local trajectory planner, sensor-based avoidance and stale-intent contingency | Assignment correctness proves physical safety |
| GPS degradation or sensor uncertainty | Track estimator uncertainty; conservative separation; validated alternate localization/recovery | Vision always rescues a failed localization system |
| Battery prediction is wrong | Calibrate current/voltage and model wind/payload/health; confidence margin and reachable recovery gate | A percentage threshold proves a mission is feasible |
| Charging/landing queues exhaust reserves | Include recovery-slot capacity and alternate sites in scheduling | Pads remain reachable or available after infrastructure damage |
| Single broker/cloud/base remains mandatory | Onboard persisted mission/ledger; peer transport; remove each dependency in failure tests | Labeling agents decentralized removes hidden infrastructure dependencies |
| Crashed process erases observations | Local durable journal and peer replication; reboot incarnation; replay-safe state | Unreplicated last observations survive destruction |
| Buggy or malicious peer wins with impossible bid | Peer authentication, anti-replay checks, feasibility validation and isolation policy | Authentication makes observations true; ordinary CBBA is Byzantine tolerant |
| More aircraft have no compute/energy advantage at scale | Bound task horizon, neighbor sets and message work; profile actual companion computers | Simulator timing transfers unchanged to hardware |
| Sensor center passes a patch but camera misses it | Ground-footprint ledger, quality/freshness thresholds and independent coverage oracle in testing | Visiting a point is complete coverage |

## Which code is useful

| Project | Appropriate use | Important boundary |
|---|---|---|
| [TrajAllocPy](https://github.com/kasperg3/trajallocpy) | Search-and-rescue line-trajectory allocation reference; CBBA, robot capacities/speeds and mission geometry | Simulation/algorithm library, not flight integration. Distance capacity is not a calibrated energy budget. MIT license. |
| [CBBA-Python](https://github.com/zehuilu/CBBA-Python) | Readable bundle building, consensus and tie-breaking reference | Its solver uses a fully connected graph and synchronous in-process agents. A central `solve()` call is not independent onboard execution. MIT license. |
| [RACER](https://github.com/Robotics-STAR-Lab/RACER) | Unknown-space exploration and decentralized workload coordination | ROS1 research stack and simulator dependencies; clarify reuse rights before copying where no top-level license is supplied. |
| [EGO-Swarm](https://github.com/ZJU-FAST-Lab/ego-planner-swarm) | Local short-horizon motion/neighbor avoidance | Navigation, not mission allocation. Default fake-drone odometry is not a realistic dynamics validation. GPLv3. |
| [Primitive-Swarm](https://github.com/ZJU-FAST-Lab/Primitive-Planner) | Newer motion-primitive local planning reference | Its thousand-agent result is simulation, with smaller physical experiments. It still needs a mission allocator. GPLv3. [Paper](https://arxiv.org/abs/2502.16887). |
| [CoverageControl](https://github.com/KumarRobotics/CoverageControl) | Voronoi/coverage-control comparison experiments | Simulation/learning library; sensor placement is not proof of completed search. GPLv3. |

Do not infer ongoing support or disaster readiness from a working README demonstration. Check the dependencies, commit selected for reuse and applicable licensing before integrating code.

## What the existing project actually needs

The current default `DroneAgent` already makes decisions locally. It is not waiting for one central nearest-drone selector. But the simulator hides important distributed-system problems.

| Current code | Observed issue | Proposed change |
|---|---|---|
| [agentic_swarm.py:176](../../../agentic_swarm.py#L176) | `(-11.25,0,11.25)[self.id//4]` has three groups of four; ID12 indexes outside the tuple | Remove ID-to-sector preferences; generate tasks from mission geometry; parameterize count |
| [disaster_demo.py:51](../../../disaster_demo.py#L51) | Fleet count fixed at twelve | Add a fleet parameter and a twenty-agent scenario |
| [agentic_swarm.py:12](../../../agentic_swarm.py#L12) | Reliable global broadcast with fixed latency | Per-link topology/range, loss, delay/jitter, reordering, duplication, partitions and byte accounting |
| [agentic_swarm.py:67](../../../agentic_swarm.py#L67) | Receipt time makes delayed health/bids look fresh | Session/sequence/age-aware message envelope |
| [agentic_swarm.py:76](../../../agentic_swarm.py#L76) | Claims lack task rounds/revision reconciliation | Separate allocation protocol from delivery logic; correct asynchronous state/reset rules |
| [agentic_swarm.py:82](../../../agentic_swarm.py#L82) | Old `released` events can regress a completed task | Versioned transitions and evidence-based completion |
| [agentic_swarm.py:104](../../../agentic_swarm.py#L104) | Eligibility relies on percentage threshold | Explicit estimated energy-to-work-and-recover constraint |
| [agentic_swarm.py:159](../../../agentic_swarm.py#L159) | Coverage means proximity to a fixed center | Sensor-footprint coverage and partial strip progress |
| [agentic_swarm.py:162](../../../agentic_swarm.py#L162) | Three-second frontier expiry is weak ownership | Explicit stale-claim/partition policy; no exclusive-action guarantee from timeout |
| [agentic_swarm.py:280](../../../agentic_swarm.py#L280) | Failure announces `offline` and returns gracefully | Silent crash, communication-only outage, reboot and uncertain completion scenarios |
| [agentic_swarm.py:303](../../../agentic_swarm.py#L303) | Safety filter has exact fleet-wide state | Keep as a development oracle; evaluate a separate local planner against local observations |

These are static code findings. The twenty-agent index failure follows directly from the tuple expression; it was not reproduced in a running aircraft simulator during this research.

Suggested boundaries are `mission_geometry`, `coverage_ledger`, `energy_model`, `peer_protocol` and `distributed_allocator`. Each drone owns an instance of the relevant state. The simulator environment can continue supplying physics and sensors without becoming the mission authority.

Implementation sequence:

1. Make fleet size and mission geometry configurable; remove fixed twelve-aircraft grouping and fixed victim-count completion.
2. Add stable coverage tasks and observed-footprint/partial-progress state; retain existing physics/control initially.
3. Build adversarial per-link communication simulation before treating auction behavior as resilient.
4. Add an independently executing one-task auction baseline, then faithful short-bundle/asynchronous allocation with task/mission revisions.
5. Add calibrated energy feasibility, explicit partition policy and silent failure/reboot recovery.
6. Separate agents into processes and make dashboard removal harmless; then evaluate PX4/SITL and realistic local flight safety.

## Acceptance scenarios

Record seeded runs, allocation latency distributions, message bytes per drone, reachable-area coverage, duplicate observation area, task churn, energy-reserve violations and separation violations. Use independently computed ground truth to score behavior; do not use the allocator's own `done` set as the only evidence.

- Twenty aircraft begin with one unassigned polygon. No individual route or ID-derived sector is supplied. Feasible work is eventually allocated under the selected protocol's stated network assumptions.
- Equal positions/capabilities/bids do not lead to indefinite conflict or starvation.
- Destroy one simulated agent silently halfway through a strip. Recover unfinished/uncertain segments; preserve already evidenced coverage.
- Shut down the dashboard and any optional LLM service. Allocation and onboard control continue.
- Partition the network twelve/eight, then heal it. Measure duplicate search and reconciliation. Do not grant conflicting exclusive authorizations.
- Deliver old claims/releases after completion, after reassignment and after a reboot. State does not regress or resurrect stale ownership.
- Inject bounded/unbounded outages, reordered/duplicated packets and constrained bandwidth. Report operating limits rather than one ideal success run.
- Reduce available energy or increase wind/payload cost. Infeasible bids are rejected before assignment; early recovery preserves the modeled reserve.
- Replace center-based coverage with camera footprints. Test overlap, missed corners, occlusion and revisit freshness.
- Replace exact neighbor knowledge with stale, noisy local observations. Measure separation separately from task completion.

Existing favorable unit tests do not establish these properties. They are proposed implementation acceptance requirements, not test results from this research.

## Primary source register

Accessed 10 October 2026. The report distinguishes source mechanisms from proposed mitigations.

1. [Choi, Brunet and How, 2009](https://dspace.mit.edu/entities/publication/b0bf0a05-be3b-433b-9f4b-ce314ed5178b): CBAA/CBBA decentralized auctions and conditional convergence.
2. [MIT ACL CBBA variants](https://acl.mit.edu/projects/consensus-based-bundle-algorithm): asynchronous handling, relay work and software/demo references.
3. [Johnson et al., 2011](https://dspace.mit.edu/entities/publication/8027006a-fddf-44a7-b5b9-3475099d7e6c): asynchronous allocation/replanning and UDP implementation.
4. [Partial Replanning for Decentralized Dynamic Task Allocation](https://arxiv.org/html/1806.04836): bundle reset dependencies, diminishing marginal gain assumptions and communication-round convergence.
5. [Cortés et al., Coverage control for mobile sensing networks](https://arxiv.org/abs/math/0212212): distributed geometric coverage control; not complete search execution.
6. [RACER](https://arxiv.org/abs/2209.08533): decentralized aerial exploration; associated code linked above.
7. [Frontier-led Swarming](https://arxiv.org/abs/2111.14295): unknown-space exploration and connectivity-friendly cohesion.
8. [Gilbert and Lynch, Perspectives on CAP](https://groups.csail.mit.edu/tds/papers/Gilbert/Brewer2.pdf): partition consistency/availability trade-offs, applied here to exclusive assignment state.
9. [Ongaro and Ousterhout, Raft](https://raft.github.io/raft.pdf): replicated log, failover and majority requirements; not a flight-control algorithm.
10. [Primitive-Swarm](https://arxiv.org/abs/2502.16887): scalable decentralized local motion planning, simulation/hardware distinction.
11. [PX4 controllers](https://docs.px4.io/main/en/flight_stack/controller_diagrams) and [failsafes](https://docs.px4.io/main/en/config/safety): onboard flight behavior separate from mission assignment.
12. Public implementation READMEs/source for TrajAllocPy, CBBA-Python, RACER, EGO-Swarm, Primitive-Swarm and CoverageControl, linked in the implementation table. These are reference components, not a certified twenty-aircraft disaster system.

Diagram: [SVG](06-decentralized-allocation.svg), [editable DOT](06-decentralized-allocation.dot), [PNG](06-decentralized-allocation.png).
