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

Eligible idle aircraft claim different configured observation points. Unobserved and older observed points are considered before recently visited ones; distance breaks ties. A blocked candidate is skipped so an aircraft can try the next feasible point. Patrolling aircraft can be reassigned to an incident.

The current demo uses 25 configured observation points. Additional aircraft can remain as reserve rather than being given duplicate patrol claims. A timestamp records a waypoint visit, not a verified camera footprint or exhaustive search of every building.

**Handled:** distinct patrol claims, revisit priority and interruption of patrol for incident response. **Limit:** all these agents share one authoritative process. Distributed auctions, radio-mesh consensus and coordinator-failure recovery are not implemented here.

## 12. “Another drone reaches first; transfer the job and cancel the other.”

This particular race is **not implemented**. The allocator normally assigns one aircraft per incident. Replacement happens after a confirmed crash or task release; there is no two-aircraft race, arrival-based ownership transfer or verified cancellation of the losing aircraft.

An internal `detect` action can merge nearby same-category observations into an existing open incident and escalate severity/people counts, but **`detect` is not exposed by the public WebSocket whitelist**. It is not an arbitrary-incident creation API for your friend's application yet.

## 13. “The browser disconnects or sends the same action twice.”

The feed reconnects, marks old data stale and disables exercise controls while disconnected. Snapshots are coalesced into a bounded two-frame queue. The first/reinitialized stream includes world geometry so clients can recover their map.

Command IDs deduplicate exact retries within the running server's 2,000-entry cache. Different payloads using the same ID are rejected. `expected_run_id` prevents a delayed control from affecting a newer exercise. Events can be replayed using durable SQLite cursors.

**Handled:** stale UI, reconnect, bounded viewer backlog, duplicate commands and event replay. **Limit:** command acknowledgments and simulation state are not persisted across server restarts; the event journal is durable but not a complete event-sourced simulator.

## What has been checked

The build has 27 domain tests, 22 actual localhost transport tests, 19 optional connector/adapter tests and 15 existing planner regressions. Nine recorded 50-aircraft scenario/fault replays completed the modeled agency response. Those results establish the checked cases, not every possible edge case or real flight safety. Dense approximately 90-airborne stress occasionally exceeds the 100 ms telemetry budget.

Source files: `ops_simulation.py`, `operations_server.py`, `test_ops_simulation.py`, `test_operations_server.py`. Integration instructions: `FRIEND-WEBSOCKET-HANDOFF.md`.
