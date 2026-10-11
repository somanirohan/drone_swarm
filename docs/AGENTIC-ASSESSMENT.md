# Are the agents actually agentic?

Short answer, both systems, read from code on 2026-10-10 (DisruptionOps commit `1697407`, this repo at `HEAD` plus uncommitted work of the same day).

1. **DisruptionOps is agentic in structure, not in the LLM sense.** Its graph is real LangGraph (`backend/app/agents/graph.py`), with checkpoints, a human interrupt, scoped memory and guardrails, but none of the graph nodes calls a model; the decisions are CP-SAT and rules. Exactly one agent is a free-form LLM loop (the Incident Commander), and it is optional and gated.
2. **Our swarm is not agentic.** It is one deterministic simulation object that computes every "agent" decision centrally. There is no LLM, no agent process, no memory beyond state fields, and `_behavior` is a label, not a behaviour tree. It acts like an agency only at the API boundary (it accepts or declines requests by itself, with reasons).
3. **Where `graph.py` is:** `D:\drone_research_reference\DisruptionOps-latest\backend\app\agents\graph.py` (977 lines). It is **not** in this repository.

## 1. DisruptionOps

### 1.1 The graph (`backend/app/agents/graph.py`)

Built by `build()` (line 705) with `StateGraph(GraphState)`; compiled with an in-memory or Postgres checkpointer (`_compiled`, line 774). Nodes, in order, and what drives each:

| Node (line) | Driver | Notes |
|---|---|---|
| `guard_in` (299) | Deterministic | Kill switch and run-storm check; routes to `held`. |
| `triage` (334) | SQL + memory | Worst open severity; recalls standing orders. |
| `command` (354) | Wakes an LLM agent | Calls `commander.nudge` (debounced) when severity >= 5; does not wait for it. |
| `fanout` / `sense` (367/377) | SQL | Four parallel reads via `Send`. |
| `assess` (428) | Arithmetic | Shortfall per capability. |
| `domain` (454) | Rules + memory | Per-domain planner; three short cycles in a row flags mutual aid. |
| `optimise` (476) | **CP-SAT** | `replan.preview`; the number-producer. |
| `validate` (495) | Rules | `guardrails.check_plan`; retries up to 3. |
| `policy_gate` (535) | Rules + memory | Remembers officer rejections for 30 minutes. |
| `human_approval` (601) | **Human** | `interrupt()` (line 604); resumes with `{approved, by, note}`. |
| `dispatch` (638) | Rules | Drift re-check, then writes. |
| `observe` (687) | Deterministic | Summary and event. |

`grep` for `llm` in `graph.py` finds no model call. The module docstring says so implicitly: "the model never produces a number" (`orchestrator.py`, docstring: "The LLM writes prose. Pull the model out entirely and the run still completes").

### 1.2 Where a model is called

Every call goes through `agents/llm.py` (`complete`, line 292): chain Gemini, Bedrock, deterministic fallback; `guard_prompt` and `guard_output` wrap it.

| Call site | Task | Fallback |
|---|---|---|
| `agents/commander.py:229` | Incident Commander tool loop: JSON `{thought, tool, args}`, max 6 steps / 45 s (lines 65-66); must read before `propose_action`; proposals go through the policy gate | Skips the episode entirely (`engine == "fallback"`, line 203); the deterministic planner has already acted |
| `copilot/agent.py:213`, `:1055` | Route a commissioner's question to a tool; narrate tool results | Rule router (`_rule_route`, line 262) |
| `incidents/parse.py:341` | Classify report text into taxonomy categories | Keywords; zero-shot XLM-R |
| `incidents/severity.py:140` | Severity from wording | Category base + corroboration |
| `incidents/clustering.py:298` | Adjudicate borderline duplicate reports | Distance/time rule |
| `api/v1/citizen.py:164` | Answer a resident question | Template |

So the LLM reads language, picks which tool to call, writes prose and proposes. It never allocates, never writes assignments directly, and never overrides the gate.

### 1.3 Memory, guardrails, human approval

- **Memory**: `agents/agent_memory.py`; per-agent namespaces, an access table, a ledger of reads/writes/refusals, TTL and importance. It is used in behaviour: `triage` recalls orders, `domain` detects persistent shortage, `policy_gate` refuses to repeat a rejected move. Commander and Copilot lessons are in `copilot/memory.py` (Supabase, migration 020).
- **Guardrails**: `agents/guardrails.py` input (clean, PII, injection), output schema, grounded numbers, plan invariants, drift. Applied at the LLM gateway and as graph structure (`guard_in`, `validate`, `policy_gate`, `dispatch`).
- **Human approval**: `human_approval` (line 601) is a real LangGraph interrupt with a timeout that counts as rejection; separately the delegation matrix in `agents/policy.py` decides which action classes never need a human. An emergency stop (`ops/autonomy.py`) halts all automation.

