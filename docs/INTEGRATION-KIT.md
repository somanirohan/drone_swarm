# DisruptionOps integration kit

The swarm is a set of nodes talking over one message bus. DisruptionOps is the **orchestrator**: it reads the
swarm's `situation_report` and `alert` messages, decides which units are needed and dispatches them. The swarm
never says "send N ambulances"; it says what it sees ("PC-AMB-06 ambulance 400 m away, en route").

## Quickstart (10 lines)

```python
# pip install websockets ; copy swarm_ops/integrations/client/swarm_client.py
client = SwarmClient("ws://HOST:PORT/ops/ws", OPS_INTEGRATION_TOKEN)   # token is sent as ?token=
client.on_situation_report = lambda m: your_planner(m["body"])          # people, injured, hazards, access, nearby_units...
client.on_alert = lambda m: your_events.append(m["body"])
client.start(); await client.wait_ready()
await client.send_control(orchestrator="external")                     # disables the built-in stand-in orchestrator
ack = await client.send_agency_request("do-123", [x, y], "flood", people=5, priority=4)  # ack.result.reply_kind
# later: ack = await client.send_assign(incident_id, drones=[{"count": 1, "role": "assess"}], resources=[{"id": "PC-AMB-06", "kind": "ambulance"}])
```

Example: `swarm_ops/integrations/client/example.py`. Tests: `tests/test_nodes.py`.

## Connection and envelope

* `ws(s)://HOST:PORT/ops/ws?token=<OPS_INTEGRATION_TOKEN>` (protocol `disruptionops.swarm.v2`).
* Server pushes `{"type":"snapshot","protocol":...,"seq":n,"state":{...,"messages":[...],"events":[...],"nodes":{...}}}` ~10 Hz.
* Command: `{"type":"command","id":"<unique 1-128 chars>","action":"...","payload":{...},"expected_run_id":"<run_id>"}`
  Ack: `{"type":"ack","id":"...","ok":true,"result":{...},"incident_id":"I003"}` or `{"ok":false,"error":"..."}`.
* Idempotency: same `id` + same payload returns the original ack; same `id` + other payload is an error.
  `request_id` inside agency_request / orchestrate_incident is idempotent too (`"duplicate": true`).
* Rate limit: more than 10 commands per second closes the socket (code 1008). Errors seen: `Operator authorization required`,
  `Unknown action`, `Command id already used with a different payload`, `Scenario changed; refresh state before issuing this command`,
  `Unknown incident`, validation messages (`position must be ...`).
* Reconnect / replay: snapshots carry the last 200 messages (`state.messages`, ids are integers); remember the last id you handled and skip
  lower ones (the client does). Older data: `GET /ops/messages?after=<id>&limit=n` and `GET /ops/events?after=<cursor>` (durable journal).

## Commands you can send (integration token)

| action | payload example | ack result |
|---|---|---|
| `control` | `{"orchestrator":"external"}` (or `{"autonomy":"paused","reason":"..."}`) | `{"orchestrator":"external"}` |
| `agency_request` | `{"request_id":"do-1","kind":"sos","position":[120,-40],"category":"flood","people":5,"priority":4,"situation":{"report_text":"..."}}` | `{"accepted":true,"reply_kind":"accept_full","units":["D17"],"assigned_drones":["D17"],"eta_s":47.4,"incident_id":"I003","reason":"accepted; aircraft assigned","plan":["launch D17 ...","report the situation ..."]}` |
| `orchestrate_incident` | `{"request_id":"do-2","external_id":"INC-9","position":[120,-40],"category":"fire","people":4,"assignments":{"drones":[{"count":1,"role":"assess"}],"resources":[{"id":"PC-FIRE-01","kind":"fire_engine"}]}}` | `{"accepted":true,"reply_kind":"accept_full","units":["D03"],"incident_id":"I004","control_mode":"orchestrated"}` |
| `assign` / `unassign` | `{"incident_id":"I004","drones":[{"count":1,"role":"overwatch"}],"resources":[{"id":"PC-AMB-06","kind":"ambulance"}]}` | `{"incident_id":"I004","assigned_drones":[...],"units":[...]}` |
| `recall_drone` | `{"drone_id":"D03","reason":"reassign"}` | `{"drone_id":"D03","state":"returning"}` |
| `update_incident` | `{"incident_id":"I004","people":9,"severity":5}` | incident summary |
| `cancel_incident` / `close_incident` | `{"incident_id":"I004","reason":"done"}` | incident summary (`status: resolved`) |
| `request_situation` | `{"incident_id":"I004"}` | `{"queued":true}`; a fresh `situation_report` follows (`reason: need_info`) |
| `external_resource_update` | `{"source":"disruptionops","run_id":"...","resources":[{"id":"PC-AMB-06","kind":"ambulance","state":"en_route","position":[..],"incident_id":"I003","eta":60}],"handoffs":[]}` | applies your authoritative ground state |

