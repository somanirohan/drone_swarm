# Plan V3: region scenario, command center, agentic architecture

Written 2026-10-10. Builds on [PLAN.md](PLAN.md). Reference DisruptionOps: commit `1697407`. Status key: [x] done and verified, [~] partly, [ ] open, [ip] in progress by another agent (not verified by this document).

## Goal of this round

Make the swarm behave like an agency that is commanded by a command center, in the real PCMC area that DisruptionOps already models, with the agent logic visible and honestly described.

## Work packages

| # | Package | Deliverable | Owner | Acceptance criteria | Status |
|---|---|---|---|---|---|
| 1 | Region scenario from migrations | `tools/prepare_pcmc_region.py`, `assets/operations/pcmc-region.json`, scenario `pccoe_pawana_night`, server `--world` flag, scenario/world registry in the engine | REGION | World built read-only from DO migrations 029/030 (wards, 32 `PC-*` units, 16 lifelines); scenario loads in `OperationsSimulation`; server starts with the region world; tests for build and load | [ip] registry + tool + `swarm_ops/scenarios/` exist; scenario acceptance not verified here |
| 2 | Command center agent | `swarm_ops/command_center.py` (rich `situation` per request from DisruptionOps data; resource requests answered from the agency catalog), `scripts/run-command-center.ps1`, `tests/test_command_center.py` | COMMAND | Connects with the integration token; issues tasks from a scenario; tracks accepted, launched, on_scene, briefing, resource_request, supply_delivered, completed; answers resource requests with `external_resource_update` (orchestrator mode) or lets the local mock answer (standalone); idempotent request ids; writes a JSON timeline | [x] 6 tests pass (5 s to 25 s each); smoke run over a real server on port 8861 completed 3 of 3 tasks |
| 3 | Real-mode adapter | `RealModeAdapter` in `command_center.py` (dry-run default) | COMMAND | Maps task to `POST /api/v1/reports`, resource request to `POST /api/v1/agency-requests`, `GET /api/v1/ops` to `external_resource_update`; unit test of mapping only | [~] skeleton, never run against a live backend; response field names for `/ops` are assumed |
| 4 | Server messages feed | `GET /ops/messages?after=<id>&limit=<n>`; `messages` reaches WS clients through the snapshot | COMMAND (server), ENGINE (content) | Endpoint returns a paged JSON list; command center logs new messages | [~] endpoint done and tested (empty list); waiting for ENGINE to publish `messages` |
| 5 | Agentic architecture (deterministic, offline; **no LLM**) | Explicit agent classes (`swarm_ops/agents.py`), onboard reasoning (`swarm_ops/reasoning`: Bayesian belief, taxonomy-driven needs, marginal-utility/CP-SAT optimiser, hysteresis escalation, under 50 ms), `messages` log, `decisions` traces | ENGINE + REASONER | Each agent decision has a reason in `decisions`; every command-center to agent exchange appears in `messages`; no behaviour change in existing tests | [ip] |
| 6 | All objects incl. civilian traffic | Civilians and vehicles on the roads of the region world, visible in the viewer, as obstacles to ground routes | ENGINE + FRONTEND | Count of civilian vehicles/people in snapshot; rendered without popping; ground ETAs react to blocked roads | [ip] |
| 7 | Frontend for command center and agents | Viewer panels: message log, decision traces, task lifecycle | FRONTEND | Shows `messages` and `decisions`; no console errors in the browser test | [ip] |
| 8 | Path fixes | Consistent paths after the reorg (`swarm_ops/`, `web/ops/`, `scripts/`, `tools/`); scripts run from any directory | all | `run-operations.ps1`, `run-command-center.ps1` start from a different working directory; docs link-check | [~] scripts use the repo root; docs updated in the reorg commit, this round's docs added |
| 9 | System logic documentation | [SYSTEM-LOGIC-MAP.md](SYSTEM-LOGIC-MAP.md), [system-logic.dot](system-logic.dot) / [.svg](system-logic.svg), [AGENTIC-ASSESSMENT.md](AGENTIC-ASSESSMENT.md) | COMMAND | Every logic file of both systems has a line; flows rendered; assessment cites file and line | [x] |

| 10 | Algorithm glossary | [ALGORITHM-GLOSSARY.md](ALGORITHM-GLOSSARY.md): formal names, code location and honest status for every decision component | COMMAND | Each row names real code or says planned; re-checked at the end of the round | [x] first version; statuses re-checked at hand-off |

## Acceptance for the round (end to end)

- [x] Server on a spare port plus command center: tasks accepted, resource requests answered, at least one completed, JSON timeline written (`tests/test_command_center.py`).
- [ ] Same run on the region world with `pccoe_pawana_night` (needs REGION package 1 accepted).
- [ ] Messages visible in the viewer (needs packages 5 and 7).
- [ ] Full test suite green after all agents' changes (run `python -m unittest discover -s tests -p "test_*.py"`).

## Design constraint (user decision)

No language model in any decision path. Decisions are onboard, deterministic and offline. See [AGENTIC-ASSESSMENT.md](AGENTIC-ASSESSMENT.md) section 4.

## Known limits (stated up front)

- The command center drives its ground units in a straight line at 9 m/s; the swarm's own ground simulation uses roads. Orchestrator mode therefore tests the protocol, not routing.
- With `external_ground` on, the engine does not complete ground service by itself; the command center closes incidents (`incidents[].status = resolved`) after a service time it chooses. This is the contract today; an engine event for it is not emitted.
- Nothing here talks to a live DisruptionOps backend. The migration-030 patch is a proposal and is not applied.
- The integration token is an environment variable shared by process, not a user identity.

## How to run

```powershell
scripts\run-command-center.ps1 -Port 8861 -Scenario pccoe_pawana_night -Mode orchestrator
```

Falls back to random valid points if the scenario is not registered in the engine.
