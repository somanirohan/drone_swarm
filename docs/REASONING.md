# Swarm reasoning: the onboard decision engine

`swarm_ops/reasoning` decides what the swarm does once DisruptionOps sends an
`agency_request`. It picks drone roles and payloads, which DisruptionOps resource
kinds to request (how many, how urgently), the escalation level and when to
reassess. **No LLM is involved, and nothing here calls the network.** The engine is
pure Python, deterministic for the same inputs and state, and fast enough to run
onboard: about 2 ms per incident (5 ms worst case in the suite), and about 20 ms to
re-plan 14 concurrent incidents together.

```python
from swarm_ops.reasoning import plan_response, plan_all, Reasoner
plan = plan_response(situation, context)        # ResponsePlan dict, or None -> caller's labelled fallback
plans = plan_all([s1, s2, ...], context)         # joint re-optimisation across concurrent incidents
```

`plan_response` is synchronous. It returns `None` only when the guardrails reject the
plan or the inputs are unusable. It never raises.

## Pipeline

| Stage | File | Kind of method |
|---|---|---|
| 1. Situation assessment | `belief.py` | Probabilistic evidence fusion |
| 2. Needs inference | `needs.py` | Physical demand models over the belief, plus the DisruptionOps taxonomy as a prior |
| 3. Response optimisation | `optimizer.py` | Integer program (greedy marginal gain onboard; OR-Tools CP-SAT as an exact cross-check) |
| 4. Drones, escalation, explanations | `planner.py` | Value-of-information and value-of-time scoring, risk with hysteresis, attribution |
| 5. Guardrails | `guardrails.py`, `schema.py` | Invariant checks, repair and rejection |

### 1. Belief (probabilistic)
Every message about an incident counts as a noisy measurement with a source
confidence: the report, its free text, the structured fields (`reported_people`,
`vulnerable`, `injuries`, `hazards_reported`, `callers`, `severity`, `crowd_size`,
`height_m`), each drone briefing, and ground reports. Two independent callers raise
the report's confidence.

* **Counts** (people, injured, critical, vulnerable, immobile) are scalar Kalman
  filters in log(1+n) space, so they are scale-free. Process noise grows between
  observations, so stale evidence counts for less. "At least one" hints, such as
  "trapped" or "elderly", are soft measurements in time order, so a later confident
  observation can overrule them.
* **Hazards** (water, fire, gas, structural, electrical, crowding, debris, heat) are
  fused intensities in [0, 1]. Category priors come from the taxonomy's own hazard id,
  name and keyword table.
* **Trend** comes from a two-state (level, rate) Kalman filter over the threat level.
  Words like "rising" or "receding" count as rate measurements. The output is a trend
  label plus P(worsening).
* **Uncertainty** is the spread of the decision-relevant state relative to the scene
  size. It drives the plan in three places: an assessing drone is launched when its
  value of information exceeds its cost, ground units hold an uncertainty margin
  (`standby`), and reassessment comes sooner.

### 2. Needs (data-driven)
Each capability has a small model over the belief. For example:

* Medical transport is needed by the expected critical casualties plus half of the
  other injured.
* Water rescue is needed by the immobile people times the share of their immobility
  that water explains. Shallow water does not strand people.
* Evacuation is needed by mobile people times the hazard or crowding pressure.
* Fire suppression scales with fire intensity, and gas aggravates it.
* A blocked emergency route makes debris clearance urgent.

Each demand has a mean, a margin (z·sd), a value (people-equivalents of serious harm
avoided) and a time constant. A trend of worsening shortens the time constant. When
no evidence speaks to a capability's drivers, the DisruptionOps
`incident_category_needs` entry for that category stands in as a prior. Categories
with empty needs in the taxonomy, such as `air_quality`, are therefore handled from
evidence alone.

### 3. Optimisation
The objective is to maximise Σ (harm avoided, discounted by each unit's own ETA,
capped at mean plus a partly-valued margin) − Σ unit cost (scarcity-weighted).

* **Cover** for each unit is seats or patients (or work units) × taxonomy
  `effectiveness` × access. Road units barely help in deep water, and boats need water.
* **Constraints:** each unit goes to at most one incident, and only units the fleet
  has can be assigned.
* **Fairness:** all concurrent incidents share one concave objective. Scarce units go
  where the marginal harm avoided per cost is highest.
* **Solvers:**
  * The onboard default is a lazy greedy on marginal gain with a removal pass. It is
    deterministic and takes milliseconds.
  * `params.solver="cpsat"` runs OR-Tools CP-SAT on the same integer-scaled model,
    with one worker, a fixed seed and a deterministic time budget. That is about
    40 ms per incident on average and up to about 150 ms. Use it for offline
    cross-checks: it is optional and not needed onboard.
* **Fleet:** when the caller passes no fleet, the default comes from
  `assets/operations/agency-catalog.json`, using real PCMC unit counts and mean
  approach times. Police and helicopter come from DisruptionOps 032/035.

### 4. Drones, escalation, explanations
* **Drone roles:**
  * **assess:** value of information. This is the uncertainty margin the drone look
    removes, plus any `field_assessment` need.
  * **supply:** people served by the kit × relief rate × how much earlier the drone
    arrives than ground help. No drop is made if an ambulance is minutes away.
  * **overwatch:** risk × P(worsening) × hazard.
  * **relay:** a poor link.
* **Drone constraints:** per-incident cap, energy feasibility (round trip plus time on
  scene plus reserve), and payload stock.
* **Escalation:** the risk is R = P(life threat) × severity factor × trend factor,
  where P(life threat) = 1 − exp(−harm at stake). Escalation goes up immediately and
  steps down only once R is `escalation_hysteresis` below the boundary, so it does not
  flip-flop. Higher levels lower the commitment cost, so more units are committed.
