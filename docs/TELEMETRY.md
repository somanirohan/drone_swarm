# Telemetry and logging

Goal: continuous, good data. Everything is derived from the server's tick snapshot, so engine internals can change freely.

## What is logged where

| Where | What |
|---|---|
| `.cache/logs/app.jsonl` (rotating 5 MB x5) | JSON lines: `ts, level, logger, run_id, sim_t, event, msg, fields`. `LOG_LEVEL` env (default INFO); `LOG_FORMAT=pretty\|json` for the console. |
| `.cache/logs/audit.jsonl` | Command audit: `action, role (operator/integration/view), command_id, payload summary, ok, error, latency_ms, incident_id`. |
| app log, logger `swarm.http` | `ws.connect`, `ws.disconnect`, `http.request` (path only, role, remote host). |
| `.cache/runs/<started>_<run_id>/` | Run recorder datasets (below); a scenario reset starts a new run. |

Secrets never reach logs: registered token values, `token=`/`control=`/`Bearer` patterns, `sk-` keys and fields named token/secret/password/authorization/api_key are masked; query strings are never logged.

## Run directory

`manifest.json` (git commit + dirty flag, scenario, world, seed, fleet, dispatch caps, non-secret env config, versions, schema versions, counts, data-quality counters, overhead), `<dataset>.jsonl` (live), `<dataset>.<firstseq>.jsonl.gz` (rotated at 8 MB), `_rejects.jsonl` (records that failed validation, capped).
Bounds: 250 MB per run (oldest `drones` parts trimmed first, `manifest.truncated=true`); 30 runs / 2 GB total (oldest deleted when a new run starts). `OPS_TELEMETRY=0` disables recording.

Non-blocking: `Telemetry.observe()` does O(1) checks and a queue put on the tick (about 0.01 ms mean); validation, dedupe and disk I/O run on a writer thread. A full queue drops records and counts them (`dropped_records`).

## Data quality

Every record has the envelope below. `seq` is per dataset (1-based) and is the paging cursor. Ids are consistent across datasets: `run_id`, `incident_id`, `drone_id`, `resource_id`, `request_id`. `sim_t` never decreases inside a dataset (regressions are clamped and counted). events/signals/messages/decisions/incident stages are de-duplicated by `id`. Invalid records are not written; they are counted in `manifest.quality.rejected` and sampled in `_rejects.jsonl`.

## Access (operator or integration token; view-only links get 403)

Send `Authorization: Bearer <token>` (or `?token=`).

- `GET /ops/runs` list runs + recorder stats
- `GET /ops/runs/<run_id>/manifest`
- `GET /ops/runs/<run_id>/<dataset>?after=<seq>&limit=<=5000>&format=json|ndjson|csv`; poll with `next_after` for a live feed.
- `GET /ops/telemetry` queue depth, drops, overhead.

## Analysis

```
python tools/analyze_run.py [run_dir_or_id]     # <run>/analysis/summary.md + summary.json (+ kpis.png if matplotlib works)
scripts/export-run.ps1 [-Run <id>] [-Out dir]   # CSV per dataset
```

## Data dictionary

### Envelope (all datasets)

| field | type | description |
|---|---|---|
| `v` | int | Dataset schema version of this record (see manifest.schemas). |
| `seq` | int | Per-dataset sequence number, 1-based, strictly increasing. Use as the ?after= cursor. |
| `run_id` | str | Simulation run id (matches engine.run_id and /ops/events run_id). |
| `sim_t` | float | Simulation seconds at which the recorder observed the record (non-decreasing within a dataset). |
| `wall_time` | str | UTC ISO-8601 wall clock when the record was observed. |
| `wall_ts` | float | Same instant as unix epoch seconds. |

### `events` (schema v1)

Engine events (incident, dispatch, briefing, handoff, resolved, faults...), one per engine event id.

