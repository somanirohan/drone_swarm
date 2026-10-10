# DisruptionOps migrations 001-029, interpreted for the drone swarm

Read on 2026-10-10 from the read-only clone `D:\drone_research_reference\DisruptionOps-latest` at commit **bd21179** (29 migrations). The older clone `DisruptionOps` (commit `4e87ade`, 28 migrations) is **stale**; the earlier patch and docs were written against it. `git diff 4e87ade HEAD --stat` shows 15 files, all additive: migration `029_pcmc_pccoe_zone.sql`, a new scenario `pccoe_pawana_night` in `backend/app/demo/scenarios.py`, `backend/ml/*` (district constants, OSM/ERA5/DEM fetchers) and `data/raw/*` (OSM roads, water, context; ERA5 meteo), plus two `.tgz` in `Claude outputs/`. No migration 001-028 changed and no API file changed. Every claim below was checked against the SQL or Python named next to it. Anything I could not verify is marked **UNVERIFIED**.

## 0. Read this first

1. **The migrations are not a complete schema.** `001` runs `alter table wards ...` and `002` alters `incidents`, `citizen_reports`, `resources`, `lifelines`. No file in `backend/migrations` creates those tables, or `assignments`, `field_tasks`, `decisions`, `policy_clauses`, `profiles`, `incident_needs`, `field_status_kinds`, `field_reports`, `road_blocks`, `app.is_staff()`, `app.my_city()`. They come from a baseline that lives only in the hosted Supabase project `Indradhanu`. The migrations README says "run 001 to 003 against a fresh database"; that cannot work from this repo alone. **UNVERIFIED:** the exact baseline columns and the enum values of `assignment_status`, `task_status`, `resource_status` (the Python code uses `proposed, approved, en_route, on_site` for assignments, `replan.py:45`).
2. **The repo numbering and the Supabase history differ.** Migration 015 says it was applied as Supabase migration `013_reporter_key_...`. Our proposed `029` would be a repo number only.
3. **The migrations README stops at 026.** `027` (sensor nodes) and `028` (leases) exist but are not in its table.
4. **Taxonomy is data, lifecycle is type.** The one rule from the README: which hazards, categories, resource kinds, capabilities and agencies exist is rows. Status columns stay Postgres enums. This is the rule our integration must follow: register the swarm with INSERTs, not with schema changes.

## 1. One row per migration

