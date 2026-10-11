# Drone Swarm Agency: test guide

You are testing a **drone-swarm agency** that runs on my laptop and is reachable over the internet through a Cloudflare tunnel. You play **DisruptionOps**, the city command centre. You send the swarm emergencies; the swarm decides on its own (no LLM, no hard-coded table) which drones to launch, what to drop and which ground agencies it needs, or, in orchestrated mode, executes the drones and units you assign. Everything appears live on the 3D map.

Simulation only: no real aircraft or agencies are dispatched.

## 1. What I send you (privately)
- **URL**: `https://<name>.trycloudflare.com` (it changes each time I restart; if it stops working, ask me for the new one)
- **WebSocket**: `wss://<name>.trycloudflare.com/ops/ws`
- **Integration token**: a secret string that lets you act as DisruptionOps. Don't post it anywhere.

## 2. Watch it (no setup)
Open the URL in a browser. Without a token the page is **view only**.
- Left: incidents, fleet, ground response, command-centre chat.
- Right: **Live log** of every event, decision (with its "why"), signal and message. Click a row to fly to it.
- Map: drones (small quadcopters), docks, ambulances / fire engines / rescue teams / police on real roads, people waiting for help (red ring), SOS and briefing "clouds".
- Double-click anywhere to fly there; scroll zooms to the cursor.

## 3. Test it as DisruptionOps (5 minutes)
Needs Python 3.10+:
```bash
pip install websockets
# get the script (or ask me for the file): examples/disruptionops_simulator.py from the repo
python disruptionops_simulator.py --url wss://<name>.trycloudflare.com/ops/ws --token <INTEGRATION_TOKEN> --requests 4
```
It sends real DisruptionOps-style reports (Ravet family on a terrace, elderly couple at Morya ghat, MIDC chemical fire with gas, a wall collapse...) and prints what the swarm does, e.g.:
```
[DisruptionOps -> swarm] #3 agency_request: "Fire at a chemical unit in MIDC, strong gas smell spreading"
  -> swarm: ACCEPTED incident=I005 eta=18.1 drones=['D10']
  SITUATION r1 I005: 6 people (about 3-9) at chemical unit fire; fire high; PC-FIRE-01 fire_engine 910 m away, available
  DISPATCH command_center -> fire_service: Dispatch 3x fire_engine to I005: fire-fighting work (orchestrator plan)
  [ground_dispatch] PC-FIRE-01 (fire_engine) dispatched concurrently via a valid road route, ETA 196 s
```
Keep the browser open next to it and watch the same events on the map and in the live log. Useful flags: `--seconds 300`, `--interval 10`, `--verbose` (every event), `--watch-only` (no token).

## 4. Write your own client
Every message is JSON text. Commands use one envelope:
```json
{"type":"command","id":"<uuid>","action":"<action>","expected_run_id":"<run_id from the latest snapshot>","payload":{...}}
```
Connect to `wss://<name>.trycloudflare.com/ops/ws?token=<INTEGRATION_TOKEN>`. The server pushes `{"type":"snapshot","state":{...}}` about 10 times a second and answers each command with `{"type":"ack","id":...,"ok":true|false,...}`.

### 4a. Ask the swarm for help (it decides)
```json
{"action":"agency_request","payload":{
  "request_id":"do-123", "kind":"sos", "position":[-148,-293,0],
  "category":"person_stranded", "people":5, "priority":4, "requested_by":"disruptionops",
  "situation":{"report_text":"Family on terrace, water rising","vulnerable":["family"],"injured_reported":0,"hazards":["rising_water"],"callers":3}}}
```
The ack says `accepted`, `reason`, `eta_s`, `assigned_drones`, `plan`, plus `reply_kind` (`accept_full | accept_partial | delayed | need_info | decline_capacity | decline_jurisdiction`) and `units`. Re-sending the same `request_id` is safe (no duplicate).

### 4b. Orchestrated: YOU assign, the swarm executes and shows
```json
{"action":"orchestrate_incident","payload":{
  "request_id":"do-200", "external_id":"DO-INC-77", "position":[25,-94,0], "category":"fire", "people":6,
  "assignments":{
    "drones":[{"count":1,"role":"assess"}],
    "resources":[{"id":"PC-AMB-06","kind":"ambulance","agency":"108 EMS"},
                 {"id":"PC-FIRE-01","kind":"fire_engine","agency":"PCMC Fire Brigade"}]}}}
```
The swarm launches exactly what you assigned, drives those units on real roads, never adds its own, and the incident shows **"Assigned by DisruptionOps"** on the map. Follow-ups (same envelope):
- `assign` `{external_id, drones, resources}`: add more
- `unassign` `{external_id, drone_ids, resource_ids}`
- `recall_drone` `{drone_id, reason}`
- `update_incident` `{external_id, people, severity, situation}`
- `cancel_incident` / `close_incident` `{external_id, reason}`
- `control` `{autonomy:"paused"|"running", reason}`: emergency stop (no new launches)

### 4c. What to read back from each snapshot
- `incidents[]`: `status`, `first_on_scene` (drone or ground), `resource_requests[]` (the orchestrator's dispatch orders and why; the swarm itself only sends `situation_report` and `alert` messages in `messages`), `assigned_drones`, `assigned_units`, `control_mode`
- `resources[]`: ground units: `id` (e.g. `PC-AMB-06`), `agency`, `state` (`available|enroute|on_scene|evacuating`), `eta`, `route`
- `drones[]`: `position`, `battery`, `activity`, `route_kind` (`mission|sweep|return`), `last_decision`
- `messages[]`: command centre ↔ swarm conversation; `decisions[]`: every decision with `why`; `signals[]`: SOS/briefing/dispatch clouds; `metrics.agency`: scorecard

Coordinates are metres east/north of the map centre (PCCOE, Akurdi; 18.6527 N, 73.7624 E). The district map spans about -600..600 on both axes.

## 5. Ideas to try
1. Send 3–4 `agency_request`s close together: does it prioritise the elderly couple and the fire with casualties?
2. Orchestrate an incident with only an ambulance: the swarm must not launch a drone of its own.
3. Assign a drone later with `assign`, then `recall_drone` it mid-flight.
4. `update_incident` people from 2 to 15: watch the swarm re-plan (more units requested) in the live log.
5. Send a position outside -600..600: expect `decline_jurisdiction`; omit the position: `need_info`.
6. `control` paused: new requests are declined (`decline_capacity`) until `running`.

Full protocol: `docs/implementation/websocket-protocol.md` and `docs/implementation/FRIEND-WEBSOCKET-HANDOFF.md` in the repo. Send me anything that looks wrong (time from the live log + what you sent).