| field | type | required | description |
|---|---|---|---|
| `id` | str | yes | Engine event id "<run_id>:<n>" (dedupe key). |
| `kind` | str | yes | Event kind, e.g. incident, drone_dispatch, drone_on_scene, briefing, resolved. |
| `message` | str |  | Human readable message. |
| `event_t` | int/float |  | Sim time the engine stamped on the event. |
| `incident_id` | str |  | Incident the event concerns. |
| `request_id` | str |  | Orchestrator request id when linked. |
| `status` | str |  | Orchestrator status (accepted, launched, on_scene, ...). |
| `data` | dict/null |  | Structured event payload (drone_id, unit_id, request_id...). |

### `decisions` (schema v1)

Onboard/central decisions with inputs and factors when the engine provides them; unknown keys land in detail.

| field | type | required | description |
|---|---|---|---|
| `id` | str | yes | Decision id (engine id, or generated "<run_id>:dec:<n>"). |
| `agent` | str |  | Deciding agent id (drone, resource or "command"). |
| `kind` | str |  | Decision type (assign, retask, return, hold, supply...). |
| `summary` | str |  | What was decided / why, one line. |
| `incident_id` | str |  |  |
| `drone_id` | str |  |  |
| `resource_id` | str |  |  |
| `request_id` | str |  |  |
| `source` | str |  | rule | llm | fallback | operator ... |
| `fallback` | bool/null |  | True when the primary reasoner failed and a rule fallback was used. |
| `latency_ms` | int/float/null |  | Reasoning latency. |
| `inputs` | any |  | Inputs the decision saw. |
| `factors` | any |  | Scored factors / alternatives. |
| `decision_t` | int/float/null |  | Sim time the engine stamped. |
| `detail` | any |  | Remaining engine fields, verbatim. |

### `messages` (schema v1)

Command-center <-> agent messages.

| field | type | required | description |
|---|---|---|---|
| `id` | str | yes | Message id (engine id or generated). |
| `sender` | str |  | Sender id/role. |
| `recipient` | any |  | Recipient id/role or list. |
| `kind` | str |  | Message type/channel. |
| `text` | str |  | Message body. |
| `incident_id` | str |  |  |
| `request_id` | str |  |  |
| `message_t` | int/float/null |  | Sim time the engine stamped. |
| `detail` | any |  | Remaining engine fields, verbatim. |

### `signals` (schema v1)

Communication pulses (SOS, resource requests, relays) shown in the viewer.

| field | type | required | description |
|---|---|---|---|
| `id` | str | yes | Signal id "<run_id>:sig:<n>". |
| `kind` | str | yes | sos, resource_request, ... |
| `from_type` | str |  |  |
| `from_id` | any |  |  |
| `to_ids` | any |  |  |
| `position` | any |  | [x, y, z] metres in the local frame. |
| `incident_id` | any |  |  |
| `text` | str |  |  |
| `signal_t` | int/float |  | Sim time of the pulse. |

### `incidents` (schema v1)

Incident lifecycle: one record per stage transition.

| field | type | required | description |
|---|---|---|---|
| `id` | str | yes | "<incident_id>:<stage>[:<qualifier>]" (dedupe key). |
| `incident_id` | str | yes |  |
| `stage` | str | yes | created | accepted | drone_on_scene | first_on_scene | briefing | resource_requested | resource_fulfilled | supplied | handoff | resolved |
| `occurred_t` | int/float/null |  | Sim time the stage actually happened (sim_t is when it was observed). |
| `category` | str |  |  |
| `severity` | int/float/null |  |  |
| `people` | int/float/null |  |  |
| `request_id` | any |  | Orchestrator/resource request id. |
| `drone_id` | any |  |  |
| `resource_id` | any |  |  |
| `first_on_scene` | any |  | drone | ground (on first_on_scene stage). |
| `time_since_created_s` | int/float/null |  | occurred_t - created_at. |
| `detail` | any |  | Stage specific extras. |

