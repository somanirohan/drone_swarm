# System logic map

Every logic file of both systems, one line each, then the end-to-end flows. The flows are also in
[system-logic.dot](system-logic.dot) (rendered: [system-logic.svg](system-logic.svg); regenerate with
`dot -Tsvg docs/system-logic.dot -o docs/system-logic.svg`, dot.exe is under `.tools/graphviz/`).

- **DisruptionOps (DO)**: read-only reference clone `D:\drone_research_reference\DisruptionOps-latest`, commit `1697407`, paths relative to `backend/app/`. Python/FastAPI/Postgres, 36 migrations.
- **Swarm**: this repository. Paths relative to the repo root.

"Decides" means the file contains the rule that chooses something. LLM = a language model may be called, always with a deterministic fallback in DO.

## 1. DisruptionOps `backend/app/**`

### 1.1 Pipeline and agents (`agents/`)

| File | What it decides / does |
|---|---|
| `agents/graph.py` | The LangGraph cycle (guard_in, triage, command, fanout, sense, assess, domain, optimise, validate, policy_gate, human_approval, dispatch, observe). Per-node read/write contracts, `interrupt()` for approval, checkpointer (in-memory or Postgres). No LLM call inside the graph itself. |
| `agents/event_router.py` | Turns events into replans: polls the event log, debounces, takes a leader lease, decides when to run the graph or the direct replan, nudges the Commander. |
| `agents/replan.py` | Builds demands and fleet from the DB, calls the CP-SAT allocator and routing, diffs against current assignments (kept / assigned / reassigned / released), writes assignments, reroutes blocked units. `preview()` is the dry run used by the graph. |
| `agents/orchestrator.py` | One hazard run end to end (signal, score, impact, proposed actions, policy gate, demands, matrix, allocation, field tasks, alerts); every step an event with its cause. States "the model never produces a number". |
| `agents/commander.py` | The only free-form LLM agent: a tool loop (max 6 steps, 45 s) that reads, analyses and may `propose_action`; output goes through guardrails and the policy gate. Skipped if no model is reachable. |
| `agents/policy.py` | The delegation matrix: which action classes may issue automatically, need an officer, or are forbidden, from the municipal plan / NDMA clauses. |
| `agents/gate.py` | Runs one decision through the policy gate at any point in a run (Copilot actions, graph, router). |
| `agents/guardrails.py` | Input cleaning/PII/injection, output schema checks, grounded-number checks, plan checks (double assignment, units moved per cycle, coverage drop), drift check, trip log. |
| `agents/agent_memory.py` | Per-agent scoped memory with an access table and a ledger; officers' standing orders readable by all, writable by none of the agents. |
| `agents/llm.py` | Provider chain Gemini then Bedrock then deterministic fallback; cache, circuit breaker, guard_prompt/guard_output on every call. |
| `agents/llm_cost.py` | Token budget and cost control: skip the model when a rule answers, cache, small model for easy tasks. |
| `agents/forecast.py` | Learned incident-rate forecast per ward and category from event history (cold-start safe). |
| `agents/ml.py` | The learned parts and where they are used (GloFAS discharge, XLM-R zero-shot classify, ETA quantiles); states that trust, gate and allocator stay deterministic. |
| `agents/preposition.py` | Proposes (never executes) moving equipment ahead of forecast demand. |

### 1.2 Solver, navigation, hazards

| File | What it decides / does |
|---|---|
| `solver/allocation.py` | OR-Tools CP-SAT assignment of units to demands (capability match, travel cost, coverage); greedy nearest fallback on timeout. |
| `solver/routing.py` | Travel-time matrix (Mapbox, OSRM, else great-circle), routes over the road graph, road blocks. |
| `nav/cost.py`, `nav/hazards.py`, `nav/profiles.py` | Segment traversal seconds per unit profile and hazard state; documented speed/stop assumptions per unit kind. |
| `nav/passability.py`, `nav/live.py` | P(blocked at arrival) per road segment from a trained model plus rules; live cached risk the router avoids. |
| `nav/eta.py` | Quantile ETA model for response time during a flood. |
| `nav/sectors.py` | Operational sectors (groups of wards) used to file agent logs and guardrail trips. |
| `hazards/base.py`, `registry.py`, `_registered.py` | The five-stage hazard adapter contract (signal, score, exposure join, actions, outcome) and the registry. |
| `hazards/flood.py`, `heat.py` (+ `air.py`, `fire.py`, `seismic.py` stubs) | Live flood (GloFAS + rain + terrain) and heatwave scoring; others are registered but not trained. |
| `guidance/router.py` | Citizen-side agent: where to go (shelter, hospital) given hazards and blocks, one sentence plus route. |

