# Drone Swarm → DisruptionOps: technical WebSocket handoff

**Verified against the local implementation on 10 October 2026.** Protocol `disruptionops.swarm.v2`, snapshot schema `2`. This document describes the operations simulator at `/ops/ws`, not the older `/ws` feed.

The simulator runs on the host computer. A client receives drone positions, energy, routes, incident briefings, ground-resource assignments, evacuation progress and exercise events. Authorized operators can change the exercise. These messages do not command physical aircraft or dispatch real agencies. **DisruptionOps has not been modified or connected.** Start with a read-only connection, then implement the integration boundary deliberately.

Related files: [implemented cases](HANDLED-CASES.md), [short protocol reference](websocket-protocol.md), [future DisruptionOps adapter](disruptionops-integration.md), [validation](validation.md), [JavaScript client](../../examples/operations-websocket-client.mjs), [Python client](../../examples/operations_ws_client.py).

## 1. Start and verify the service

From the swarm repository, with Python 3.12+:

```powershell
python -m pip install -r requirements-ops.txt
python operations_server.py --host 127.0.0.1 --port 8787 --fleet 50 --seed 42 --frame-hz 10
```

Alternatively use the existing launcher:

```powershell
.\run-operations.ps1 -Fleet 50 -Port 8787 -Seed 42
```

If the demo is already running on 8787, connect to it rather than starting another process on that port. Use a different port for an independent exercise.

```powershell
Invoke-RestMethod http://localhost:8787/health
python examples/operations_ws_client.py --base-url http://localhost:8787 --frames 3
```

The second command only reads telemetry. It reports fleet size, scenario, pause state and whether the initial world was cached.

| Configuration | Current behavior |
|---|---|
| `--host` | Default `127.0.0.1`: other machines cannot reach this directly |
| `--port` | Default `8787` |
| `--fleet` | Default `20`; engine clamps the count to `1–100`; use `50` for the main exercise |
| `--seed` | Default `42`; reproducible initialization |
| `--frame-hz` | Default `10`; clamped to `1–20` snapshots/second |
| `OPS_CONTROL_TOKEN` | Optional private operator token; generated on startup if absent |
| `OPS_INTEGRATION_TOKEN` | Optional separate secret for the reserved external ground-update action |

The operator link is saved locally at `.cache/operations/operator-url.txt`. Do not commit, screenshot or share that file. A restart creates a new exercise and, unless explicitly configured, a new operator token. No public trial URL is currently available. An HTTPS deployment uses `wss://`; a tunnel transports traffic while simulation continues on this computer.

`localhost` on your friend's laptop means **their** laptop. Use a shared server address or a reverse proxy to the host running this simulator. Binding to a LAN interface is an explicit deployment choice; the default is loopback.

## 2. HTTP endpoints

| Method and path | Purpose and response |
|---|---|
| `GET /health` | `{ok, protocol, simulation_only, clients, integration}` |
| `GET /ops/state` | Full current **state object**, including `world`; no WebSocket envelope |
| `GET /ops/session` | Direct loopback requests can receive `{control_token, can_control:true, simulation_only:true}`; forwarded/public requests receive `{can_control:false, simulation_only:true}` |
| `GET /ops/events?after=0&limit=100` | Durable event replay: `{events:[...], next_cursor}`; integer parameters; page size clamped to `1–500` |
| `GET /` | Existing operations UI |
| WebSocket `/ops/ws` | Continuous snapshots; authorized command input; acknowledgments |

No REST POST command endpoint exists. No subscription message is required after WebSocket connection. HTTP responses do not enable cross-origin browser CORS. Use the proxy/relay in section 8 for a separate frontend.

Example health response:

```json
{"ok":true,"protocol":"disruptionops.swarm.v2","simulation_only":true,"clients":1,"integration":false}
```

`health.integration` reports whether the optional bridge object is attached, not whether a raw external update was received. The current server does not instantiate the bridge; this flag remains false. Use `state.metrics.external_ground` to identify the engine's ground-authority mode.

## 3. Connection and permissions

```text
Read-only:    ws://localhost:8787/ops/ws
Operator:     ws://localhost:8787/ops/ws?token=<URL-ENCODED-PRIVATE-OPERATOR-TOKEN>
Integration:  ws://localhost:8787/ops/ws?token=<URL-ENCODED-PRIVATE-INTEGRATION-TOKEN>
HTTPS host:   wss://your-host.example/ops/ws
```