| # | File | What it changes | Why (from the file's own comments) | What it means for the swarm |
|---|---|---|---|---|
| 001 | portability_taxonomy_as_data | Creates `cities`, `hazard_types`, `capabilities`, `resource_kinds`, `resource_kind_capabilities` (with `effectiveness` 0-1), `lifeline_kinds`, `incident_categories` (dedup radius/window, base severity, `life_safety`), `incident_category_needs`. Seeds Pune, 5 hazards, 8 capabilities, 7 resource kinds, 8 categories. | A new city or disaster must not need a migration and redeploy. | This is the registration surface. A `drone` kind and new capabilities are INSERTs here. |
| 002 | convert_taxonomy_enums_to_fks | Turns hazard/category/kind enum columns into text FKs, drops the old enums, rebuilds two views. Not idempotent. | Enums cannot be extended without a migration. | Confirms category and kind ids are free text with FKs: unknown ids fail loudly (`UnknownTaxonomyValue`, `taxonomy.py`). |
| 003 | cities_agencies_events_simruns | Adds `city_id` to wards/lifelines/resources/policy_clauses. Creates `agencies` + `agency_capabilities`, `resources.agency_id`, `sim_runs` (mode sim/replay, `clock_*`, `speed`, `seed`), nullable `sim_run_id` on 9 tables, and the append-only `events` table with `causation_id`, plus a trigger that raises on UPDATE/DELETE. RLS: reference data public read, `agency_capabilities`/`sim_runs`/`events` staff read. | One audit log, replan trigger and replay tape. A simulation must never contaminate live ops and must be deletable whole. | The swarm's actions should become `events` rows. The swarm itself can be an agency. A swarm run can be a `sim_runs` row (with caveats in section 4). |
| 004 | hot_path_indexes | Indexes on assignments, ward_risks, events, incidents. | Console polls and replans were timing out. | None directly. Confirms the planner re-plans in a transaction on every relevant event, so noisy writers hurt. |
| 005 | streets_routes_and_learned_inputs | `assignments.route` (LineString), `route_engine`, `progress`, `steps`; `incidents.street`, `citizen_reports.street`. | Units moved in straight lines; hazards were not on roads. | Ground units move along road geometry with `progress` 0..1. A drone flying a straight line does not fit this model (see section 5). |
| 006 | relief_logistics | New lifeline kinds (relief_centre, water_point, medical_camp, food_kitchen), capabilities `supply_delivery`, `water_supply`, kinds `supply_truck`, `water_tanker`, bus gets `supply_delivery` 0.45, category `supply_shortage` (need `supply_delivery`), `lifelines.supplies` jsonb, `people_served_per_hour`, `opened_at`. | Relief supplies were not modelled. | `supply_delivery` and `supply_shortage` already exist: drone supply drops map to them. A drone is a low-capacity supplier, so effectiveness must be low. |
| 007 | public_facing_lifeline_kinds | `lifeline_kinds.serves_public`. | Stop hard-coding which kinds the public may be sent to. | None. |
| 008 | opening_state_baselines | `lifelines.supplies_baseline`, `occupancy_baseline`; sets `resources.base_location = location` where null. | A demo you cannot rewind gives one take. `POST /demo/reset` restores baselines. | A swarm run needs the same discipline: a recorded opening state per run (our `seed` + scenario id). Drone docks are the analogue of `base_location`. |
| 009 | command_copilot | `decisions.params` jsonb; adds `reallocate_unit` to clause `pol-2`; new clause `pol-8` "request_mutual_aid", `max_severity_without_escalation = 0` (always waits for a person). | A decision must record what it acts on. Asking another organisation for units is never automatic. | If the swarm is a separate organisation (agency), asking it for help falls under `pol-8`: a person must approve. |
| 010 | reporter_outcomes | `citizen_reports.outcome` (confirmed/false, set only by a person), note/by/at; rebuilds `reporter_reliability` view. | Trust was circular; a human verdict is the only ground truth. | A confirmed drone sighting can be marked `outcome = confirmed` by an officer. That raises the drone reporter's reliability. |
| 011 | city_scoped_tenancy | `profiles.city_id`, `app.my_city()`; city-scoped read policies on `resources`, `agency_requests`, `events`. | Staff-only operational data must be scoped by city. | The swarm's service identity needs a city. Event reads are filtered by it. Only 3 tables are city-scoped by RLS; the rest rely on `is_staff()` and the API. |
| 012 | shelter_occupancy | Category `shelter_full` (need `mass_transport`), `occupancy_baseline` backfill, `lifelines_kind_room_idx`. | Occupancy was never modelled. | Shelters are real rows the swarm's map should show. No new swarm concept. |
| 013 | remove_mesh_transport | Drops `citizen_reports.mesh_hops`. | A column for a channel that did not exist. | History only. 022 adds the real mesh. |
| 014 | harden_rls_and_grants | Drops `reports_insert_any`; revokes insert/update/delete from `anon`/`authenticated` on all public tables; `security_invoker` on 3 views; tightens `field_reports_read`; fixes duplicate indexes; adds `events_causation_idx`. | The anon key could write fabricated reports underneath the intake pipeline. | **The API is the only door.** The swarm must never write tables directly; it goes through intake, mesh or iot endpoints. |
| 015 | reporter_key | `citizen_reports.reporter_key` ('user:<uuid>' or 'device:<id>'); rebuilds `reporter_reliability` by key. | Anonymous reporters never built a history. | The swarm reports as `device:sensor:drone-swarm` (used in `drone/swarm.py`) so its reliability can be learned. |
| 016 | persist_photo_evidence | `citizen_reports.photo_evidence` jsonb, `photo_agreement`, indexes. | Vision readings were thrown away. | A drone's image reading could be stored here via intake. Intake accepts `photo_evidence`. |
| 017 | return_a_unit_to_service | Inserts `field_status_kinds` row `back_in_service`; clears stale `unavailable_reason`. | A broken-down unit could never return. | Model for a drone coming back after a fault: a status kind, not a new column. |
| 018 | fire_and_the_unknown_report | Categories `fire` (needs `fire_suppression` + `medical_transport`, severity 5) and `unknown_report` (no needs at the time). | A reported fire was classified as a flooded road and would have got a pump. | Our `fire` scenario maps directly to `fire`. |
| 019 | keywords_as_data | `incident_category_keywords` (phrase, weight) seeded with ~140 phrases; categories `air_quality` (no needs), `earthquake_damage` (needs `search_rescue`, `medical_transport`). | Adding a hazard should be configuration. | Free-text classification is data. A drone sends a category id, so keywords are not needed, but a text note still passes through parsing. |
| 020 | agent_memory | `copilot_turns`, `agent_memory` (scopes standing_order/fact/lesson/episode/preference, tsvector), `recall_memory()`, optional pgvector column. | The Copilot had no memory. | A standing order such as "hold Boat 2 in Kothrud" is stored here and linked to an override row. A swarm-related standing order would live here. |
| 021 | operations_and_overrides | Adds `cancelled` to `task_status` and `assignment_status`; creates `operator_overrides` (kind pin/hold/forbid, `resource_id`, `incident_id`, reason, `expires_at`, `active`); adds `cancel_assignment`, `hold_unit`, `pin_unit` to clause `pol-2`. | The planner undid an officer's move six seconds later. | The swarm must obey the same three verbs for any drone that DisruptionOps knows as a resource (section 6). |
| 022 | mesh_transport | `mesh_messages` (unique `(packet_id, type)`), `mesh_outbox` (pending/sent/acked/expired, `source_event_id` unique), `mesh_nodes` (kind camera/gateway/phone/sensor, lat/lon, `last_seen`, `meta`), `mesh_state`. | Offline mesh transport for real. | `mesh_nodes` is where drone positions are published today (`drone/swarm.py` writes kind `drone`, which the column comment does not list but the column allows). `mesh_outbox` is an existing pull channel to devices. |
| 023 | help_requests_get_a_responder | Capability `field_assessment`; provided by rescue_team 1.0, ambulance 0.8, fire_engine 0.8, boat 0.6; `unknown_report` now needs `field_assessment`; more "help" keywords; backfills `incident_needs`. | An unclassified call for help dispatched nobody. | **This is the drone's natural slot:** "go and look at an unknown report". It also makes the earlier docs wrong that said `unknown_report` has no needs. |
| 024 | severity_reading_and_agent_memory_scopes | `citizen_reports.assessed_severity` + `severity_assessment`; `agent_memory.namespace`; `agent_memory_ledger`. | Severity should come from the words; agents get namespaced memory. | None directly. |
| 025 | control_state | `control_state(key, value jsonb, updated_at)`. First key `autonomy` = `{paused, by, reason, since}`. | The emergency stop must survive a restart. | The pause flag the swarm must honour (section 6). |
| 026 | ghaziabad_region | 3 agencies (gmc, upfs, ndrf8), 14 wards (Voronoi), 45 resources, 8 lifelines in Ghaziabad. **All stored under `city_id = 'pune'`.** Re-homes reports within 30 km of (77.38, 28.66) to Ghaziabad wards. | Reports from IPEC Sahibabad snapped to a Pune ward 1,200 km away. | A region is not a tenant here. `city_id` is the deployment id. Before 029 the swarm demo area (18.65 N, 73.76 E) was 11.4 km from the nearest ward; 029 fixes that the same way 026 did. |
| 027 | iot_sensor_nodes | `sensor_nodes` (baseline, state, `simulated`, rssi/snr, escalation memory), `sensor_readings` (mq2, mq135, temp, tilt, gyro, vib, mic, piezo, pir, plus derived `human`, `structural`, `environmental`, `overall`, `flags`, `evidence`). | LoRa field nodes post readings every ~2 s. | A drone with a thermal or gas payload could be a sensor node. The channel set is fixed to Arduino hardware; anything else goes in `extra`. |
| 028 | scale_leases_and_llm_usage | `service_leases(name, holder, expires_at, meta)`, `llm_usage_minutely`. | With N replicas every one re-plans the same change. The lease makes one replica the event router. | Re-plan happens on one leader. The swarm must not assume a specific replica answers. Our own server needs a similar rule if ever run twice (it is single-process today). |
| 029 | pcmc_pccoe_zone | **The zone of our simulator.** Agencies `pcmc`, `pcmcfire`; 14 wards `w-pc-01..14` (Voronoi, envelope 73.715-73.845 E, 18.585-18.712 N; ward `w-pc-01` is "Akurdi-Pradhikaran (PCCOE)" centred exactly on 18.6527, 73.7624); 32 resources `PC-FIRE-01..07`, `PC-AMB-01..06`, `PC-BOAT-01..03`, `PC-TEAM-01..03`, `PC-PUMP-01..05`, `PC-JCB-01..02`, `PC-TANK-01..02`, `PC-TRUCK-01..02`, `PC-BUS-01..02` at mapped station coordinates; 16 lifelines (6 hospitals, 6 shelters, 2 relief centres, 1 medical camp, 1 water point); UPDATEs that re-home existing reports and incidents inside the envelope. Everything under `city_id = 'pune'`. Populations and elevations are labelled planning estimates. No new taxonomy rows. | PCCOE was 11.4 km from the nearest ward (Aundh), so campus reports were planned with Aundh's units. | Our anchor (18.6526632, 73.7624378) is now inside a real ward and has real local units. The simulator's agency catalog should come from this migration (see section 10). **No `drone` kind, no aerial capability** is added. |