### `resources` (schema v1)

Ground unit lifecycle: one record per state or assignment change (dispatch, ETA, arrival, service, return).

| field | type | required | description |
|---|---|---|---|
| `resource_id` | str | yes |  |
| `kind` | str |  | ambulance, rescue, fire_engine ... |
| `transition` | str | yes | dispatch | state_change | released |
| `from_state` | any |  |  |
| `to_state` | str | yes | available, busy, enroute, on_scene, awaiting_handoff, returning... |
| `incident_id` | any |  |  |
| `eta` | int/float/null |  | Engine ETA value (seconds) when known. |
| `position` | any |  | [x, y, z] |
| `agency` | any |  |  |
| `load` | any |  | People carried. |

### `drones` (schema v1)

Drone telemetry time series, default 1 Hz of sim time per drone.

| field | type | required | description |
|---|---|---|---|
| `drone_id` | str | yes |  |
| `x` | int/float | yes | Local metres east. |
| `y` | int/float | yes | Local metres north. |
| `alt` | int/float | yes | Altitude metres. |
| `speed_mps` | int/float |  |  |
| `heading` | int/float/null |  | Degrees/radians as engine reports. |
| `battery` | int/float |  | 0..1 state of charge. |
| `state` | str |  | Engine state (ready, enroute, observing...). |
| `activity` | str |  | transit, delivering, scanning, assessing, returning, docked, charging, holding, failed. |
| `behavior` | str |  | search, confirm, assess, relay, return, charge, idle, hold, failed. |
| `task_id` | any |  | Incident id the drone serves. |
| `zone_n` | int/float |  | Cells in its search zone. |
| `link` | any |  | Communications link up. |
| `active` | any |  |  |
| `payload` | any |  |  |
| `payload_stock` | any |  |  |
| `dock_id` | any |  |  |
| `target` | any |  | {type,id} |

### `ground` (schema v1)

Ground unit time series (default every 2 s of sim time per unit).

| field | type | required | description |
|---|---|---|---|
| `resource_id` | str | yes |  |
| `kind` | str |  |  |
| `state` | str |  |  |
| `x` | int/float | yes | Local metres east. |
| `y` | int/float | yes | Local metres north. |
| `incident_id` | any |  |  |
| `eta` | int/float/null |  |  |
| `transport` | any |  | road | boat |
| `load` | any |  |  |
| `agency` | any |  |  |

### `kpis` (schema v1)

KPI snapshot every simulated minute (and at run end).

| field | type | required | description |
|---|---|---|---|
| `minute` | int/float | yes | Simulated minute index (final record may repeat the last minute with final=true). |
| `final` | bool |  | True on the end-of-run record. |
| `open_incidents` | int/float | yes |  |
| `resolved_incidents` | int/float | yes |  |
| `total_incidents` | int/float |  |  |
| `airborne` | int/float | yes | Drones above 1 m. |
| `active_drones` | int/float |  |  |
| `drone_utilisation` | int/float | yes | Share of active drones not docked/charging (0..1). |
| `ground_utilisation` | int/float |  | Share of ground units not available (0..1). |
| `coverage_pct` | int/float/null |  | Search patches observed / total. |
| `mean_time_to_first_on_scene_s` | int/float/null |  | Mean (first drone on scene - created) over incidents with a drone arrival. |
| `mean_briefing_latency_s` | int/float/null |  | Mean (briefed_at - created_at). |
| `unmet_needs` | int/float |  | Open-incident needs not yet supplied. |
| `open_resource_requests` | int/float |  |  |
| `drone_first` | int/float |  |  |
| `ground_first` | int/float |  |  |
| `faults` | int/float |  |  |
| `decisions` | int/float |  | Decisions recorded so far. |
| `fallbacks` | int/float |  | Fallback decisions so far. |
| `dropped_records` | int/float |  | Recorder queue drops so far (data-quality signal). |
