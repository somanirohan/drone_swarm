# What the disaster swarm currently handles

This document explains the implemented behavior in the same drone-first / agency-first terms used to design the system. These are simulated decisions and movements. The live DisruptionOps system remains untouched and disconnected.

## 1. “People are stuck; a drone is nearby, so send it immediately—and send an agency too.”

Imagine seven people stranded on a roof. The allocator considers a reconnaissance/supply aircraft and a rescue resource independently, in the same allocation pass. It does not wait for the drone to finish before dispatching a boat.

The drone needs a valid outbound and recovery path, a functioning link, an eligible state and enough modeled energy. If food is needed, a stocked food-carrying aircraft ranks ahead of a nearer aircraft carrying something else. Among equivalent feasible offers, travel time wins. If no compatible supply is available, an eligible drone can still provide reconnaissance, and the missing supply remains visible.

Ground selection requires the correct agency kind, availability and a traversable route. A vehicle cannot take an edge classified as flooded; a rescue boat can follow the modeled river/flood corridor.

**Handled:** parallel dispatch, route/energy eligibility, stocked payload preference and reconnaissance fallback. **Limit:** this is a severity-first greedy allocator in one process; it does not globally optimize all fleet assignments.

## 2. “The drone is already there; an agency becomes free nearby—send that agency too.”

A responder marked busy can become available at its scheduled release time, or after its previous service and transfer finish. Pending incidents are reconsidered approximately every simulated second. The newly available responder is considered from its current position, with the correct capability and a usable route.

The incident can therefore already have a drone observing it while a newly freed ground resource starts travelling. The aircraft is not recalled just because a ground assignment was created.

**Handled:** later ground availability and dispatch while aerial response continues. **Limit:** committed resources are not automatically pulled away from their current incidents when a more severe report appears.

## 3. “The drone reaches first; give the agency the current situation.”

On reaching its planned observation position, the aircraft enters `observing`. It produces a shared incident briefing: number of people, modeled hazards, outstanding needs, supplied items, source aircraft, observation time, confidence and briefing revision.

The agency and UI refer to the same incident record. While the aircraft remains observing, the briefing refreshes approximately every four simulated seconds. The content revision increases when the content changes, rather than for every observation heartbeat.

**Handled:** a shared, versioned situation briefing and visible observation age. **Limit:** people/hazards come from the scenario state. There is no implemented camera recognition, medical diagnosis or real video interpretation.

## 4. “The agency reaches first, so the drone can return.”

The responder enters `awaiting_handoff` only after traversing its simulated route. The local demo waits four simulated seconds and acknowledges the briefing automatically. If there is no aerial briefing yet, it generates a ground-crew briefing as a fallback. The connected assigned drone can be recalled even if it is still en route.

The acknowledgment can also be requested through `ack_handoff`, but only for a matching responder in `awaiting_handoff` or `on_scene`. An en-route responder cannot be falsely marked as having completed handoff by that action.

**Handled:** agency-first arrival, ground assessment fallback and early drone recall once handoff is accepted. **Limit:** the local acknowledgment is simulated, rather than a real crew member pressing an acceptance button.

## 5. “The agency reaches and has enough information; the drone should revert.”

The first accepted on-scene acknowledgment sets `handed_over=true`, starts responder work and returns the connected aircraft to base. It does not immediately resolve the incident. For a crossing needing engineering and rescue, the first crew acknowledgment can recall the drone while the remaining agency work continues.

If the aircraft has lost its link, the system records that recall remains unconfirmed. A ground handoff does not prove the aircraft received the return instruction.

**Handled:** arrival/briefing acknowledgment is separate from dispatch and resolution. **Important exception:** low energy or exhausted stock can cause an earlier return. Continuous observation until ground arrival is not guaranteed.

## 6. “People need food or first aid; the drone can supply it.”

An assigned aircraft drops a modeled item only if its payload matches an outstanding need and stock remains. The item enters `supplied`, and `payload_stock` decreases. Stock is a count of simplified supply units; the scenario does not model cargo mass or prescribed medical treatment.

The incident stays open after the drop. After eight simulated seconds with exhausted stock, the aircraft can return to reload while ground response continues. Another eligible aircraft may be allocated when aerial work remains. Reload happens at base; charging returns the aircraft to eligibility.

**Handled:** compatible supplies, stock consumption, reloading and separation of supply delivery from rescue completion. **Limit:** a recorded drop is simulator evidence, not a verified real delivery.