## 2. The model, synthesised

```
cities
  |-- agencies --< agency_capabilities >-- capabilities
  |-- resources (kind, agency_id, city_id, location, base_location, status)
  |      \-- resource_kinds --< resource_kind_capabilities(effectiveness) >-- capabilities
  |-- incidents (category, hazard, sim_run_id) --< incident_needs(capability_id, required)
  |      \-- incident_categories --< incident_category_needs(capability_id, qty) >-- capabilities
  |      \-- incident_category_keywords (phrase, weight)
  |-- assignments (resource -> incident, route, progress, status, sim_run_id)
  |-- events  (append-only; kind, actor, subject_type/id, payload, causation_id, sim_run_id)
  |-- sim_runs (seed, clock, mode, status)
  |-- operator_overrides (pin | hold | forbid) , control_state ('autonomy')
  |-- mesh_messages / mesh_outbox / mesh_nodes / mesh_state
  |-- sensor_nodes -- sensor_readings
  \-- service_leases
```

How the pieces behave, as read in code:

- **Capabilities connect need to supply.** A category lists needs (`incident_category_needs`). Planning creates `incident_needs` rows per incident. The solver matches a need to a unit by `resource_kind_capabilities` and divides ETA by `effectiveness` (`solver/allocation.py:168`). A new unit type needs only rows, no solver change.
- **Travel time is a road-graph quantity for every unit.** `allocate()` takes a `TravelMatrix` built from OSRM/Mapbox or a haversine fallback (`solver/routing.py`). There is no per-kind flight mode. See section 5 for the consequence.
- **Events are the spine.** `events.append()` writes inside the caller's transaction. Actors follow `agent:*`, `officer:*`, `citizen:*`, `feed:*`, `sensor:*`, `field:*`, `sim`, `system`. The event router wakes the planner on kinds `incident.opened`, `incident.severity_changed`, `incident.resolved`, `road.blocked`, `road.cleared`, `resource.status_changed` and any `field.*` or `override.*` (`event_router.py:41-54`). A `drone.*` kind is stored and shown but does **not** wake the planner by itself.
- **`sim_run_id` is the scope.** NULL means live. SimClock vs WallClock (`world/clock.py`) is what sets it. Mesh outbound only reads events where `sim_run_id is null` (`mesh/service.py`), so a simulation never leaks to real devices.
- **Overrides are read on every solve** (`replan.py:97-125`): `pin`/`hold` per resource, `forbid` per (resource, incident). They expire on their own.
- **Emergency stop** (`ops/autonomy.py`): while paused the event router counts but does not re-plan, the LangGraph cycle and Commander do not run, the policy gate waits for a person on every decision. It does **not** recall crews already out. The flag is in memory and in `control_state`. It is readable only through the staff endpoint `GET /api/v1/ops/autonomy`.
- **Mesh.** Inbound packet types R (report), S (sensor), F (field status), H (heartbeat), K (ack). Outbound A (alert), D (dispatch), C (cancel), B (block). Gateways authenticate with `X-Mesh-Gateway-Key`. Outbound rows are generated from events `alert.issued`, `assignment.created/changed/cancelled/rerouted`, `road.blocked`.
- **Trust.** Source credibility: field 0.95, agency 0.92, sensor 0.88, phone 0.70, mesh 0.66, app 0.62, sim 0.62, mesh_unsigned 0.45 (`incidents/trust.py:37`). A drone sighting as `sensor` gets 0.88; as `sim` 0.62.
- **Tenancy.** RLS is `app.is_staff()` almost everywhere; city scope only on `resources`, `agency_requests`, `events`. After 014 and 020+ the pattern for new tables is: enable RLS, revoke all from anon/authenticated, backend connects as owner.
- **Staff roles:** citizen, field_operator, ward_officer, commissioner, admin. STAFF = ward_officer, commissioner, admin (`core/security.py:35-43`). JWT from Supabase, role from `profiles.role`.