### 1.4 Verdict and what would make it more agentic

Verdict: a well-engineered **workflow with agent-shaped parts**. Autonomy is bounded on purpose (docstring of `agents/ml.py`: allocator, gate, trust are arithmetic because "the model said so is not a defensible answer in an inquiry"). Gaps against a stricter "agentic" bar:

1. Only the Commander plans with a model; it runs outside the graph, is debounced, and its proposals are just inputs to the gate. Moving it into the graph as a node that can emit alternative plans (scored by CP-SAT) would close the loop.
2. Sense/domain nodes are not goal-driven; they cannot ask for more information or call tools conditionally.
3. No negotiation with external agents: agency requests (`reports.py:281`) are rows with acknowledge/decline buttons, not a conversation. A message protocol with the swarm (task, accept, decline, report, resource request) is exactly what this round adds on our side.
4. Learning from outcomes is limited to memory notes and the forecast model; there is no evaluation loop that changes policy.

## 2. The swarm (this repository)

### 2.1 What exists

| Piece | Where | Nature |
|---|---|---|
| Accept/decline | `engine._agency_request` (~line 1109) | Rule checks in sequence: bounds, no-fly, 12-incident capacity, launch cap, energy-feasible route. Returns a reason and a plan (list of strings). |
| Allocation | `engine._allocate` (~line 998) | Deterministic: incidents by severity; ground unit chosen as minimum route distance. Docstring says it is a mock orchestrator for ground units. The drone choice is also computed centrally. |
| Routing / energy | `_drone_path` (A* flight grid), `_ground_path`, 0.018 Wh/m model | Classical algorithms; see `ALGORITHMS.md`. |
| "Behaviour tree" | `engine._behavior` (~line 1899) | A **classifier** that maps drone `state` to a label (search, confirm, assess, relay, return, ...). It does not choose actions; `step()` does. Not a behaviour tree in the control sense. |
| Search | `_update_zones`, `_next_search` | Probability-weighted coverage cells; greedy next cell. |
| Briefing / requests | `SwarmNode.report`, `OrchestratorNode.check_capacity` | The swarm reports the situation (hysteresis); the orchestrator decides on seats vs people. |
| Memory | none | Only simulation state; `search_map` probabilities are the nearest thing. |
| LLM | none | Nothing in the swarm calls a model. |

The engine docs already say so: `allocation_mode: "deterministic feasible bids (central simulation harness)"` in the snapshot metrics, and `ALGORITHMS.md` opens with "single-process, centralised harness". Auctions are computed by the engine on behalf of drones, not negotiated between them.

### 2.2 Verdict

It is a **deterministic multi-agent simulation with an agency-shaped interface**. The interface (accept or decline with reasons, report back with machine statuses, ask the orchestrator for ground units, accept handoffs only against the current briefing revision) is genuinely agent-like and tested. The inside is not.

### 2.3 What would make it more agentic (in order of value)

1. **Explicit agent classes with a decision trace** (in progress by the ENGINE work): each drone, the dock/fleet coordinator and the agency liaison as objects that read an observation and return a decision plus a reason, logged in `decisions`. This makes today's logic inspectable even though it stays deterministic.
2. **A real message log** between command center and agents (`messages`: task, accept, decline, report, status, ack, cancel, handoff, situation_report, alert, dispatch, unit_status), so behaviour is observable as a conversation. `/ops/messages` and `swarm_ops/command_center.py` consume it.
3. **Decentralised bidding**: each drone computes its own bid from local state and the coordinator only selects; allows faults and link loss to change outcomes without central code paths. **Update: implemented as an opt-in mode.** With `set_coordination {mode:"decentralized"}` (or `--coordination decentralized`) the swarm is given a mission over the command link and the drones derive the aerial sub-tasks (assess, overwatch, relay, search sectors, own supply drops, handover) and allocate them among themselves with CBBA over a simulated mesh radio; no central award is made (tested). `central` stays the default; `orchestrated` is the dependent mode where DisruptionOps assigns drones and roles. See docs/DECENTRALIZED.md.
4. **Real behaviour trees** (or a utility selector) that pick actions in `step()`, rather than `_behavior` labelling afterwards.
5. **Optional LLM liaison** (same pattern as DO): a model may rewrite briefings, summarise a situation for the commander, or propose a retask, but accept/decline and routing stay deterministic with a fallback and a guardrail layer. Do not let a model change energy or no-fly checks.
6. **Memory**: per-drone and per-sector notes (blocked cells, repeated declines) that change later bids.