## 7. “There are more people than fit in one ambulance or boat.”

The responder loads at most its capacity, travels to a transfer destination and increments `evacuated_people` only after reaching it. If people remain, it returns to the incident for another trip.

Ambulances use modeled hospital destinations, road rescue uses shelters and boats use modeled shore-transfer points near shelters. If no safe transfer path exists, the resource holds and the incident stays open.

**Handled:** capacity-limited trips and actual kinematic travel before counting people as transferred. **Limit:** shore handover, patient treatment, shelter occupancy and real loading/extraction mechanics are not simulated in full.

## 8. “A bridge is damaged; rescue alone is not enough.”

The damaged road edge is removed from usable ground routes. The scenario requires both `engineering` and `rescue`. Completion of only one agency kind is insufficient; `completed_kinds` must satisfy all `required_kinds` before resolution.

**Handled:** damaged-crossing detours and multiple required agency kinds. **Limit:** one resource is allocated per required kind; there is no optimized package of several simultaneous units per kind or detailed bridge-repair physics.

## 9. “The nearest drone has too little battery; another drone should take it.”

An assignment checks an illustrative energy requirement:

```text
required Wh = (outbound route metres + recovery route metres) × 0.018
              + 7 Wh modeled work + 22 Wh reserve
```

An aircraft with less than that estimate is skipped. During the exercise, reserve depletion triggers return and releases the incident task so another eligible aircraft can respond. An injected low-battery fault follows that same return path.

**Handled:** energy eligibility, low-energy return, charging and incident reassignment. **Limit:** these constants are illustrative. Wind, aging batteries, payload mass, landing queues and calibrated electrical consumption are not modeled.

## 10. “A drone crashes or goes silent—what happens to its work?”

For a **confirmed injected crash**, the aircraft becomes inactive, its incident ownership is released and another feasible aircraft can be selected. The incident and ground response remain in the system.

For **link loss**, the aircraft freezes in `lost_link`; incident ownership is retained and `uncertain_delivery=true` records uncertainty. The allocator does not blindly send another aircraft to repeat an uncertain task. Ground agencies can still complete their work; aircraft recall remains unconfirmed.

**Handled:** incident recovery after confirmed crash and conservative ownership after link loss. **Not yet handled:** link restoration/reconciliation, bounded claim leases or a verification workflow that clears uncertain delivery.

**Known coverage recovery issue:** crash/link faults currently leave a patrol aircraft's `coverage_task` intact. The occupied-patch calculation includes inactive aircraft, so that patrol patch can remain claimed indefinitely. Existing failure tests verify incident replacement, not complete patrol-claim recovery. The zero-energy branch also does not itself clear every ownership field or simulate a safe descent. Do not describe these as fully solved failure cases.

## 11. “Twenty drones have no area assigned—how do they choose work?”

The district is a 100 m search grid with a shared probability map (prior higher near the river, in dense blocks and around reported incidents). Usable aircraft get a Voronoi zone of cells, recomputed only when an aircraft joins or fails. Each searcher flies to the most likely cell in its own zone and sweeps it with a two-lane boustrophedon pattern; a sensor footprint (altitude × tan 30°) passing over a cell lowers its probability, which slowly relaxes back. When a sweep ends the aircraft goes straight to its next cell (no hovering in "ready"). Searchers win incidents through the same bid (ETA + energy share) and keep their zone to resume afterwards.

With more aircraft than cells, the extra aircraft stay in reserve. The footprint is a geometric model, not verified camera detection.

**Handled:** distinct patrol claims, revisit priority and interruption of patrol for incident response. **Limit:** all these agents share one authoritative process. Distributed auctions, radio-mesh consensus and coordinator-failure recovery are not implemented here.

## 12. “Another drone reaches first; transfer the job and cancel the other.”

This particular race is **not implemented**. The allocator normally assigns one aircraft per incident. Replacement happens after a confirmed crash or task release; there is no two-aircraft race, arrival-based ownership transfer or verified cancellation of the losing aircraft.

`detect` (operator map click) and `random_incident` are now operator commands. Both go through the swarm's `agency_request` decision path (see section 15); `detect` still merges nearby same-category reports into an existing open incident.

## 13. “The browser disconnects or sends the same action twice.”

The feed reconnects, marks old data stale and disables exercise controls while disconnected. Snapshots are coalesced into a bounded two-frame queue. The first/reinitialized stream includes world geometry so clients can recover their map.

