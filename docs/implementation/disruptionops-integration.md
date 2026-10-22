# DisruptionOps integration contract (prepared, disabled)

Current requested milestone is the local WebSocket simulation and robust local tests. **No DisruptionOps server was started, no production API was called, and the reference clone has no modifications.** This contract and optional patch are saved for the later integration milestone.

## What exists now

`swarm_ops/integrations/disruptionops_bridge.py` is a runnable standard-library client with a SQLite WAL outbox, stable event IDs, bounded HTTP requests, explicit acknowledgments, retries/backoff, run scoping, and simulator-to-geographic coordinate conversion. Disabled mode creates no outbox or network traffic. Its fake HTTP tests verify transport behavior; they do not prove integration with a real database or allocator.

`swarm_ops/integrations/disruptionops_patch/backend/app/drone/operations.py` and `backend/migrations/039_swarm_operations_demo.sql` (renamed from 029, which upstream now uses) are an **unmounted patch proposal** for the DisruptionOps clone at commit `4e87ade9dc5da76ae55651388fd8199aba6cd36a`. They adapt its actual existing intake, resources, assignments, taxonomy, and CP-SAT replan functions. No generic invented dispatch endpoint is presented as implemented in that repository.

## Ownership

The simulator owns drone observations, incident local IDs, simulation time, flight trajectories, and payload demonstrations. DisruptionOps would own ground assignment status and ground resource location when external mode is explicitly enabled. External mode disables local simulated ground allocation/movement/handoff so there is one ground authority. The operations UI would show imported positions as coming from DisruptionOps' resource table; this is not a claim that those values are GPS measurements.

A drone reaching a scene does not resolve the incident. A ground crew reaching the scene does not imply it accepted the briefing. The crew must acknowledge the current briefing revision; only a matching accepted acknowledgment lets the drone relinquish that observation task. Payload delivery reduces a supply need, not the extraction/medical need. Never infer scene arrival from ETA alone.

## Existing repository APIs inspected

All are under `/api/v1`:

| Existing API/module | Actual role | Consequence for integration |
|---|---|---|
| `POST /ingest/sensor` | Gateway-key authenticated detections, mesh packet IDs | Current detector taxonomy lacks `person_stranded`; duplicate responses omit original incident reference |
| `POST /reports`, `app.incidents.intake.receive` | Trust, clustering, category validation and needs | Drone evidence must go through intake, not raw incident SQL |
| `POST /replan`, `app.agents.replan.replan` | Existing CP-SAT allocation and incremental rerouting | Keep authoritative ground logic here; no parallel physical dispatcher |
| `GET /resources` | Staff-authorized resource records | Coordinates and status source, not a dispatch success acknowledgment |
| `GET /ops` | Active assignment summary | Includes actual assignment status and remaining estimated time |
| `GET /tasks`, `PATCH /tasks/{id}` | Operator work, acceptance and closure proof | Generic task acceptance is not explicit acceptance of a drone briefing |
| `POST /ops/cancel` | Policy-gated cancellation/redirect proposal | Drone bridge must never impersonate an officer or bypass the policy gate |
| `GET /events` | Newest-first scoped audit feed | Requires replay/idempotency accounting; not an ordered WebSocket command feed |
| `app.drone.swarm` | Original read-only listener for the 12-drone stream | Existing `/ws` listener does not consume the new `/ops/ws` protocol |

The three simulator categories are explicitly translated: `flood` (the implemented stranded-person scenario) becomes `person_stranded`; generic `medical` and `blocked_route` become `unknown_report`. Upstream has no generic medical-emergency or unsafe-crossing category, and `unknown_report` intentionally has no automatic capability demand. Consequently those two scenarios need reviewed taxonomy/need additions at the later integration milestone before automatic external ground allocation can work. They still work with local simulated resources today. Mapping a medical emergency to `heat_casualty`, or an unsafe crossing to a flooded road, would silently request the wrong assistance and is deliberately avoided.

An important implementation limitation: `replan(..., clock=<sim clock>)` still updates shared `resources` statuses. A non-null `sim_run_id` alone therefore does not guarantee complete resource isolation. The future adapter refuses to run outside development and requires an explicit separate demo database declaration. Its demo observations use the existing development live scope in that isolated database; local bridge run IDs scope mappings, not all database allocation demand. This must remain a separate database until upstream resource scoping is improved.

## Future adapter API

The patch would add these routes only after a future integration task mounts them:

| Method and path | Authentication | Behavior |
|---|---|---|
| `POST /drone/operations/events` | `X-Swarm-Operations-Token` | Records `incident.observed`, `drone.briefing`, or `drone.payload_delivered` in an idempotency inbox; first observation uses existing intake |
| `GET /drone/operations/state?run_id=...&city_id=pune` | Same dedicated token | Runs existing ground replan and returns corresponding ground records and explicit handoffs |
| `POST /drone/operations/handoff` | Existing staff principal | Accepts a briefing revision only for an on-site matching ground assignment |