## 3. Concepts the swarm should plug into

| Swarm concept | DisruptionOps concept | Table / row | Notes |
|---|---|---|---|
| Swarm as an organisation | `agencies` | `agencies('swarm', ...)` + `agency_capabilities` | Lets Copilot and `request_mutual_aid` name it. Clause `pol-8` then forces a person to approve a request. |
| Drone type | `resource_kinds` | `resource_kinds('drone', ...)` | Inert until a `resources` row of that kind exists. |
| What a drone can do | `capabilities`, `resource_kind_capabilities` | new `aerial_assessment`, `aerial_supply_drop`; reuse `field_assessment`, `supply_delivery` | Effectiveness must be low (see seed). |
| "Drone saw a person" | `intake.receive(source='sensor')` | `citizen_reports`, `incidents` | Already used by `drone/swarm.py`. Gets trust, dedup, severity and the Commander nudge for free. |
| "Drone sees a blocked bridge" | `road_blocks` + event `road.blocked` | `road_blocks`, `events` | Already used (max 2 per run, 20 min TTL). Wakes the planner. |
| "Supplies landed" | event on the incident | `events(kind='drone.payload_delivered')` | Already used. Does not reduce `incident_needs`; a person or later logic must do that. |
| Drone positions | `mesh_nodes` kind `drone` | `mesh_nodes(id='swarm:D01', meta=...)` | Already used, every 5 s. |
| Drone payload sensor | `sensor_nodes` / `sensor_readings` | optional | Only if a drone carries gas, heat or sound sensors. Use `simulated = true`. |
| Mission assigned to the swarm | not present | see call contract | No existing inbound command to the swarm. Two options in the call-contract doc. |
| Emergency stop | `control_state['autonomy']` | readable via `GET /ops/autonomy` | Swarm must poll it. |
| Hold / pin / forbid | `operator_overrides` | per `resource_id` | Only meaningful if drones are `resources` rows. |
| One run | `sim_runs` + `sim_run_id` | optional | Needs care: see section 4. |

