# Drone Swarm Live Feed: integration spec (for a coding agent)

A 12-drone rescue-swarm simulation (PyBullet physics, decisions by TypeSafe Jev) runs on the host's laptop and broadcasts its live state over a WebSocket. You can build any client against it: a viewer, a dashboard, a logger, an analyser.

- **Base URL:** `https://were-utah-karl-clause.trycloudflare.com`
  - This is a Cloudflare quick tunnel. The hostname changes whenever the host restarts the tunnel. If it stops resolving, ask the host for the new one.
- **WebSocket:** `wss://pcs-albums-coating-pills.trycloudflare.com/ws`
- **Auth:** none. The feed is **read-only**: anything a client sends is ignored.
- **Transport:** plain text JSON frames. No binary, no compression, no subprotocol.
- **Keepalive:** the server pings every 20 s, so any standard client library handles it.

## Connection lifecycle

1. Connect to `/ws`.
2. The server immediately sends one `init` message.
3. It then sends the most recent `frame`, followed by `frame` messages at about **12 Hz** and `log` messages as events occur.
4. Each mission lasts about 3 minutes. About 12 seconds after it completes, the server restarts the scenario. **The WebSocket connection drops and `t` restarts near 0.** Reconnect automatically after 1 to 2 s, and treat `t` going backwards as a new run.

## Message schemas

Every message has a `type` field.

### `init` (once per connection)
```json
{"type":"init",
 "scene":{
   "survivors":[[x,y,z],[x,y,z],[x,y,z]],
   "placements":[["building-01",[x,y,z]], ...],
   "launch_pad":[x,y],
   "manifest":{"building-01":{"parts":[{"mesh":"building-01-0.obj","texture":"assets/.../Concrete031_1K_Color.jpg"}],"collision":"building-01-collision.obj","height":7.05}, ...},
   "drones":12,
   "ground":[16.25,12.75],
   "mode":"AI (jev)"},
 "log":["SENSOR: ...", "..."]}
```
- `survivors` has one entry per rescue site, indexed 0 to 2. They are the ground truth positions of the people to rescue.
- `ground` is the half-extents of the district in metres (x, y).
- `mode` is `"AI (jev)"`, `"rule agents"` or `"scripted"`.
- `log` holds the last 40 log lines.

### `frame` (about 12 per second)
```json
{"type":"frame","t":34.08,"phase":"AGENTIC SEARCH / RESCUE | AI ACTIVE",
 "found":[0,1],"delivered":[0],
 "drones":[[-5.553,9.047,9.995, 0.0183,-0.0072,-0.0019,0.9998, "EXPLORE", 0.981, 1], "... 12 rows total"],
 "payloads":{"0":[x,y,z]}}
```
| Field | Meaning |
|---|---|
| `t` | Simulated mission time in seconds (real time 1:1). |
| `phase` | Free-text phase. Starts with `AGENTIC SEARCH / RESCUE`, `RETURN` or `COMPLETE`. It may end with `\| AI ACTIVE` or `\| AI WAITING / HOLD`. |
| `found` | Site indices (0-based) located so far. |
| `delivered` | Site indices (0-based) where supplies have landed. |
| `drones[i]` | Row for drone `i` (0-based, 12 rows), see below. |
| `payloads` | Dropped supply packages, as `{ "<siteIndex>": [x,y,z] }`. |

**Drone row**, by index: `[0..2]` position x,y,z in metres; `[3..6]` orientation quaternion in **x,y,z,w** order; `[7]` state string; `[8]` battery from 0 to 1; `[9]` active flag (1 or 0, where 0 means unavailable).

**Drone states:** `TAKEOFF`, `EXPLORE`, `DELIVER`, `RELAY`, `RETURN`, `COMPLETE`.

### `log` (as events happen)
```json
{"type":"log","message":"AI: D5 -> Jev: sector 20 p=0.67"}
```
The `message` is plain text. The prefixes you will see:
- `SENSOR: D4 located site 2; broadcast to peer agents for auction`
- `AGENT: {"time":40.38,"event":"assignment","drone":2,"site":2}` (JSON after the colon; events are `assignment`, `unavailable` and `obstacle_detour`)
- `PAYLOAD: D1 released at site 1`
- `CONFIRMED: payload landed at site 1`
- `AI: D5 -> Jev: sector 20 p=0.67; bid 0 p=0.83` (Jev decision: the chosen search sector and any rescue bids, each with a probability)
- `AI: D3 request failed (<ErrorType>)`
- `MISSION COMPLETE: restarting scenario`

## Conventions and pitfalls

- **Coordinates:** metres, **Z-up**. The district centre is the origin, spanning roughly x ±16.25 and y ±12.75. The launch pad is at (-13, -11).
- **Indexing:** the `drones` array and `found` / `delivered` / `payloads` are **0-based**. Log lines use **1-based** drone numbers (`D5`) and site numbers (`site 2`). Drone `D5` is `drones[4]`; `site 2` is index 1.
- **Quaternion order** is x,y,z,w. In three.js, `quaternion.set(x,y,z,w)` works directly inside a Z-up group. In scipy, use `Rotation.from_quat([x,y,z,w])`.
- **Timing:** `t` is simulated time. It resets to about 0 on each restart.
- **Jev:** Jev picks the search sector and the rescue bids. The simulator handles flight control and collision avoidance.

## Minimal clients

### Python
```python
import asyncio, json
from websockets.asyncio.client import connect  # pip install websockets

URL = "wss://pcs-albums-coating-pills.trycloudflare.com/ws"

async def main():
    while True:
        try:
            async with connect(URL) as ws:
                async for raw in ws:
                    m = json.loads(raw)
                    if m["type"] == "init":
                        print("mode:", m["scene"]["mode"], "survivors:", m["scene"]["survivors"])
                    elif m["type"] == "frame":
                        d1 = m["drones"][0]
                        print(f"t={m['t']:.1f} D1 pos={d1[:3]} state={d1[7]} batt={d1[8]}")
                    elif m["type"] == "log":
                        print(m["message"])
        except Exception as e:
            print("reconnecting:", e)
            await asyncio.sleep(2)

asyncio.run(main())
```

### JavaScript (browser or Node 22+)
```js
const ws = new WebSocket("wss://pcs-albums-coating-pills.trycloudflare.com/ws");
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.type === "frame") console.log(m.t, m.drones[0]);
};
ws.onclose = () => setTimeout(() => location.reload(), 2000); // or reconnect
```

## HTTP resources (same host)

| Path | Content |
|---|---|
| `/` | The reference three.js viewer. Open it in a browser to see it work. |
| `/assets/city-ready/manifest.json` | The same building manifest as in `init`. |
| `/assets/city-ready/<mesh>.obj` | Building meshes (Z-up OBJ, with UVs). Gzip is supported. |
| `/<texture path from the manifest>` | JPG textures. |
| `/web/legacy/index.html`, `/web/vendor/*` | Source of the reference viewer. |

Only `web/` and `assets/` are served. There are no other endpoints.

## What a coding agent can build

- A custom 3D view or a 2D top-down map using `drones[*][0..1]` and `survivors`.
- A mission dashboard with per-drone battery, state timelines and a Jev decision log, parsed from `log` lines starting with `AI:`.
- Analytics such as coverage over time, time to find each site, and delivery latency.
- A recorder that stores frames to disk for replay.

## Operating notes

- The feed works only while the host's laptop is on and the simulation is running.
- Please do not hammer the server. One WebSocket per client is plenty.
