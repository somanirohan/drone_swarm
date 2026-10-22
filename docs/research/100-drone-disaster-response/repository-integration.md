# Repository findings and integration boundaries

Inspected 10 October 2026. Local swarm commit: `d65a5388827eb5d93a4016e51ff0184f695296f2`. DisruptionOps shallow reference clone: `4e87ade9dc5da76ae55651388fd8199aba6cd36a`, at `D:\drone_research_reference\DisruptionOps`. The existing untracked `PRODUCTION-ARCHITECTURE.md` was read and left unchanged. No production endpoints were exercised and no flight/application code was modified.

## Existing integration

- `disaster_demo.py`: hardcoded `n = 12`, fixed survivor locations, geometric sensing, physics and web publishing.
- `agentic_swarm.py`: independent agents, bids, ownership and exploration; fixed-latency in-memory `MessageBus`; scalar battery depletion; global pairwise separation filter.
- `ai_planner.py`: asynchronous mission proposals validated before use; no motor outputs from the model.
- `jev_planner.py`: typed planning questions, distance-sorted candidate menu, serialized requests and shared reservation memory.
- `web_stream.py`: read-only browser feed. It ignores received messages, so it is not a mission-command interface.
- DisruptionOps `backend/app/drone/swarm.py`: read-only listener; simulator-to-map anchor/scale; parses init/frame/log; converts discovery to `intake.receive`; writes delivery events, mesh-node positions and temporary road blocks.
- `backend/app/api/v1/drone.py`: staff GET/POST swarm status/configuration and image-localizer endpoints. POST swarm changes feed configuration; it does not command an aircraft.
- `backend/app/agents/gate.py`: existing action-specific policy/delegation gate and decision audit.
- `backend/app/solver/allocation.py`: capability-based ground allocation with a bounded solver and fallback.
- `backend/app/solver/routing.py`: road travel estimates; do not reuse as aircraft route/energy estimates.
- `backend/app/core/ingest.py`: prioritized memory queues or Redis Streams. Memory acceptance is not durable across process loss.
- `backend/app/world/events.py`: existing domain-event integration point.
- `backend/app/regions.py`: Pune and NCR demo geography share one city ID; Assam needs real region/ward/tenant handling, not an anchor change alone.
- `frontend/indradhanu/src/routes/admin/droneApi.ts`: existing status polling and drone types.
- `backend/tests/test_drone_swarm.py`: mocked feed/DB integration tests, not physical-flight evidence.

Repository links: [swarm](https://github.com/tusharsingh-spring/drone_swarm), [DisruptionOps](https://github.com/officialayush23/DisruptionOps). Above claims are based on these checked-out source versions.

## Proposed boundary

Keep the current listener as a **simulation adapter**. Add a versioned fleet adapter that sends typed observations to existing intake and mission results to the event log. Mission requests go through authority checks and a durable outbox to the designated field coordinator. Ground rescue allocation stays in DisruptionOps; aircraft allocation and air-route energy checks use their own model.

The existing shared gateway key is not a per-aircraft identity/authorization model. Real field gateways need scoped identities, replay protection, revocation and authenticated encrypted transport.

Proposed task identity: `mission_id`, `task_id`, `command_id`, `authority_generation`, `assigned_cell`, `assigned_aircraft`, `issued_at`, `expires_at`, capability, operating polygon, altitude reference, outcome condition and contingency. These are design fields, not existing APIs.

Proposed observation identity: event ID, mission ID, aircraft ID, boot/session ID, sequence, capture and receive times, position with datum/reference and uncertainty, detection confidence, sensor/model version, evidence reference/hash and simulation flag.

Proposed result: command ID, accepted/rejected/executing/completed/failed/aborted, structured reason, recorded evidence and observed time. Receipt, flight-controller acceptance and physical outcome are distinct.

Durable deduplication must outlive the retry window. Preserve assignment generations and reject stale issuers. A task lease timing out does not by itself prove that a disconnected executor stopped. Hold conflicting assignments until release or validated expiry/quiescence is established.

## Assumptions to remove

1. Ground truth survivor coordinates in operational feed: keep only in evaluation tools.
2. Aerial obstacle detour becoming ground-road closure: require separate road-status evidence and validation.
3. Payload simulator landing becoming confirmed receipt: retain separate release, landing, receipt and extraction events.
4. Local coordinate anchor scaling as real geolocation: actual aircraft require validated frames, calibration and uncertainty.
5. In-memory state and text logs as operational history: use durable typed events and resumable synchronization.
6. Increasing `n` as field scalability: benchmark independent processes, actual protocol traffic, fault recovery and operator workload.