## 4. Isolation: what `sim_run_id` does and does not protect

`003` scopes 9 tables by `sim_run_id`, but `resources` is **not** one of them. A re-plan with a simulated clock still updates shared `resources` rows. The earlier docs already flagged this (`docs/implementation/disruptionops-integration.md`). Consequence: any drone registered as a `resources` row is shared between live and simulated scopes. Keep drone integration in a separate demo database until upstream resource scoping exists.

## 5. Why not simply register drones as `resources`

Verified: `allocate()` prices every unit by a road-graph travel matrix, and `assignments.route` is a road LineString with `progress`. A `drone` row in `resources` would be routed along streets, in the haversine fallback at the flood-event speed, and the swarm would be told "go there" by an allocator that does not know the drone flies, has a battery, or can only carry one payload. Also, if `aerial_assessment` were added to a category's needs with no drone resource present, the planner would record `demand.uncovered` for it, because nothing could cover it (what downstream reacts to that event: UNVERIFIED).

Recommended order:

1. **Step A (inert, safe):** insert taxonomy rows and the `swarm` agency (section 7). Nothing changes in planning.
2. **Step B (events only):** the swarm reports through the existing doors (intake, `road_blocks`, `mesh_nodes`, `events`). This is what `drone/swarm.py` already does for the old PyBullet feed.
3. **Step C (only after an upstream change):** register real drone `resources` with `base_location` = dock, and add per-kind travel mode to the matrix. Then add the `aerial_assessment` need to `unknown_report`.

## 6. What the swarm must honour from DisruptionOps

| Signal | Where it is read | Swarm behaviour |
|---|---|---|
| `control_state.autonomy.paused = true` | `GET /api/v1/ops/autonomy` (staff token) | Stop launching new missions and stop accepting new assignments. Do not recall drones already airborne unless told: DisruptionOps itself does not recall crews, and recalling is a human decision. Keep reporting observations (they are sensing, not acting). Show "AUTONOMY PAUSED by X" in the viewer. |
| `operator_overrides` kind `hold` on a drone | `GET` not exposed; only via `replan.load_overrides` internally | If drones are resources: the drone is out of the plan until `expires_at`. Stay docked or loiter at base. **UNVERIFIED:** no staff endpoint lists overrides; `/ops` lists active assignments and `/ops/overrides/{id}/lift` lifts one. A new read endpoint is needed. |
| `pin` | same | Keep the drone on its current incident; do not retask or recall. |
| `forbid (drone, incident)` | same | Never assign that drone to that incident again. |
| Assignment `cancelled` / `assignment.cancelled` event | `events`, mesh outbox C packet | Return the drone to base. |