| Connection | Receive snapshots | Nine exercise actions | `external_resource_update` |
|---|---|---|---|
| No token / wrong token | Yes | Rejected | Rejected |
| Operator token | Yes | Allowed | Rejected |
| Integration token | Yes | Rejected | Allowed |

A wrong token does **not** reject the handshake with HTTP 401: the socket remains view-only. A rejected mutation receives `ok:false`. Possessing a token string in a client does not prove that the server accepted its permissions.

`/ops/session` grants control only when the remote address and Host are loopback and there are no `CF-Connecting-IP`, `CF-Ray`, `X-Forwarded-For` or `Forwarded` headers. It is not a public login endpoint. The existing viewer can use its private `#control=...` link; the fragment is removed and the value stored in session storage.

The browser's `Origin` hostname **and port** must match the request `Host`. Browser `http://localhost:5173` → direct `ws://localhost:8787/ops/ws` fails with HTTP 403, even with a valid token. Section 8 solves this without weakening the check. Server-to-server clients normally send no browser Origin.

## 4. Server → client messages

### Snapshot envelope

This example shows the envelope shape; its empty arrays are placeholders, not a real fleet snapshot:

```json
{
  "type": "snapshot",
  "protocol": "disruptionops.swarm.v2",
  "seq": 120,
  "server_time": 1791590400.0,
  "state": {
    "schema_version": 2,
    "run_id": "opaque-exercise-uuid",
    "revision": 240,
    "t": 12.0,
    "paused": false,
    "speed": 1,
    "scenario": {"id":"rooftop_flood","name":"...","description":"...","seed":42},
    "anchor": [18.6526632,73.7624378],
    "drones": [], "resources": [], "incidents": [], "events": [],
    "coverage": [], "metrics": {}, "flood": {}, "road_conditions": []
  }
}
```

`world` is additionally included on the initial snapshot, reconnect and a changed `run_id`; normal frames omit it. It contains bounds, buildings, roads, terrain and exercise infrastructure. **Preserve the cached world when a frame omits it.** Do not replace the entire application model with an omitted-world frame and discard the map.

| Field | Meaning |
|---|---|
| `seq` | Process-wide telemetry sequence; accept increasing values within a run; gaps are normal |
| `server_time` | Unix seconds from the server clock |
| `run_id` | Exercise identity; changes for scenario/reset/server restart |
| `revision` | Engine mutation/step counter; not an entity version or compare-and-swap lock |
| `t` | Simulated elapsed seconds |
| `paused`, `speed` | Exercise clock controls; a paused exercise still sends snapshots |
| `scenario` | Selected case, description and initialization seed |
| `metrics.simulation_only` | Always true; retain this distinction in consumers |
| `metrics.external_ground` | False for local ground simulation; true after authoritative external import |
| `metrics.active_drones` | Number with `active:true`, including ready aircraft; **not** an airborne/available count |
| `metrics.covered_patches` | Patches ever observed; not a claim that every building was searched |

On a new `run_id`, clear selected entity IDs, route animations, pending assumptions and old world/sequence state. Accept the new world's first frame. Do not carry an old incident assignment into a new exercise. The JavaScript reference client implements world caching and sequence/run handling.

### Entity fields and states

Fields vary with state; accept nullable and missing optional fields. Arrays are full current collections, not entity patches. Join entities by ID rather than array order.

| Entity | Relevant fields |
|---|---|
| Drone | `id`, `position`, `heading`, `state`, `active`, `link`, `battery`, `energy_wh`, `capacity_wh`, `task_id`, `coverage_task`, `payload`, `payload_stock`, `payload_capacity`, `route`, `last_telemetry`; optional transient planning/delivery fields |
| Ground resource | `id`, `kind`, `transport`, `agency`, `state`, `position`, `incident_id`, `capacity`, `route`, `eta`, `free_at`, `arrived_at`, `acknowledged`; optional `load`, `service_end`, `transfer_destination`, `assignment_id` |
| Incident | `id`, `title`, `category`, `severity`, `people`, `position`, `status`, `needs`, `supplied`, `drone_id`, `resource_ids`, `required_kinds`, `completed_kinds`, `briefing`, `handed_over`, `uncertain_delivery`, `evacuated_people`; timestamps and optional `wait_reason` |
| Briefing | `source`, `observed_at`, `summary`, `people`, `hazards`, `needs`, `supplied`, `confidence`, `revision` |
| Coverage patch | `position`, nullable `last_observed` in simulation seconds |
| Event | `id`, `run_id`, `time` (simulated seconds), `kind`, `message`; optional `incident_id`; replay additionally adds `cursor` |