`reply_kind` is one of `accept_full | accept_partial | delayed | need_info | decline_capacity | decline_jurisdiction`. The swarm accepts or
declines **drone** work only.

## Messages the swarm sends (in `state.messages`; `{id,t,from,to,kind,incident_id,request_id,text,body}`)

* `situation_report` (swarm -> command_center): first look, significant change, periodic (30 s), on `request_situation`.
  body: `incident_id, request_id, observed_at, revision, reason, people{estimate,low,high}, injured{estimate,severity}, vulnerable[],
  hazards[{kind,level,value,trend}], trend, access{roads_open,roads_blocked,roads_flooded,best_approach},
  nearby_units[{id,kind,agency,distance_m,eta_s,state,assigned_here}], lifelines[{name,kind,capacity,distance_m}], drones_on_scene[],
  supplies_dropped[], confidence, uncertainty_note, text, controlled_by, scene{...}, belief{...}`. No unit kinds or quantities are requested.
* `alert` (swarm -> command_center): body `{alert: drone_fault|life_threat|situation_worsening|access_blocked, severity, incident_id, ...}`;
  drone faults carry `drone_id` and `fault` (`low_battery|link_loss|crash`).
* `accept` / `decline` / `status` / `ack` / `handoff` (swarm -> command_center): answers and drone-side status.
* `unit_status`, `handoff`, `decline` (agency node -> command_center): `body{unit_id,kind,agency,state,phys_state,incident_id,eta_s,position,capacity,dispatch_id}`;
  `state` is `en_route|arrived|on_scene|transporting|available|unavailable`.
* `dispatch` / `cancel` (orchestrator -> agency node): only visible when the built-in orchestrator is enabled.
  Real examples are in `docs/implementation/websocket-protocol.md` and any `/ops/messages` page of a live run.

## Mapping to DisruptionOps

| swarm | DisruptionOps |
|---|---|
| `agency_request` ack `reply_kind`/`units` | `agency_requests.reply_kind`, `.units`, `.replies` |
| `status`/`events` (`/ops/events`) | `events.append(kind=status)` |
| `situation_report.body` | update incident needs (people, injuries, hazards, access) then run YOUR planner |
| `alert` | high-priority incident events / notifications |
| `unit_status` | operations/resources rows (state, eta) |
| your dispatch | `assign` / `orchestrate_incident` (or `external_resource_update` for authoritative ground state) |

## Remote agency nodes

An agency can run as its own process: `python -m swarm_ops.nodes.run --node ambulance_service --url ws://HOST:PORT/ops/ws --token TOKEN`.
It attaches (`node_attach`), reads `dispatch` messages addressed to it, chooses one of its units, orders it (`node_order`) and posts
`unit_status` (`node_post`).

## Not included yet

JSON Schemas for each payload (`swarm_ops/integrations/schemas/`) were not generated in this pass; the payload shapes above are the
contract, exercised by `tests/test_nodes.py`.