## 7. Data-only seed proposal (NOT APPLIED)

This is a proposal. Do not run it against the live Supabase project. It matches the style of 006 and 023: `insert ... on conflict do nothing`, no schema change.

```sql
-- PROPOSAL ONLY. NOT APPLIED. Target: a separate demo database.
-- Pattern copied from 006_relief_logistics.sql and 023_help_requests_get_a_responder.sql.
begin;

insert into capabilities (id, label, description) values
  ('aerial_assessment', 'Aerial assessment',
   'Fly over a scene and report people, hazards and access before a crew arrives.'),
  ('aerial_supply_drop', 'Aerial light supply drop',
   'Drop a small payload (first aid, water, food ration) to a point a vehicle cannot reach yet.')
on conflict (id) do nothing;

-- Inert until a resources row of kind 'drone' exists (see section 5).
insert into resource_kinds (id, display_name, default_capacity, city_id) values
  ('drone', 'Response drone', 1, 'pune')
on conflict (id) do nothing;

insert into resource_kind_capabilities (kind_id, capability_id, effectiveness) values
  ('drone', 'aerial_assessment',  1.0),
  ('drone', 'field_assessment',   0.5),   -- can look, cannot touch or treat
  ('drone', 'aerial_supply_drop', 0.3),   -- one small payload per sortie
  ('drone', 'supply_delivery',    0.1)    -- not a substitute for a truck
on conflict (kind_id, capability_id) do nothing;

insert into agencies (id, city_id, name, short_name, kind, jurisdiction) values
  ('swarm', 'pune', 'Drone Response Swarm (exercise)', 'Swarm', 'volunteer', 'city')
on conflict (id) do nothing;

insert into agency_capabilities (agency_id, capability_id) values
  ('swarm', 'aerial_assessment'), ('swarm', 'aerial_supply_drop'), ('swarm', 'field_assessment')
on conflict do nothing;

-- Categories the swarm scenarios need but the taxonomy lacks (section 8).
insert into incident_categories
  (id, display_name, hazard_id, dedup_radius_m, dedup_window_min, base_severity, life_safety) values
  ('medical_emergency', 'Medical emergency', 'flood', 100, 20, 4, true)
on conflict (id) do nothing;

insert into incident_category_needs (category_id, capability_id, qty_per_incident) values
  ('medical_emergency', 'medical_transport', 1)
on conflict do nothing;

insert into incident_category_keywords (category_id, phrase, weight) values
  ('medical_emergency', 'injured', 1.0), ('medical_emergency', 'first aid', 1.0),
  ('medical_emergency', 'medical emergency', 1.0)
on conflict do nothing;

-- Optional, only for the dock_fire demo database: a dock as a base.
-- insert into resources (...)   -- deliberately NOT included; see section 5, step C.
commit;
-- Suggested file name: 040_drone_swarm_agency.sql (upstream now ends at 038).
```

Cautions: (a) the `'drone'` kind appears in Copilot's `kinds_providing()` lists once inserted; (b) `person_stranded` needs `water_rescue` (boat, rescue_team, fire_engine), not an ambulance, so a medical case must not be mapped to it; (c) the `hazard_id` for `medical_emergency` is a required FK, not a claim that it is a flood (same reasoning as `unknown_report` in 018).

## 8. Errors and conflicts in the earlier proposed patch

Reviewed (against 4e87ade, rechecked at bd21179: no file it depends on changed): `integrations/disruptionops_patch/backend/migrations/029_swarm_operations_demo.sql`, `.../app/drone/operations.py`, `integrations/disruptionops_bridge.py`, `docs/implementation/disruptionops-integration.md`.