Command IDs deduplicate exact retries within the running server's 2,000-entry cache. Different payloads using the same ID are rejected. `expected_run_id` prevents a delayed control from affecting a newer exercise. Events can be replayed using durable SQLite cursors.

**Handled:** stale UI, reconnect, bounded viewer backlog, duplicate commands and event replay. **Limit:** command acknowledgments and simulation state are not persisted across server restarts; the event journal is durable but not a complete event-sourced simulator.

## 14. "A fire is reported; drones wait in docks and only the best two or three launch" (`dock_fire`)

Aircraft start on their own landing pads at 3–8 docks spread over dry roads. At most `DOCK_DISPATCH_CAP = 3` are airborne at once, and every other aircraft stays docked. Each incident gets the best feasible aircraft: path, recovery and energy are checked, and a stocked supply aircraft is preferred. An aircraft returns to its own pad and recharges/reloads there. The scenario has 2 or 3 incidents depending on the seed: a building fire (fire engine), a smoke casualty on the street (ambulance) and optionally trapped residents (rescue).

Ground units are the real PCMC/PCCOE units from DisruptionOps migration `029_pcmc_pccoe_zone` (`assets/operations/agency-catalog.json`): ids such as `PC-FIRE-01`, operator, station and real coordinates. Units outside the 1.2 km map wait at the map-edge road node nearest their real station. They pay a one-off approach delay of real distance ÷ ground speed (for example, "PC-FIRE-01 left Pradhikaran stn, Nigdi, ETA to district 53s"). Hospital transfers drive to the map edge toward the real hospital and add an off-map run. On-map shelters are used directly. Dispatch picks the nearest unit by real distance. If a unit frees up much nearer than the one still on its way, it takes over and the farther unit stands down (`ground_reassign`).

**Ground-first vs drone-first.** Some seeds place an ambulance already patrolling near the casualty. If a crew arrives before any aircraft, it writes the briefing (`source: "ground crew"`). The en-route aircraft is then retasked to finish only its supply drop, or recalled to its dock with a `recall` signal and a `drone_recall` event. If the aircraft arrives first, it briefs every dispatched unit (one `briefing` signal each). For each outstanding supply, it names the nearest stocked aircraft or ground unit (`supply_request`). Once an incident has been assessed, a new aircraft launches only to bring a missing supply that a stocked aircraft carries, and only after a 20 s cooldown. This stops launch/return churn. Pinned examples (Pune world, fleet 20): seed 42 has I002 ground-first, and seed 91 is all drone-first.

**Checked:** seeds 42/7/91 × fleets 5/10/20/25/40/50. Each takes ≈0.3–0.5 s to initialise. Peak airborne is ≤3, every incident resolves, and every aircraft ends on its own pad. **Limits:** off-map travel is straight-line distance at 9 m/s, not a real road route. The nearby-patrol ambulance is a scenario variation. Fire suppression is a timed service, not fire physics.

## 15. "The swarm is an agency; DisruptionOps orchestrates"

The orchestrator sends `agency_request` (sos/assess/supply/overwatch, idempotent `request_id`). The swarm accepts or declines with a reason (outside the area, no-fly zone, capacity, no energy-feasible aircraft, or launch cap for non-SOS requests). An accepted request returns an ETA, the assigned aircraft and a step plan. The swarm then raises `resource_request`s for the ground agencies the report implies. After the first aerial look it raises follow-ups, for example when more people are on scene than seats on the way. It drops what it carries itself and reports status events (`accepted`, `launched`, `on_scene`, `briefing`, `resource_request`, `supply_delivered`, `ground_arrived`, `retasked`/`recalled`, `handed_over`, `completed`). Standalone, the local units answer requests as a mock orchestrator. Two units at one incident never load the same people twice. `random_incident` and `detect` use the same path. **Limit:** request decisions are this process's rules, not a negotiated protocol with a live DisruptionOps instance.

## What has been checked

The build has 41 domain tests (including dock_fire, agency-request and snapshot-contract tests), 24 actual localhost transport tests, 19 optional connector/adapter tests and 15 existing planner regressions. Nine recorded 50-aircraft scenario/fault replays completed the modeled agency response. Those results establish the checked cases, not every possible edge case or real flight safety. Dense approximately 90-airborne stress occasionally exceeds the 100 ms telemetry budget.

Source files: `swarm_ops/engine.py`, `swarm_ops/server.py`, `tests/test_ops_simulation.py`, `tests/test_operations_server.py`. Integration instructions: `FRIEND-WEBSOCKET-HANDOFF.md`.