Drone states: `ready`, `patrolling`, `enroute`, `observing`, `returning`, `charging`, `holding`, `lost_link`, `crashed`, `emergency_landed`.

Local ground states: `available`, `busy`, `enroute`, `awaiting_handoff`, `on_scene`, `evacuating`, `holding`. External imports can expose upstream values such as `assigned`, `en_route`, `on_site`, `offline` or `unavailable`; handle unknown display states safely.

Incident states: `reported`, `responding`, `observing`, `ground_response`, `evacuating`, `resolved`. These are not a strictly increasing linear workflow: replacement dispatch or a new observation can change the visible phase.

Important joins and interpretation:

- `drone.task_id === incident.id` identifies its current incident; `incident.drone_id` is nullable and the engine assigns at most one aircraft to an incident.
- `incident.resource_ids` is an assignment history. Determine **current** responders from `resource.incident_id`; do not count every historic resource as currently assigned.
- `supplied` records item kinds supplied, not kilograms, quantity or proof of a real delivery. `supply_drops` counts those recorded item entries, not payload mass.
- `briefing.revision` changes when content changes. `observed_at` changes for refreshes; compute observation age as `state.t - observed_at`, not Unix time minus simulated time.
- `uncertain_delivery:true` means the engine cannot safely infer completion from a missing link. Do not show it as “delivered”.
- `evacuated_people` increases at modeled safe transfer, not at dispatch or supply drop. An incident remains open until required agency actions complete.
- `wait_reason` is a current explanatory string, not an exhaustive structured list of every shortage. Show required/completed agency kinds and needs/supplied alongside it.

### Coordinates and units

| Value | Convention |
|---|---|
| `anchor` | `[latitude, longitude]` |
| Local `position` and route waypoint | `[east_metres, north_metres, height_above_local_ground_metres]` |
| External geographic location | `[longitude, latitude]` |
| Heading | Radians, computed from local east/north motion |
| `battery` | Fraction `0–1`; multiply by 100 for percentage |
| `energy_wh`, `capacity_wh` | Watt-hours |
| ETA, service/release/observation times | Simulated seconds; ETA can be null |

For this small local map, the existing connector uses:

```text
longitude = anchor_lon + east / (111320 * cos(anchor_lat_in_radians))
latitude  = anchor_lat + north / 111320
```

This is a local approximation, not a general geodetic/flight transformation. The 3D renderer uses `[east, AGL + interpolated_terrain, -north]`; do not send those renderer coordinates back as API positions. Terrain is visual; this simulator does not implement terrain-following flight or flight-qualified vertical datums.

### Acknowledgments

```json
{"type":"ack","id":"your-uuid","ok":true,"result":{"ok":true,"action":"pause","run_id":"opaque-exercise-uuid","revision":241}}
```

```json
{"type":"ack","id":"your-uuid","ok":false,"error":"Operator authorization required"}
```

An ACK confirms command acceptance, **not mission completion**. Continue observing incident/resource/drone state to determine the result. Invalid JSON can produce an ACK with `id:null`.

## 5. All supported exercise calls

Every operator action uses this envelope:

```json
{
  "type":"command",
  "id":"fresh-uuid-for-this-intended-action",
  "expected_run_id":"run-id-from-latest-snapshot",
  "action":"speed",
  "payload":{"value":2}
}
```

The ID must be a nonempty string of at most 128 characters. `payload` must be an object. Always include the current `expected_run_id` in an integrating client; the server supports omission but then the protection against affecting the wrong exercise is lost.