### 1.3 Reports, incidents, sensing (`incidents/`, `iot/`, `mesh/`, `drone/`, `ingest/`)

| File | What it decides / does |
|---|---|
| `incidents/intake.py` | The single door for any claim that something is happening (citizen, field, agency, sensor, sim): create or link an incident, record the report. |
| `incidents/trust.py` | Report trust score (reporter reliability, corroboration, source, burst/adversarial patterns). |
| `incidents/clustering.py` | Duplicate-report clustering; optional LLM adjudication of borderline pairs (`llm.complete`, line 298). |
| `incidents/duplicates.py` | Duplicate effort: two agencies dispatched to one incident. |
| `incidents/severity.py` | Severity from the words, with optional LLM help (line 140); category base plus corroboration otherwise. |
| `incidents/parse.py` | Keyword category pass, then zero-shot/LLM classify (line 341) only into existing taxonomy categories. |
| `incidents/speech.py`, `vision.py`, `vision_client.py` | Spoken reports; the contract for what a photo may assert; client for the external vision model. |
| `iot/fusion.py`, `kinds.py`, `service.py`, `virtual.py` | LoRa sensor readings judged against each node's baseline, state/escalation, analytics, and a virtual sensor fleet. |
| `mesh/envelope.py`, `service.py` | IDX1 offline-mesh packets: verify, store once, feed into intake. |
| `drone/swarm.py` | Read-only listener of the earlier 12-drone PyBullet swarm feed: survivor located becomes a report (`intake.receive`), obstacle becomes a road block, payload landed becomes an event. |
| `drone/service.py` | Drone-frame localisation results into the log. |
| `ingest/client.py`, `open_meteo.py` | Never-raise upstream fetching with last-good fallback; forecast, GloFAS, air quality. |
| `core/ingest.py`, `worker.py` | Lane-based ingest bus (accept fast, batch writes, shed bulk before critical) and its worker process. |

### 1.4 Operations, units, surge, world

| File | What it decides / does |
|---|---|
| `ops/operations.py` | One committed unit and its job: list, alternatives, cancel as a gated proposal. |
| `ops/autonomy.py` | Emergency stop: no machine moves a unit, alerts or starts an agent until an officer resumes. |
| `units/service.py` | Position reports, off-route rerouting, replans, per-unit logs. |
| `surge/service.py`, `surge/operations.py` | Escalation ladder when units/shelters run out; mutual aid, surge shelters, scarcity drill. |
| `world/clock.py`, `world/events.py` | Injected clock (nothing calls `now()`); append-only event log that is the audit trail and the replan trigger. |
| `copilot/agent.py` | Commissioner Command Agent: LLM routes a question to a tool (line 213), tools return numbers, LLM narrates (line 1055); rule router when no model. |
| `copilot/tools.py`, `simulate.py`, `strategies.py`, `execute.py`, `memory.py` | The tool catalogue (model never sees the DB), what-if re-solves with the same CP-SAT model, solver-priced strategies, executing approved actions, conversation and lesson memory. |
| `demo/runner.py`, `demo/scenarios.py` | The demo world driving the real pipeline at human speed; named scripted scenarios usable as regression checks. |

### 1.5 API, core, data