| # | Finding | Evidence | Severity |
|---|---|---|---|
| 1 | The docs and bridge say `unknown_report` "intentionally has no automatic capability demand", so medical and blocked_route "need reviewed taxonomy additions". Since migration 023 it **does** need `field_assessment` (rescue_team, ambulance, fire_engine, boat). Mapping medical to `unknown_report` will dispatch a first responder to look, not an ambulance. | `023_...sql` | Wrong statement; changes behaviour. |
| 2 | `ward_at()` never returns None when the city has wards: it orders by `ST_Covers` desc then distance and takes the nearest. The patch's "outside configured wards -> 422" guard and the old listener's "lies outside every ward" check cannot trigger. A drone sighting 5 km outside the map is silently filed into the nearest ward. | `mesh/service.py:74-88` | Logic error. Add an explicit distance/covers check in the adapter. |
| 3 | `GET /drone/operations/state` calls `replan.replan()` on every poll. A GET with a write side effect. `replan()` does not check `autonomy.paused()` (only event_router, graph, policy, commander do), so polling during an emergency stop re-plans and writes assignments. | `replan.py`, grep of `autonomy` | **High.** Violates the emergency stop. |
| 4 | The patch writes only to its own `swarm_operations_events`. It appends nothing to `events`, so the audit trail, causation chain (`/events/{id}/chain`) and mesh outbox never see the drone observations. The older `drone/swarm.py` does append `drone.survivor_located`. | `operations.py` vs `drone/swarm.py` | Medium. |
| 5 | Observations use `source="sim"` with `clock=WALL`. `sim` has credibility 0.62 against `sensor` 0.88, and `_assess_severity(... simulated=clock.sim_run_id is not None)` is false, so the severity model and possibly LLM calls are not skipped. | `trust.py:37-50`, `intake.py` | Low-medium. |
| 6 | Category mapping is only `person_stranded` / `unknown_report`. There is no fire, no medical, no blocked route (should be `road_blocks`, which `drone/swarm.py` already does). The new `dock_fire` scenario has no mapping. | `SCENE_CATEGORY_MAP` | Gap. |
| 7 | `needs` from the drone (food, rescue, first_aid...) are stored in a note and ignored. Real needs come from `incident_category_needs`. The patch never reduces a need after a drop. Honest, but the field names suggest more. | `operations.py` | Low (documentation). |
| 8 | Idempotency table is reasonable (no unique key on `events` exists), but a retry whose first intake committed and ACK was lost relies on `citizen_reports.device_id` being stored by intake. **UNVERIFIED** that intake persists `device_id` in that column; `015` references it, so likely. | `operations.py` | Medium until tested. |
| 9 | Tied to `city_id = 'pune'` (Literal) and `a.sim_run_id is null`. Resources are not scoped by `sim_run_id` (section 4), so a demo replan moves shared resource rows. The patch states this and demands a separate database; keep that. | `003`, patch header | Known limit. |
| 10 | The older listener `drone/swarm.py` is already in the app and reads the **PyBullet** feed (`init`/`frame`/`log` messages at `/ws`). Pointing it at our `/ops/ws` would connect (or fail the handshake on `/ws`) and then ignore every `snapshot` message. Do not use both. | `swarm.py` `handle()`, `operations_server.http()` | Conflict to resolve. |
| 0 | **Its migration number 029 is now taken** by upstream `029_pcmc_pccoe_zone.sql`. Superseded: our patch is now 039/040 (upstream ends at 038). | `bd21179` | **High (blocking)** |
| 11 | `029` itself is syntactically consistent with the real schema: FK targets `cities(id)` (text), `incidents(id)` (uuid, per 021), `resources(id)` (text, per 021 and 026) match; RLS and revokes match 014/020+. No error found in the SQL. | `021`, `026` | OK. |

## 9. Collision and consequences of migration 029 (bd21179)

1. **Number collision.** The earlier patch file `integrations/disruptionops_patch/backend/migrations/029_swarm_operations_demo.sql` now collides with upstream `029_pcmc_pccoe_zone.sql`. Upstream now ends at 038; ours are **039** and **040**. Rename before anything is copied. (Done: the file is now `swarm_ops/integrations/disruptionops_patch/backend/migrations/030_swarm_operations_demo.sql`.) (Repo numbers differ from Supabase history anyway, see section 0.) Order matters: 029 must be applied first, because the idempotency table has no dependency on it but any swarm test uses PCMC wards.
2. **Catalog.** `assets/operations/agency-catalog.json` is generated from 029: 32 `PC-*` ids with real coordinates. Distance from our anchor (computed from the SQL coordinates, flat-earth approximation): `PC-TRUCK-01` 6 m (PCCOE staging), `PC-PUMP-01` 511 m, `PC-AMB-06` 887 m; the next nearest are 1.2 km (`PC-AMB-04`, `PC-TEAM-02`, `PC-FIRE-01`); the farthest is 7.9 km. So 2 units are inside 600 m, 3 inside 1.2 km, and the other 29 are 1.2-7.9 km away. Consequence: with the real fleet most ground units start off the 1.2 km map and need an off-map approach (travel time before they appear), and the engine must label them as such rather than teleporting them.
3. **The old docs' statements to correct.** "Upstream has no generic medical-emergency category" is still true, but "unknown_report has no needs" is false (023). The mapping table below replaces them.
4. **Categories, verified.**