Ingest acknowledgment means **observation accepted**, not resource dispatched. `dispatch_confirmed` is always false in the ingest receipt. State polling is the source of actual assignment state, which can still be proposed, unfilled, or on site. Drone supplies are recorded as observations only: the patch does not automatically decrement ground needs, medically diagnose victims, approve dispatches, or claim stock has changed.

Example outbound event (all positions finite):

```json
{
  "id": "stable-run-and-observation-id",
  "run_id": "local-simulation-run-id",
  "type": "incident.observed",
  "payload": {
    "city_id": "pune",
    "local_incident_id": "I1",
    "category": "person_stranded",
    "location": [73.7624378, 18.6526632],
    "title": "People stranded on rooftop",
    "people": 4,
    "severity": 4,
    "needs": ["water_rescue", "food"],
    "drone_id": "D04",
    "briefing_revision": 1,
    "briefing": {"revision": 1, "hazards": ["rising floodwater"]}
  }
}
```

Inbound callback payload for engine action `external_resource_update`:

```json
{
  "source": "disruptionops",
  "mode": "simulation",
  "run_id": "local-simulation-run-id",
  "incidents": [{"local_id": "I1", "external_id": "db-incident-uuid", "status": "responding", "resolved": false}],
  "resources": [{"id": "boat-1", "kind": "boat", "state": "on_site", "position": [100, 200, 0], "location": [73.76338, 18.65446], "incident_id": "I1", "eta": 0, "agency": "rescue", "assignment_id": "db-assignment-uuid"}],
  "handoffs": [{"incident_id": "I1", "resource_id": "boat-1", "briefing_revision": 1, "accepted": true}]
}
```

Coordinates in all simulator payloads are `[east, north, up]` in metres, anchor latitude `18.6526632`, longitude `73.7624378`, scale 1. Only the renderer converts these to its graphics axes. DisruptionOps locations are `[longitude, latitude]`. Conversion is a local tangent approximation adequate for this small demo region, not survey accuracy. Imported ground altitude is zero; no fabricated aircraft altitude or ETA-derived ground position is produced.

## Later activation steps (not executed)

1. Create a separate, seeded development database and verify its city/ward geometry covers the selected Pune demo region. Check actual category/capability taxonomy; missing categories return 422 rather than silently coercing to a different hazard.
2. Copy the two optional patch files to the corresponding DisruptionOps paths and apply migration 029 to that demo database only. Review resource sharing and policy behavior before any production integration.
3. Mount the optional router conditionally in `app/api/v1/router.py`:

   ```python
   import os
   if os.getenv("ENABLE_SWARM_OPERATIONS") == "1":
       from app.drone.operations import router as swarm_operations_router
       api_router.include_router(swarm_operations_router)
   ```

4. Set `ENABLE_SWARM_OPERATIONS=1`, `SWARM_OPERATIONS_DEMO_DATABASE=1`, `INDRADHANU_ENV=development`, and a dedicated `SWARM_OPERATIONS_TOKEN` in the DisruptionOps development process. The adapter is restricted to `pune`, requires at least two DB pool slots, and uses transaction advisory locks to serialize duplicate ingestion behind transaction poolers.
5. Set `DISRUPTIONOPS_BRIDGE=1`, `OPS_DISRUPTIONOPS_URL=http://127.0.0.1:<port>/api/v1`, and `OPS_DISRUPTIONOPS_TOKEN` in the local simulation process. Remote URLs require HTTPS. Tokens are environment values and are never returned from `status()`.
6. Wire `enqueue_snapshot(sim.snapshot())` periodically and `bridge.run()` as a cancellable background task. Route the callback to `sim.command("external_resource_update", state)` according to the engine's command API. Begin external mode before allocating any local demo ground resources. Close/stop the bridge during server shutdown.
7. Verify dropped POST replies, process restart after intake commit, duplicated event IDs, bad auth, missing taxonomy, out-of-ward coordinates, remote disconnect, two runs with identical incident local IDs, pending ground decisions, and stale briefing acknowledgment. Add real database integration tests before enabling this connection.

## Local validation completed

```powershell
.\.conda-drones\python.exe -m pip install -r swarm_ops/integrations/requirements-tests.txt
.\.conda-drones\python.exe -m unittest discover -s tests -p 'test_disruptionops_*.py' -v
```

The test server binds only `127.0.0.1` and uses a test-only token. It covers restart persistence, duplicate event suppression, failed send/backoff, run isolation, hidden incident exclusion, versioned brief updates, correct coordinate mapping, no implicit handoff, simulation-scope rejection, and disabled behavior. Additional import validation rejects NaN/infinity, wrong coordinate lengths, positions outside 50 km, invalid status values, oversized collections, malformed rows, inconsistent resolution flags, and stale or mismatched handoff assignments before invoking the engine callback. Adapter tests stub the repository interfaces to verify intake retry recovery, duplicate receipts, auth/development guards, missing taxonomy, and finite geographic bounds. No running DisruptionOps database or production credentials are involved.

The SQLite outbox is a single-process writer owned by the simulation server event loop. WAL protects against process crash but not hardware loss or disk corruption. Retention is currently manual; failed events remain pending and visible. Permanent auth/schema errors retry with bounded backoff until corrected. No local observer should treat a pending observation as a dispatch confirmation.
