# Decentralized coordination (CBBA over a simulated mesh radio)

Simulation only. Nothing here has flown or used a real radio.

The swarm can work **dependently** or **independently**:

| Mode | Who decides which drone does what | How to select |
|---|---|---|
| `central` (default) | the swarm coordinator runs a single-item auction over the drones' own bids | default; `set_coordination {mode:"central"}` |
| `orchestrated` (dependent) | DisruptionOps (or the stand-in) assigns specific drones/roles with `assign` / `orchestrate_incident` | `set_coordination {mode:"orchestrated"}` globally or per incident |
| `decentralized` (independent) | the drones themselves, by consensus (CBBA); no central award | `set_coordination {mode:"decentralized"}` or `--coordination decentralized` |

`set_coordination` takes an optional `incident_id`/`external_id` (per mission) and radio parameters (`radio_range_m`,
`packet_loss`, `latency_s`, `max_bundle`, `peer_timeout_s`). See `docs/implementation/websocket-protocol.md`.

## What happens in decentralized mode

1. **Mission in.** The command side (DisruptionOps / stand-in) sends "go to I005, report the situation" over the
   command link. It reaches only the drones that are **gateways** at that moment (within range of the base or a
   dock). A gateway is not a coordinator; any drone can be one and gateways change as aircraft move. The command
   side retransmits every 5 s until a report comes back.
2. **Local sub-task derivation** (`swarm_ops/cbba.py`). A drone that hears a mission derives `assess` and, if the
   scene is farther from the command link than one radio hop, a chain of `relay` points. The drone that reaches the
   scene derives what it *sees*: `overwatch` (3+ people, hazards or severity 4+), `search` sectors around the scene
   (uncertain head count or a search-type category), `supply` for each onboard item still missing
   (first aid / medical / food / water, only from drones carrying it), and `handover` when a ground unit is coming.
   A drone on station that has had no radio route to any gateway for 6 s announces a `relay-link` sub-task. Sub-task
   ids are deterministic (`I005/search-2`), so two drones deriving the same thing merge instead of duplicating.
3. **Allocation by CBBA** in every drone (`CBBAAgent` in `DroneAgent.cbba`). Each drone scores paths with its own
   state only: time-discounted priority `priority * exp(-t/300 s)` along the path (straight-line legs x 1.15 at
   18 m/s plus on-station durations), times a small battery factor; infeasible (None) when the payload does not
   fit or energy for the path + recovery leg home + 29 Wh reserve is not there. Bundle length 2 by default.
4. **Execution.** A drone flies to the head of its own path once it has held the claim for `commit_s` (1 s), works
   it (assess 15 s with briefing and own payload drop, search 12 s, supply drop, overwatch until handover or 240 s,
   relay until the mission closes, handover until the ground crew is in charge), marks it done and gossips that.
5. **Reports out.** The assess report is flooded back over the mesh and posted on the bus as `report` from the
   drone to `command_center` (`body.via:"mesh"`, `hops`). The swarm node still sends its `situation_report` and
   `alert` messages. Drones never ask for units, kinds or quantities, and never work an incident that was not given
   to the swarm as a mission (tested).
6. **Close.** A drone on scene that sees the ground crew in charge for 20 s closes the mission for the swarm; the
   command side also closes it when the incident is resolved or switched to another mode. Closed missions are
   gossiped (grow-only set) and their sub-tasks dropped.

No component computes a global assignment. `swarm_ops/decentral.py` is the world side: radio physics, the command
link and a scheduler that gives each aircraft a turn (`DroneAgent.coordinate`). The top-level `subtasks` list in
the snapshot is an observer aggregation for the UI and is never read by a decision.

## Mesh radio (`swarm_ops/mesh.py`)

- Range 600 m on a district map, 1.5 km on a region map (configurable). Link quality 1 up to 80 % of range, falling
  linearly to 0.5 at the edge. Delivery probability = quality x (1 - packet_loss); latency 0.1 s + up to 50 % jitter.
- One-hop broadcast for CBBA consensus messages (standard CBBA talks to neighbours; information spreads by
  consensus); flooding with TTL for reports to the command side (delivered when the flood reaches a gateway).
- Partitions are the connected components of the range graph: they emerge from where the aircraft are.
- Each drone keeps a distance-vector estimate of its hop count to a gateway from its neighbours' adverts.
- Counters: messages sent, transmissions, delivered, dropped, by kind; a small sample of recent messages.

## CBBA rule table used

Receiver `i`, sender `k`, task `j`; `z` = believed winner, `y` = winning bid, `s` = information timestamps;
"newer(m)" means `s_k[m] > s_i[m]`; ties on `y` go to the lower agent id. (Choi, Brunet, How 2009, Table 1.)

| sender thinks `z_kj` | receiver thinks `z_ij` | action |
|---|---|---|
| `k` | `i` | update if `y_kj > y_ij` |
| `k` | `k` | update |
| `k` | `m ∉ {i,k}` | update if newer(m) or `y_kj > y_ij` |
| `k` | none | update |
| `i` | `i` | leave |
| `i` | `k` | reset |
| `i` | `m` | reset if newer(m) |
| `i` | none | leave |
| `m ∉ {i,k}` | `i` | update if newer(m) and `y_kj > y_ij` |
| `m` | `k` | update if newer(m), else reset |
| `m` | `m` | update if newer(m) |
| `m` | `n ∉ {i,k,m}` | update if newer(m) and newer(n); update if newer(m) and `y_kj > y_ij`; reset if newer(n) and `s_i[m] > s_k[m]` |
| `m` | none | update if newer(m) |
| none | `i` | leave |
| none | `k` | update |
| none | `m` | update if newer(m) |
| none | none | leave |