| File | What it decides / does |
|---|---|
| `main.py`, `core/config.py`, `core/middleware.py`, `core/logging.py`, `core/errors.py` | App wiring, settings, request middleware, logging, error types. |
| `core/security.py`, `core/jwks.py`, `core/ratelimit.py`, `core/lease.py`, `core/shared.py`, `core/cache.py` | JWT auth and roles, JWKS, rate limits, leader lease, cross-replica state, single-flight cache. |
| `api/v1/router.py` | Mounts all routers under `/api/v1`. |
| `api/v1/reports.py` | `POST /reports`, `/replan`, `/agency-requests` (+ acknowledge/decline/fulfil), `/simulate/reports`, report verdicts, reporter reliability. |
| `api/v1/ingest.py`, `iot.py`, `mesh.py` | High-volume doors (202 + ticket), sensor observations, mesh gateway. |
| `api/v1/ops.py`, `operations.py`, `units.py`, `runs.py` | `GET /ops`, cancel, autonomy; incidents/resources/allocation/decisions/tasks; unit tracks and logs; hazard runs and events. |
| `api/v1/agent_graph.py`, `agent_log.py` | Graph status/diagram/run/resume/memory/orders; agent log board. |
| `api/v1/copilot.py`, `citizen.py`, `personas.py`, `demo.py`, `replay.py`, `config.py`, `surge.py`, `risk.py`, `nav.py`, `drone.py`, `geography.py`, `system.py`, `auth.py` | Command agent API, citizen portal (LLM answer at line 164), role-specific surfaces, demo snapshot and controls, replay, deployment config, surge, risk, passability, drone endpoints, reference geography, status, auth. |
| `schemas/domain.py`, `taxonomy.py`, `regions.py` | Pydantic models; taxonomy cache (hazards, categories, capabilities as rows); operating regions sharing one city id. |
| `db/session.py`, `db/repositories/queries.py`, `seed/seed_pune.py`, `media.py` | Pool, explicit read SQL, idempotent Pune seed, small photo storage. |
| `migrations/001-036` | Schema; region world for the swarm comes from 029 (PCMC/PCCOE units, lifelines) and 030 (sectors). |

## 2. Swarm repository

| File | What it decides / does |
|---|---|
| `swarm_ops/engine.py` | The whole simulation. `_agency_request` (accept/decline), `_incident` (merge/new), `_allocate` (feasible bids), `_drone_path` (A* flight grid, no-fly), energy model, `step()` (flight, search, briefing, ground, handoff), `_behavior` (drone behaviour label), `_drone_arrival_brief` and `_raise_resource_request` (briefing, ask for ground), `_ack` (handoff, recall), `command()` (all actions), `snapshot()`. |
| `swarm_ops/server.py` | HTTP + WebSocket `/ops/ws`: control vs integration token, 10 commands/s limit, idempotent command ids, run-id guard, 10 Hz coalesced snapshots, SQLite event journal, `/ops/events`, `/ops/messages`, static web. |
| `swarm_ops/command_center.py` | Orchestrator agent for the swarm: issues tasks, tracks lifecycle, commits ground units for resource requests, closes incidents; REAL-mode adapter skeleton. |
| `swarm_ops/scenarios/` | Scenario and world registrations (region scenario `pccoe_pawana_night`; owned by the REGION work). |
| `swarm_ops/integrations/disruptionops_bridge.py` | Durable outbox client to DO's optional `/drone/operations` adapter: coordinate transform, event/snapshot enqueue, poll external state back, validation. Off unless `DISRUPTIONOPS_BRIDGE=1`. |
| `swarm_ops/integrations/disruptionops_patch/` | Proposed backend patch (migrations 039-040 and `app/drone`) for DO; not applied. |
| `web/ops/operations.{html,css,js}` | 3D viewer and operator console: renders snapshots, sends control commands. No decisions about the mission. |
| `tools/build_agency_catalog.py`, `prepare_pcmc_region.py` | Read DO migrations 029/030 and write the agency catalog and PCMC world. |
| `tools/prepare_operations_{map,heights,terrain}.py` | Fetch OSM, building heights, SRTM into local-metre world files. |
| `scripts/run-operations.ps1`, `run-command-center.ps1`, `setup-ops.ps1`, `share-with-friend.ps1` | Start server (+tunnel), start server + command center, set up environment, share. |
| `examples/operations_ws_client.py`, `.mjs` | Reference WS clients. |
| `tests/` | `test_ops_simulation.py` (engine), `test_operations_server.py` (transport), `test_command_center.py`, `test_disruptionops_*` (bridge/contract), acceptance scripts. |
| `legacy/` | The earlier PyBullet 12-drone exercise (not used by the operations simulator). |

