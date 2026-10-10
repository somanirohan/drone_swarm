# Documentation index

Reference DisruptionOps version for all integration documents: commit `bd21179` (clone `D:\drone_research_reference\DisruptionOps-latest`). The older `DisruptionOps` clone (`4e87ade`) is stale.

## Start here

| Document | One line |
|---|---|
| [PLAN.md](PLAN.md) | The user's requirements as numbered items, deliverables with acceptance tests, phases, risks, definition of done. |
| [ALGORITHMS.md](ALGORITHMS.md) | The algorithms the engine really uses (A*, Dijkstra, bidding, zones, energy), with function names and honest limits, and what is not used. |
| [CAPABILITIES.md](CAPABILITIES.md) | What works, what is partial, what is planned, and how each was verified. Honest about being a kinematic demo. |
| [FOLDER-LAYOUT.md](FOLDER-LAYOUT.md) | Folder structure (executed 2026-10-10): final tree, old-to-new move map, compatibility shims. |
| [PLAN-V3.md](PLAN-V3.md) | This round: region scenario, command center and agents, agentic architecture, civilian traffic, path fixes; owners, acceptance, status. |
| [SYSTEM-LOGIC-MAP.md](SYSTEM-LOGIC-MAP.md) | Every logic file of DisruptionOps and the swarm, one line each, plus end-to-end flows ([system-logic.dot](system-logic.dot), [system-logic.svg](system-logic.svg)). |
| [AGENTIC-ASSESSMENT.md](AGENTIC-ASSESSMENT.md) | Are the agents really agentic? Where `graph.py` is, what is LLM or LangGraph driven, what the swarm lacks. |
| [ALGORITHM-GLOSSARY.md](ALGORITHM-GLOSSARY.md) | Formal literature names (MRTA, Contract Net, CBBA, Bayesian fusion, MCDA, CP-SAT, hysteresis, FSM/BT, Voronoi, boustrophedon, any-angle planning, MCLP, START, MAPE-K) mapped to code with honest status. |

## DisruptionOps integration (`integration/`)

| Document | One line |
|---|---|
| [integration/disruptionops-migrations.md](integration/disruptionops-migrations.md) | All 29 migrations read and interpreted, the data model, where the swarm plugs in, a NOT APPLIED seed, and errors found in the earlier patch. |
| [integration/how-disruptionops-calls-the-swarm.md](integration/how-disruptionops-calls-the-swarm.md) | HTTP and WebSocket contract, mission requests, report-back mapping to `events`, pause/hold handling, auth, failure behaviour. |
| [integration/call-sequence.dot](integration/call-sequence.dot) | Graphviz source of the call sequence (render with `dot -Tsvg`). |

## Existing implementation docs (`implementation/`)

| Document | One line |
|---|---|
| [implementation/implementation-plan.md](implementation/implementation-plan.md) | Earlier plan and run instructions for the Pune simulator. Superseded in scope by PLAN.md. |
| [implementation/websocket-protocol.md](implementation/websocket-protocol.md) | `disruptionops.swarm.v2` WebSocket and HTTP contract, including the agency/orchestrator commands. |
| [implementation/FRIEND-WEBSOCKET-HANDOFF.md](implementation/FRIEND-WEBSOCKET-HANDOFF.md) | Full connection guide; section A has the new fields and commands, the view-only rule, and how to detect and plot a dispatched ambulance with real payloads. |
| `../examples/operations_ws_client.py`, `../examples/operations-websocket-client.mjs` | Reference clients (code: `../swarm_ops/`, viewer: `../web/ops/`); print dispatches, signals, resource requests and agency metrics; optional `random_incident` with an operator token. |
| [implementation/HANDLED-CASES.md](implementation/HANDLED-CASES.md) | Thirteen scenarios the engine handles, with limits. |
| [implementation/validation.md](implementation/validation.md) | Test counts and commands from the last local verification. |
| [implementation/disruptionops-integration.md](implementation/disruptionops-integration.md) | Earlier integration contract. **Partly outdated**: written against 4e87ade; see the migrations doc, section 8 and 9. |
| `implementation/acceptance-results.json`, `browser-results.json` | Recorded results. |
| `implementation/operations-architecture.dot` / `.svg` / `.png` | Architecture diagram of the simulator. |

## Research (`research/100-drone-disaster-response/`)

| Document | One line |
|---|---|
| [research/.../README.md](research/100-drone-disaster-response/README.md) | Entry point to the swarm architecture research. |
| [research/.../architecture-study.md](research/100-drone-disaster-response/architecture-study.md) | How 100+ drone disaster systems work. |
| [research/.../20-drone-decentralized-allocation.md](research/100-drone-disaster-response/20-drone-decentralized-allocation.md) | Decentralised task allocation, drawbacks and fixes. |
| [research/.../repository-integration.md](research/100-drone-disaster-response/repository-integration.md) | Earlier repository findings; DisruptionOps parts predate bd21179. |
| `research/.../sources.md`, `*.dot`, `*.svg`, `drawio/` | Sources and diagrams. |

## Legacy PyBullet exercise guides (`../legacy/pybullet/docs/`)

| Document | One line |
|---|---|
| [../README.md](../README.md) | Project overview and run commands. |
| [AGENTIC-GUIDE.md](../legacy/pybullet/docs/AGENTIC-GUIDE.md), [AI-SWARM-GUIDE.md](../legacy/pybullet/docs/AI-SWARM-GUIDE.md) | Rule agents and optional AI planner for the 12-drone exercise. |
| [DISASTER-GUIDE.md](../legacy/pybullet/docs/DISASTER-GUIDE.md), [DRONES-GUIDE.md](../legacy/pybullet/docs/DRONES-GUIDE.md), [WEB-GUIDE.md](../legacy/pybullet/docs/WEB-GUIDE.md), [FRIEND-GUIDE.md](../legacy/pybullet/docs/FRIEND-GUIDE.md) | Legacy map, setup, web hosting and the old feed spec. |
| [PRODUCTION-ARCHITECTURE.md](../legacy/pybullet/docs/PRODUCTION-ARCHITECTURE.md) | Early production architecture brief. |
| `history/resume.md` | The user's earlier conversation (local only, not committed); source of the stated expectations. |