Update = copy `(y_kj, z_kj)`; reset = `(0, none)`. Then `s_i[k] = now` and `s_i[m] = max(s_i[m], s_k[m])`. After
consensus, every task in the bundle after the first one this drone was outbid on is released (its bid was computed
on top of the lost task) and re-bid in the next bundle-building pass: this is the partial replanning that also runs
when new sub-tasks appear.

Practical extensions (all local to each drone):

- **Asynchronous (ACBBA-style)**: messages are processed on arrival; a drone rebroadcasts when its state changed and
  sends a heartbeat every 2 s.
- **Lost peers**: if nothing (direct or relayed) has been heard from a drone for `peer_timeout_s` (10 s), its open
  sub-tasks are reset so others can take them over (crash, radio fault, partition). A fresher timestamp of that drone
  arriving later revives it; stale claims by a lost drone are ignored.
- **Locked execution**: a sub-task being executed is bid at `LOCK + bid` so it is not taken mid-execution. Two locked
  claims (both sides of a healed partition did it) resolve by the higher bid.
- **Hysteresis**: bidding against an existing winner needs `max(0.5, 5 % of the winning bid)` more.
- **Commit delay**: fly only after holding a claim for 1 s, so tentative bids overturned within a few exchanges do
  not launch aircraft.
- **Grow-only knowledge**: missions, sub-task definitions, done and closed sets are merged by union.

## Guarantees that do NOT hold here

- CBBA's convergence and 50 %-optimality results assume **diminishing marginal gains** (DMG) and a static,
  connected network. Our scores are nonlinear: energy feasibility is a hard cut, payload capability is binary,
  and positions change while drones fly. DMG can be violated, so convergence is not guaranteed in general and
  oscillation is possible; hysteresis, locking and the commit delay damp it, and the tests check convergence on
  fixtures, not in general.
- The convergence bound (2 x network diameter x tasks rounds) assumes a fixed topology and no loss. With packet loss
  and moving partitions we only claim eventual agreement while the network stays connected long enough.
- During a partition both sides may work the same sub-task (by design: both keep working). Duplicates are resolved
  after rejoin, not prevented.
- The lost-peer timeout cannot tell a crash from a partition.
- Launch caps of docked fleets (`dispatch_cap`) are a central constraint and are not enforced in decentralized mode.
- A mission closed for the swarm is not re-issued if the incident is switched back to decentralized later.
- No MAC/PHY model (collisions, bandwidth, interference); no real clock skew (simulation time is shared).

## Comparison (tools/compare_coordination.py)

Same scenario and seed in the three modes: `rooftop_flood`, Pune district world, seed 42, 20 drones, 600 s, two
extra random incidents at 30 s and 50 s, and the aircraft flying to the farthest scene is crashed at 12 s.

| mode | assessed | first assessment | mean | coverage | sub-tasks done | recovery after crash | bus msgs | mesh msgs |
|---|---|---|---|---|---|---|---|---|
| central | 4/4 | 9.5 s | 24.8 s | 100 % | 9 (4 assess, 5 supply) | 1.0 s | 186 | 0 |
| decentralized | 4/4 | 9.0 s | 26.8 s | 100 % | 23 (4 assess, 12 search, 4 handover, 3 supply; relays/overwatch end at close) | 9.0 s | 370 | 6539 |
| decentralized, 20 % loss | 4/4 | 9.0 s | 30.0 s | 100 % | 24 | 9.0 s | 371 | 6568 |
| orchestrated (stand-in re-assigns on the fault alert) | 4/4 | 10.2 s | 26.2 s | 100 % | 6 | 2.0 s | 132 | 0 |

Reading it: the decentralized swarm reaches scenes about as fast and does more aerial work on its own (search
sectors, handover, overwatch, relays), at the cost of radio traffic and a recovery time bounded by the lost-peer
timeout instead of an instant central re-award. Recovery is measured differently by design: decentralized = the
crashed drone's own sub-task is held by a peer; central/orchestrated = a new aircraft is sent to that incident.

Run it: `.\.conda-drones\python.exe tools\compare_coordination.py [--loss 0.2] [--seed N] [--json]`.

## Tests

`tests/test_decentralized.py`: conflict-free assignment and convergence (fully connected within 12 rounds, a 6-hop
line, 20 % loss), the rule table, lost-peer release and re-win, a geometric partition where both sides keep working
and reconcile with one winner per task after rejoin, multi-hop flooding to a gateway, local sub-task derivation (no
unit kinds or quantities), no central `award` call in decentralized mode, crash mid-mission re-won within 30 s, no
two drones executing the same sub-task with 20 % loss, drones scoped to the mission they were given, and
orchestrated/central modes unchanged.

## References

Choi, Brunet, How, "Consensus-Based Decentralized Auctions for Robust Task Allocation", IEEE T-RO 25(4), 2009.
Johnson, Ponda, Choi, How, "Asynchronous Decentralized Task Allocation for Dynamic Environments" (ACBBA), 2011.
Prior notes: `docs/research/100-drone-disaster-response/20-drone-decentralized-allocation.md` (CBBA-Python,
TrajAllocPy, MIT ACL CBBA).