## 3. Honest comparison

| | DisruptionOps | Swarm |
|---|---|---|
| Orchestration | LangGraph, checkpoints, interrupt | One `step()` loop |
| Nodes that call a model | 0 in the graph; Commander and 5 language tasks elsewhere | 0 |
| Number-producing core | CP-SAT | Greedy/feasible bids, A* |
| Human in the loop | Real interrupt + delegation matrix + emergency stop | Operator pause/fault controls only |
| Agent memory | Scoped, ledgered, used in decisions | None |
| Guardrails | Layered, tripped and logged | Input validation only |
| Agent boundary | Internal state keys | Public WS protocol with accept/decline and statuses |

## 4. Design update: onboard deterministic reasoning (no LLM in the decision path)

Decided 2026-10-10: **the user rejected LLM-based decisions.** An earlier draft of this section described an LLM planner with guardrails; that design is withdrawn. Sections 2 and 3 above describe the engine before the new layer.

- **Decisions come from an onboard, deterministic, offline system** (`swarm_ops/reasoning`, REASONER work, target under 50 ms per decision): Bayesian situation belief with evidence fusion; needs from DisruptionOps taxonomy data (not per-category rules); CP-SAT or greedy marginal-utility optimisation to pick units and agencies; risk-based escalation with hysteresis. Fixed constants live in one file (`reasoning/params.py`) and are documented as model scales, not as rules. Formal names and honest status per component: [ALGORITHM-GLOSSARY.md](ALGORITHM-GLOSSARY.md).
- **Same inputs, same outputs.** No network, no model, no randomness in the decision path, so a run is replayable and a decision can be explained from its belief and costs.
- **Guardrails stay as hard constraints:** bounds, no-fly zones, energy-feasible route, open-incident and launch caps, schema validation of incoming commands.
- **Any rule-based fallback is labelled** in the decision trace as such, not presented as inference.
- **Command center input.** `swarm_ops/command_center.py` sends `payload.situation` taken from DisruptionOps data (the `pccoe_pawana_night` reports and the migration 029/030 catalog): report text, DisruptionOps category id, ward and place, people and reporter count, `crowd_size`, `vulnerable` groups, `injured_reported`, `hazards` and the three nearest lifelines. Vulnerable groups, injuries and hazards are read from the report text; fields the source does not state are omitted. These are the observations the belief fuses.
- **Allocating from situation reports.** In orchestrator mode the command center plans from the swarm's `situation_report`s with the stand-in planner (`AllocationPlanner`) and commits the nearest free unit of the matching kind from `assets/operations/agency-catalog.json` (the swarm's snapshot position wins when it places that unit), never twice, with a labelled synthetic unit only if none is free. That is nearest-neighbour dispatch, a rule, and it is named so in the glossary.
- **Still true:** the command center is deterministic and replays a scenario script; it does not decide tasks itself.
- **Compared with DisruptionOps:** DO keeps the allocator deterministic (CP-SAT) and uses a model only for language and the optional Commander. The swarm now matches that spirit more strongly by having no model at all in the decision path.


## Multi-node design (current)

The swarm is one node among several, all running concurrently and talking only through bus messages (`swarm_ops/nodes/`):

| node | owns | decides | emits |
|---|---|---|---|
| `SwarmNode` | the drones | accept/decline DRONE work, which drone, role, dropping its own onboard supplies | `situation_report`, `alert`, `accept`/`decline` |
| `OrchestratorNode` (id `command_center`) | the plan (stand-in for DisruptionOps) | which unit kinds and how many, from situation reports (belief -> needs -> optimizer -> planner; labelled fallback) | `dispatch`, `cancel` |
| `AmbulanceService`, `FireService`, `RescueService` (rescue + boats), `PoliceService`, `PublicWorks` (pump/JCB, tankers, trucks, buses) | their units | which of ITS units answers a dispatch; nearest-unit takeover | `unit_status`, `handoff`, `decline` |

The engine only simulates world physics (movement on roads, water and air) and exposes a small world port to the nodes. The swarm never says how many ambulances are needed: it says what it sees (people, injuries, hazards, access, "PC-AMB-06 ambulance 400 m away, en route") and DisruptionOps (or the stand-in) allocates. With DisruptionOps connected the `OrchestratorNode` is disabled and DisruptionOps dispatches through `orchestrate_incident`/`assign`/`external_resource_update`. Agency nodes can run in-process (default), as asyncio tasks (live server) or as separate WebSocket processes. Honest limits: the stand-in planner is the same simulation-grade reasoning package as before; the engine still advances all units' physics in one loop; node cadence is simulated seconds.