## 3. End-to-end flows

### 3.1 DisruptionOps: report to observe

1. **Intake.** A report arrives via `POST /reports`, `/ingest/reports` (queued, 202), the mesh gateway, a LoRa observation, or a swarm feed sighting. `incidents/intake.py` scores trust (`trust.py`), links duplicates (`clustering.py`) and sets severity (`severity.py`); category by keywords then optional model (`parse.py`). Result: an incident with severity and capability **needs** from the taxonomy. Every step is an event (`world/events.py`).
2. **Trigger.** `agents/event_router.py` sees the event, debounces and (leader lease) starts a cycle: the LangGraph in `agents/graph.py` if installed, else `replan.replan` directly. A severity of 5 or more also wakes the Commander (`command` node, `commander.nudge`).
3. **Needs and fleet.** `sense` fans out four reads (needs, fleet, hazards, risk); `assess` computes shortfall per capability; `domain` planners per domain with a need record it in their own memory (three cycles short in a row flags mutual aid).
4. **CP-SAT replan.** `optimise` calls `replan.preview` (dry run) which builds demands and fleet, gets the travel matrix (`solver/routing.py`, passability from `nav/`), and solves with `solver/allocation.py`. Output: kept, assigned, reassigned, released, uncovered.
5. **Validate.** `guardrails.check_plan` (double assignment, units moved, coverage drop); up to 3 retries, else `give_up`.
6. **Gate.** `policy_gate`: paused (emergency stop) holds; no change ends; a remembered officer rejection holds; pulling a unit off an S3+ incident, or tripping a guardrail, requires an officer.
7. **Approval.** `human_approval` calls `interrupt()`; the run is checkpointed and waits for `POST /agent-graph/runs/{id}/resume` with `{approved, by, note}` (timeout counts as rejection). The answer is written to gate memory.
8. **Dispatch.** `dispatch` re-previews the solve; if it would now move a unit the approved plan did not, it writes nothing (drift). Otherwise it writes assignments and field tasks and audits what it wrote.
9. **Observe.** `observe` summarises; units report positions (`units/service.py`), arrivals and off-route events go back into the event log and trigger the next cycle.

### 3.2 Swarm: agency flow

1. The orchestrator (DO or `command_center.py`) sends `agency_request` (kind sos, assess, supply, overwatch) with an integration token; the server checks the token, rate limit and idempotent id (`server.apply_command`).
2. `engine._agency_request` **decides by itself**: outside the area, no-fly, 12 open incidents, launch cap reached (non-sos), or no energy-feasible aircraft means decline with a reason. Otherwise it merges with an open incident within 35 m or creates one, then runs `_allocate`.
3. `_allocate` gives the incident to the best feasible drone (distance, energy, payload); `_drone_path` plans around no-fly zones; docked aircraft launch, capped at 3 airborne.
4. On arrival `_drone_arrival_brief` produces a briefing (people, needs, ground access), drops stocked supplies, and raises `resource_request` events for ground kinds the briefing implies (`_raise_resource_request`) addressed to the orchestrator.
5. The orchestrator commits ground units with `external_resource_update` (or the local mock moves its own units). Arrival plus an accepted handoff with the current briefing revision triggers `_ack`: the drone is recalled or retasked, the ground crew serves.
6. When every required kind has completed, the incident is resolved (`completed` status). Every transition is an `events` entry with a machine `status` (`ORCHESTRATOR_STATUS`), also in `/ops/events`.

### 3.3 Where the two systems meet

Today: the swarm exposes the contract (`disruptionops.swarm.v2`); DO's older `drone/swarm.py` listener reads the PyBullet feed only, and the proposed patch (`disruptionops_patch`) adds a `/drone/operations` adapter that is **not applied** to DO. `command_center.py` therefore stands in for DO against the real swarm API, and its `RealModeAdapter` documents the intended DO endpoint mapping (`POST /reports`, `/agency-requests`, `GET /ops`), unverified against a live backend.