* **Explanations:**
  * `explanation.factors` gives each need's share of the harm at stake, with its
    evidence. `multipliers` gives vulnerability and trend.
  * Every request has a `why` (amount, margin, cover, effectiveness, ETA). When a
    less effective kind is used, the `why` says why the better kind wasn't used, for
    example "fire_engine (substitute for boat: no boat in the fleet)".
* **Idempotent requests:** `qty` is the total the incident should have. `already` and
  `delta` are net of what is already requested, assigned or en route. That comes from
  `situation.committed`, `context.committed[incident_id]`, or `context.resources[*].assigned_to`,
  and otherwise from this reasoner's last plan. A negative delta appears in `stand_down`.
  Engines should apply `requests_delta` and never add per re-plan.

### 5. Guardrails
The guardrails apply these checks:
* Requests for unknown kinds are dropped, and simulator aliases are mapped
  (`rescue`→`rescue_team`, `engineering`→`jcb`).
* Quantities are clamped to availability.
* Capability and kind must agree.
* No boat is sent to a dry scene.
* Drones are trimmed to caps, available aircraft and payload stock.
* The plan is validated against the schema.
* The plan is rejected if a life threat has no response at all.

Violations are listed in `guardrail_violations`.

## Plan shape (excerpt)
```json
{"assessment": {"severity": 5, "trend": "worsening", "key_risks": [...], "unknowns": [...]},
 "drones": [{"role": "assess", "payload": "none", "count": 1, "why": "...", "drone_ids": ["D3"]}],
 "agency_requests": [{"kind": "boat", "qty": 2, "already": 1, "delta": 1, "urgency": "immediate",
                      "capability": "water_rescue", "why": "...", "unit_ids": [], "eta_s": 557, "sim_kind": "boat"}],
 "requests_delta": [...], "stand_down": [...], "public_actions": [...], "escalation": "full_team",
 "reassess_after_s": 90, "confidence": 0.6, "rationale": "...", "explanation": {...}, "belief": {...},
 "engine": "onboard-v1", "fallback": false, "latency_ms": 2.1}
```
`plan_schema()` returns the strict JSON schema.

**Inputs (`context`, all optional):**
* `resources` (units with id, kind, eta_s or position, state, assigned_to), or
  `resource_summary` ({kind: {available, eta_s}})
* `drones` ([{id, payload, stock, energy_frac, eta_s|position}]) and `drone_caps`
  ({per_incident, available_total})
* `concurrent` (other situations to optimise jointly)
* `committed`
* `params` (overrides)
* `taxonomy`

## What is data, optimised, probabilistic, and fixed

| Category | What it covers |
|---|---|
| Data (DisruptionOps) | Resource kinds, capacities and capability effectiveness; category needs, hazards and keywords (`assets/operations/disruptionops-taxonomy.json`, built-in copy as fallback); fleet counts and approach distances (`agency-catalog.json`) |
| Probabilistic | All hidden-state estimates, trend, uncertainty, P(life threat), value of information |
| Optimised | Unit-to-incident assignment and quantities; drone role and payload assignment (greedy on value) |

**Fixed parameters,** all in `params.py` and overridable with `context["params"]`:
* **Priors:** people prior; injured, critical, vulnerable and immobile base rates.
* **Sensor noise and drift:** source confidences, count and hazard noise, drift rates,
  trend-word rate.
* **Harm and time:** harm per person by condition, vulnerability weight, time
  constants per capability, trend factors.
* **Capacity and access:** care per crew, seats on goods trucks, litres per person,
  access depth limits.
* **Costs:** unit cost, scarcity cost, margin value, retention bonus, escalation
  thresholds, hysteresis and cost factors.
* **Drones:** drone cost, speed, endurance, reserve, kit sizes and relief rates.
* **Fallback fleet:** police and helicopter pool.

**Calibration caveat:** these numbers were calibrated by hand against the eval suite
below. They are not fitted to real incident outcomes.

**Fixed lexicons:** the hazard, injury, vulnerability and trend word lists and the
structured hazard tokens (`belief.py`), and the public-action advisory texts
(`planner._public_actions`). These turn words into evidence or into advice text. They
do not choose units.

**No learned model yet.** A risk model trained on simulated scenarios, with frozen
JSON weights, is a possible next step.

## Evals
`python -m swarm_ops.reasoning.evals [--solver greedy|cpsat] [--json]` runs 14
situations and 5 same-category pairs:
* **Situations:** an elderly heat casualty, a rooftop family in rising water, a crowd
  at a full shelter, a chemical fire with casualties, a wall collapse with one person
  trapped, a live wire in a waterlogged lane, a tree blocking ambulances, a camp out
  of water, a scene improving after responders, a worsening flood, an unknown report,
  a mass casualty, a "flood" report on a dry street, and smoke with coughing
  (`air_quality`, which has no taxonomy needs).
* **Pairs:** crowd size, injuries, vulnerability, trend, stranded count.

Current result: **44/44 checks and 5/5 pairs** with both solvers. Tests:
`python -m unittest tests.test_reasoning`.

## Limits
* Single-hop ETAs: an ETA is given or straight-line × detour / speed. There is no road
  routing here; the engine owns routing.
* Units contribute to every capability they have at their incident. The model has no
  task sequencing on scene.
* The text lexicon is English only. DisruptionOps Marathi and Hindi keywords are used
  only for category hazard priors.
* There is no utility or power kind in the taxonomy. Live wires are handled with a
  police cordon and an advisory, and are listed under `unknowns`.