| Action | Payload example | Result / constraint |
|---|---|---|
| `scenario` | `{"id":"rooftop_flood","seed":42}` | Reinitializes the selected case and changes `run_id`; seed optional |
| `scenario` | `{"id":"medical_supply","seed":91}` | Medical support case |
| `scenario` | `{"id":"blocked_bridge","seed":42}` | Crossing/route obstruction case |
| `random_scenario` | `{}` | Picks a different one of the three cases and a new random seed; changes run |
| `reset` | `{}` or `{"seed":42}` | Restarts current scenario; changes run |
| `pause` | `{}` | Stops simulated-time progression; telemetry remains connected |
| `resume` | `{}` | Resumes progression |
| `speed` | `{"value":5}` | Finite value `0.25–16`; UI commonly offers `1`, `2`, `5`, `10` |
| `fault` | `{"drone_id":"D01","kind":"crash"}` | Confirmed unavailable aircraft; incident task can be reassigned |
| `fault` | `{"drone_id":"D01","kind":"battery"}` | Injects low energy and requests recovery |
| `fault` | `{"drone_id":"D01","kind":"link"}` | Lost link; incident delivery can become uncertain |
| `release_resource` | `{"resource_id":"R02"}` | Frees an uncommitted simulated resource; rejects one serving an incident |
| `ack_handoff` | `{"incident_id":"I001","resource_id":"R01"}` | Must use **actual IDs** from the snapshot; requires matching physically arrived responder |

There are nine action names; the repeated table rows enumerate their options. IDs above are illustrative. Select real IDs and states from current telemetry. There is no remote fleet-size command; restart with `--fleet` to change it.

The engine contains an internal `detect` helper, but it is **not whitelisted on `/ops/ws`**. Do not send a detection, `goto`, arm, takeoff, cancel, camera stream, link-restoration or individual waypoint command: these are not public actions. A new live incident ingestion contract would need implementation.

## 6. Timing, safety and recovery semantics

The server advances the kinematic mission at a nominal 20 Hz and publishes at 10 Hz by default. The engine subdivides steps to at most 0.25 simulated seconds. Speed changes the mission clock, not the configured WebSocket frame rate. These are not aircraft motor-control loops.

| Behavior | Current model |
|---|---|
| Reconsider allocation | Approximately every 1 simulated second and on selected command/failure events |
| Drone travel / road travel / boat travel | Nominal 18 / 9 / 7 metres per simulated second |
| Refresh aerial briefing | Approximately every 4 simulated seconds while observing |
| Local arrival → automatic handoff | 4 simulated seconds; ground briefing fallback if necessary |
| Stock-empty observation | Can return after 8 simulated seconds even before agency arrival |
| Low battery / ready after charge | Recovery around 23% threshold; eligible after charge around 85% |

Routing, energy budgets, payload and collision yielding are simplified modeled behavior; wind is not modeled. Acknowledged handoff can recall the drone while other required agency work remains. Ground availability and drone availability are allocated independently; a committed resource is not preempted automatically for a new emergency.

Known limitations that consumers must not mask:

- One assigned aircraft per incident: there is no two-drone arrival race or ownership-transfer protocol.
- Lost link is different from confirmed crash. No public restore/verification action exists, and uncertainty can leave an incident waiting.
- A faulted patrol drone can retain a coverage claim; that patch may remain stale. This is a known recovery gap.
- Continued aerial observation until crew arrival is not guaranteed because supply-empty aircraft can return earlier.
- Shelter capacity is not enforced as admission control; supplies are item flags, not a complete cargo/inventory model.
- Allocation runs centrally in one simulation process. This is not a decentralized peer-to-peer flight swarm, CBBA implementation, hardware autopilot, Gazebo model or perception pipeline.

Read [HANDLED-CASES.md](HANDLED-CASES.md) before labelling behavior “fully handled”. Validation includes the three scenarios, failures and real transport tests; dense 100-aircraft profiling does not establish a strict 100-airborne real-time guarantee.

## 7. Reliability, errors and event replay

### Command identity and uncertain outcomes

Use a fresh ID for each intended operation. The server caches up to 2,000 acknowledgments in a process-wide LRU, scoped separately for operator/integration. An **identical envelope** retried while its cached entry remains returns the original ACK. Reusing an ID with a different action, payload or run expectation is rejected.

If the connection drops after a send, the action may already have happened. Do not create a new ID and blindly repeat it. Reconnect, inspect the current run and state, then either retry the identical envelope within the same process/cache or reconcile the observed result. The cache is bounded and is lost on restart, so this is not indefinite exactly-once delivery. Exact cached reset retries work despite their old run expectation; a new command against an old run is rejected.