| Swarm category | Best DisruptionOps category | Need produced | Check |
|---|---|---|---|
| `flood` | `person_stranded` | `water_rescue` | 001 |
| `fire` | `fire` | `fire_suppression` + `medical_transport` | 018 |
| `trapped` | `structural_damage` if a collapse (029 scenario uses it for "wall collapsed, someone trapped"), otherwise `person_stranded` | `search_rescue` or `water_rescue` | 001, 029 |
| `medical` | `heat_casualty` gives `medical_transport` but is a heat hazard; `unknown_report` gives one `field_assessment` responder; neither is right. Propose `medical_emergency` (seed below) | `medical_transport` | 001, 023 |
| `blocked_route` | `flooded_road` (need `dewatering`, a pump) fits only a flooded crossing; `fallen_tree` (need `debris_clearance`) fits debris; a damaged bridge is best a `road_blocks` row plus `structural_damage` if people are at risk | varies | 001, `drone/swarm.py` |

5. **Existing scenario to reuse.** `pccoe_pawana_night` files 11 categories through real intake in this exact zone (person_stranded x5, flooded_road, waterlogging, fire, air_quality, structural_damage x2, power_line, fallen_tree, shelter_full, supply_shortage) and checks PC-* units are tasked. It needs migration 029 applied and uses no drone. It is the natural test for Phase 3 Step B: replay it with swarm sightings in place of some reports.
6. **`ward_at()` is unchanged** and still returns the nearest ward even for a point outside the 029 envelope (finding 2 of section 8). The envelope is 0.13 deg x 0.127 deg, about 14 x 14 km; our map is 1.2 km, so inside it.
7. **New data folders** (`data/raw/osm`, ERA5, `backend/ml`) are inputs for a flood forecast model, not interfaces. They do contain OSM roads and water for the same bounding box; our `prepare_operations_map.py` could reuse them instead of calling Overpass again (ODbL licence applies). **UNVERIFIED:** coordinate/projection parity with our `pune-world.json`.

## 10. Things I could not verify

- Baseline schema and enum values (section 0).
- Whether a staff endpoint lists `operator_overrides` (none found in `api/v1/ops.py`).
- Whether the hosted database has applied 027-029 (the migrations README table still stops at 026 at bd21179).
- How the live Copilot and Commander react to `drone.*` event kinds (not read).
- End-to-end behaviour: nothing here was run against a database.

## 10. Upstream f7ca6d8: migrations 037 and 038 (added after this document was first written)

| Migration | What it changes | Effect on the swarm |
|---|---|---|
| `037_hold_unclassified_reports.sql` | `citizen_reports.source` check adds `sensor`, `phone`, `sim`; closes every open `unknown_report` incident (reports go back to the inbox, needs and assignments removed, event `incident.resolved` by `system:migration-037`). With `incidents/intake.py`, a report nothing can classify is now HELD with no incident until an officer calls `POST /reports/{id}/classify` (`intake.classify_held`). | Only incidents exist to be sent; a held report must never be sent as an `agency_request`. `unknown_report`/`field_assessment` incidents no longer appear, so the `assess` request kind is rare. |
| `038_agency_replies.sql` | `agency_requests` gains `reply`, `reply_kind`, `next_step`, `replies jsonb`, `reply_due_at`, `followup_of`, `units text[]`. | The swarm is an agency, so its ack becomes the reply. |

`ops/agency_replies.py`: every new `agency_requests` row gets `reply_due_at` (3-12 sim minutes) and is answered at random from `REPLY_SET`: `accept_full`, `accept_partial`, `delayed` (life-safety capabilities also hedge to a second agency), `need_info`, `decline_capacity`, `decline_jurisdiction`. The coordinator then stages `AID-` units and re-plans, asks the next agency, or escalates to the EOC. Events: `agency.replied`, `agency.requested` (with `followup_of`), `agency.clarified`, `agency.escalated`. `record()` is the door for a real answer; it demotes an acceptance to a decline when the agency has no units.

Consequence for our patch: a request to `drone-swarm` would be answered at random and an honest acceptance demoted. `backend/patches/agency_replies_external_agencies.diff` marks `drone-swarm` as an external agency (no random reply, no staged units). `swarm_agency.py` then maps the swarm ack to `agency_replies.record(...)`. Our patch migrations are `039_swarm_operations_demo.sql` and `040_drone_swarm_agency.sql`. Taxonomy is unchanged by 037/038 (same 5/12/11/16/11/20 counts); the PCMC catalog (029) is unchanged, regenerated only to record commit f7ca6d8.