The JavaScript reference client rejects pending promises on disconnect, marks telemetry stale after 7 wall-clock seconds, reconnects with bounded delay (0.6–15 seconds), and **does not automatically resend mutations**. It uses an 8-second ACK timeout. The Python reference is a short bounded diagnostic consumer, not a permanent reconnection daemon.

### Limits and error responses

| Condition | Response |
|---|---|
| Cross-origin browser upgrade | HTTP `403` |
| Invalid JSON / nonfinite literal | Rejected ACK; may have `id:null` |
| Invalid envelope / missing ID / non-object payload | Rejected ACK |
| Missing permission | Rejected ACK; socket may remain read-only |
| Unknown action / entity / scenario | Rejected ACK |
| Wrong `expected_run_id` | Rejected ACK; refresh the exercise |
| Reused ID with different contents | Rejected ACK |
| More than 10 incoming messages in a rolling second | WebSocket close `1008`; invalid inputs also consume this budget |
| Input frame over 16 KiB | WebSocket close `1009` |
| Binary input | WebSocket close `1003` |

Transport settings: incoming queue 8, outgoing per-client snapshot queue 2, protocol ping every 20 seconds, ping timeout 30 seconds, deflate compression. Old queued telemetry is dropped for slow clients. The **16 KiB cap is for incoming commands**; initial world snapshots can be much larger. The Python example allows 4 MiB incoming messages. Do not assume every snapshot is tiny.

The stream includes the latest 180 in-memory events. It is not a complete event-history guarantee and is not an event-sourced reconstruction of every state change. Recover recorded events through:

```text
GET /ops/events?after=<last_processed_cursor>&limit=100
```

Process a page, persist its final cursor, then fetch the next page until empty. Deduplicate by `(run_id,id)`. Replay can include multiple runs; filter using `run_id`. Cursor is separate from telemetry `seq` and event-local `id`. SQLite journal data survives restart; simulation state and ACK cache do not. Very high event production can outrun the in-memory ring before journaling; do not advertise durable capture of every conceivable event. No journal-retention policy is configured.

Current event kinds include `scenario`, `incident`, `deduplicated`, `ground_dispatch`, `drone_dispatch`, `flood_routes`, `route_blocked`, `supply_unavailable`, `briefing`, `supply`, `ground_arrival`, `handoff`, `handoff_pending`, `drone_return`, `resource_free`, `evacuation`, `reload`, `emergency_landing`, `safety_hold`, `evacuation_hold`, `external_update`, `stale_handoff`, `fault`, `resolved`. Treat new kinds as displayable events rather than crashing on an exhaustive enum.

## 8. Connect a separate app properly

### Recommended first connection: backend relay

Have your friend's backend connect read-only to `/ops/ws`. It receives the full model, then forwards the fields their frontend needs through their own authenticated WebSocket. This avoids browser cross-origin handshakes and keeps integration secrets on the backend. Their existing user permissions still apply. A simulator connection does not automatically allocate anything in DisruptionOps.

```text
Swarm host /ops/ws → friend's backend adapter → friend's frontend
                      ID mapping + permissions + coordinate conversion
```

Use the Python client as a starting point for parsing, world caching and run handling; extend it with a reconnect loop and a latest-snapshot queue for a permanent relay. Do not put an unbounded queue between two 10 Hz feeds.

### Direct browser alternative: same-origin proxy

For a Vite app running on 5173, add the following to **the friend's frontend configuration when they integrate**. No existing DisruptionOps files have been changed here.

```js
// vite.config.js
import { defineConfig } from 'vite';
export default defineConfig({
  server: {
    proxy: {
      '/swarm': {
        target: 'http://127.0.0.1:8787',
        ws: true,
        changeOrigin: false, // preserve browser-facing Host for the upstream Origin check
        xfwd: true,         // prevent forwarded requests from receiving a local operator session
        rewrite: path => path.replace(/^\/swarm(?=\/|$)/, '')
      }
    }
  }
});
```

Connect to `ws://localhost:5173/swarm/ops/ws`, or derive it from `location.origin`. Browser Origin and preserved upstream Host now match. Do not enable `rewriteWsOrigin` to bypass origin checking. These settings follow Vite's [server proxy documentation](https://vite.dev/config/server-options#server-proxy). This development proxy is not a production deployment.

Production Nginx equivalent, with `map` in the `http` context and `location` in your existing `server` block:

```nginx
map $http_upgrade $swarm_connection_upgrade {
    default upgrade;
    ''      close;
}

# Inside the existing server block:
location /swarm/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $swarm_connection_upgrade;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 75s;
}
```

Nginx requires explicit forwarding of WebSocket upgrade headers; see its [official WebSocket proxy documentation](https://nginx.org/en/docs/http/websocket.html). Add the snippet to an existing configuration; it is not a complete Nginx file. This prefix proxies the **API** for the friend's app. The simulator UI uses absolute asset paths and is not automatically remounted under `/swarm/` by this configuration.

### JavaScript usage

Copy `examples/operations-websocket-client.mjs` into the friend's project/module bundle. The simulator only serves allowlisted `web`/`assets` files, so `/examples/...` is not a hosted URL.

```js
import { connectSwarm } from './operations-websocket-client.mjs';

const client = connectSwarm({
  baseUrl: `${location.origin}/swarm/`, // direct viewer origin can use location.origin + '/'
  onStatus: status => showConnectionStatus(status),
  onState: (state, {seq, worldUpdated}) => {
    renderIncidentList(state.incidents);
    renderDroneList(state.drones);
    renderResources(state.resources);
    if (worldUpdated) loadMapGeometry(state.world);
    updatePositions(state.drones, state.resources);
    showSimulationBadge(state.metrics.simulation_only);
  },
  onAck: ack => console.debug('Exercise acknowledgment', ack.id, ack.ok)
});

// On component unmount/page teardown:
// client.close();
```

The rendering functions above belong to the friend's app. Create one connection per mounted view/service, not one per React render. Subscribe in an effect and call `close()` in its cleanup. Read-only is the default; the example makes no changes.

For a deliberately authorized local operator view, supply a token through private runtime configuration:

```js
const operator = connectSwarm({
  baseUrl: 'http://localhost:8787/',
  token: privateRuntimeToken,
  onState: renderExercise
});

// After the first snapshot, in an explicit user action handler:
async function pauseExercise() {
  const envelope = operator.makeCommand('pause', {});
  try {
    const ack = await operator.send(envelope);
    showAccepted(ack);
  } catch (error) {
    showCommandError(error.message); // inspect state before deciding whether to retry envelope
  }
}
```

Do not expose the integration token in a public browser. Query tokens can appear in proxy access logs; configure redaction. The operator example must run from an allowed origin/proxy, and `privateRuntimeToken` is a supplied value, not a library constant.

### Python usage

```powershell
# Read-only snapshots:
python examples/operations_ws_client.py --base-url http://localhost:8787 --frames 5

# Explicit local operator command; reads the direct loopback session privately:
python examples/operations_ws_client.py --local-session --action pause
python examples/operations_ws_client.py --local-session --action resume

# JSON file input works with both Windows PowerShell and PowerShell 7:
$scenarioPayload = Join-Path $env:TEMP 'swarm-scenario-payload.json'
'{"id":"medical_supply","seed":42}' | Set-Content -LiteralPath $scenarioPayload -Encoding UTF8
python examples/operations_ws_client.py --local-session --action scenario --payload-file $scenarioPayload
```

For a remote authorized exercise, set `OPS_FRIEND_TOKEN` privately in the process environment and omit `--local-session`. Do not paste actual secrets into documentation or checked-in code. `--token-env` selects a different environment variable; `--base-url` accepts the HTTP(S) base, including an API prefix such as `https://host.example/swarm/`. `--frames` controls how many snapshots the diagnostic consumer reads. `--payload` accepts inline JSON; `--payload-file` takes precedence and avoids native shell quoting problems, including UTF-8 BOM handling. Python examples use `proxy=None` for the explicit server URL, so an ambient corporate/system proxy does not silently redirect the connection.

Programmatic use from the repository:

```python
from examples.operations_ws_client import consume
result = await consume('http://localhost:8787', frames=3)
state = result['state']
assert state['metrics']['simulation_only'] is True
```

## 9. Future authoritative agency updates

This is a **reserved, explicitly enabled channel**, not the current running connection. Set a separate `OPS_INTEGRATION_TOKEN` on the simulator process, then connect from a trusted backend with that token. The only permitted action in this scope is `external_resource_update`.

Decide the authority first: when connecting DisruptionOps for real integration, **DisruptionOps owns agency assignments, arrival and completion**. The simulator must not allocate those same ground resources independently. On the first successful import, the engine clears its local simulated ground resources and sets `external_ground:true`; local ground movement/allocation/automatic handoff stop. Aircraft decisions continue. Reset/scenario starts a fresh local exercise again; the adapter must re-establish run mappings.

Before forwarding, validate the whole response using `integrations.disruptionops_bridge.validate_external_state(external_state, current_run_id)`. The raw WebSocket action does **not automatically call this stricter validator**. An integrating backend must apply it and keep the import token private.

Illustrative upstream object, with IDs/revision replaced from actual state:

```json
{
  "source":"disruptionops",
  "mode":"simulation",
  "run_id":"current-exercise-uuid",
  "incidents":[
    {"local_id":"actual-local-incident-id","external_id":"agency-incident-123","status":"responding","resolved":false}
  ],
  "resources":[
    {"id":"agency-resource-456","kind":"rescue","state":"on_site",
     "position":[100,200,0],"incident_id":"agency-incident-123",
     "eta":0,"agency":"example-rescue-team","assignment_id":"assignment-789"}
  ],
  "handoffs":[
    {"incident_id":"agency-incident-123","resource_id":"agency-resource-456",
     "briefing_revision":3,"accepted":true}
  ]
}
```

Validation expects external `[longitude,latitude]` in `location`, or local three-element `position`. It accepts upstream resource states `proposed`, `approved`, `assigned`, `en_route`, `on_site`, `available`, `offline`, `complete`. Handoffs require explicit boolean acceptance and a matching current on-site assignment; historical acknowledgments are ignored. The engine additionally refuses recall for a stale briefing revision and checks that the responder matches the incident.

Forward the **validated** object as the command payload:

```python
from integrations.disruptionops_bridge import validate_external_state

payload = validate_external_state(external_state, state['run_id'])
envelope = {
    'type': 'command', 'id': fresh_uuid,
    'expected_run_id': state['run_id'],
    'action': 'external_resource_update', 'payload': payload
}
```

Do not blindly mark an incident resolved merely because supplies arrived. Imported resolution is authoritative and can close a local incident; establish what upstream “resolved” means. The proposed `/api/v1/drone/operations/events`, `/state` and `/handoff` endpoints belong to the **unapplied** adapter patch in this repository; they are not live endpoints of this server or the existing DisruptionOps deployment. Setting bridge environment flags alone does not wire that adapter into `operations_server.py`.

Read [disruptionops-integration.md](disruptionops-integration.md) for taxonomy mapping, durable outbox, proposed database migration and route changes. The older DisruptionOps drone feed and this schema are not plug-compatible. First build an explicit versioned adapter, test it independently, then review installation into the other project.

## 10. Acceptance checklist for the friend

1. Start/read the existing service; `/health` reports the expected protocol and simulation-only mode.
2. Receive the initial full-world snapshot, then verify that later omitted-world frames preserve the map.
3. Read positions, energies, incident needs and briefing age with the documented units and ID joins.
4. Reconnect read-only; keep receiving state and replace the world when the run changes.
5. Confirm browser access through the same-origin proxy/relay; direct mismatched-origin access should remain rejected.
6. Confirm a view-only connection cannot mutate the exercise.
7. In an isolated exercise, test each needed authorized action, inspect its ACK, then observe the resulting state.
8. Retry an identical command ID and confirm the cached ACK; reject reused IDs with changed contents and commands against a stale run.
9. Replay event pages using a persisted cursor; distinguish runs and avoid duplicate processing.
10. Before agency integration, agree ownership, map incident IDs/coordinates, validate imports and confirm arrival/revision-based handoff. Keep local agency allocation disabled in authoritative mode.

The supplied clients are additionally exercised against a real ephemeral localhost server by `tests/operations_clients_acceptance.py`; its Node helper is `tests/operations-client-example.mjs`. Run it from the repository with Python and Node 22+ installed. It does not send commands to the running demo on 8787:

```powershell
python tests/operations_clients_acceptance.py
```

Source of truth: [`operations_server.py`](../../operations_server.py), [`ops_simulation.py`](../../ops_simulation.py), [`integrations/disruptionops_bridge.py`](../../integrations/disruptionops_bridge.py). If a later implementation changes these contracts, update the version and this handoff together.
