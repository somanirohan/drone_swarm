# Drone Swarm Live: host it and share it

The simulation runs on the host's laptop. It streams every drone position over a WebSocket to a three.js viewer in the browser. The viewer is read-only, so nobody who opens it can change the mission. Everyone sees the same live run.

```
Host laptop:  simulation (PyBullet)  ->  WebSocket + web page on port 8765
Friend:       any browser (phone or laptop)  ->  opens the shared link
```

## Part 1: Host (you)

Everything lives on `D:\drone_swarm`. The Python environment is in `D:\drone_swarm\.conda-drones`. The package cache and temp files are in `D:\drone_swarm\.cache`.

Start the simulation and viewer:

```powershell
cd D:\drone_swarm
powershell -ExecutionPolicy Bypass -File .\scripts\legacy\run-web.ps1
```

Open <http://localhost:8765> on your own laptop. You should see the district with 12 drones taking off.

- `-FailureDemo` makes one drone drop out so you can watch the others take over its job.
- `-Port 9000` changes the port.
- After each finished mission (about 3 minutes), the scenario restarts by itself after about 12 seconds. Browsers reconnect automatically.
- Press `Ctrl+C` to stop.

The server serves only `web/` and `assets/`. It does not expose the rest of the repo, your `.env` or any API keys.

## Part 2: Let a friend on another network connect

Your laptop sits behind home or mobile NAT, so your friend cannot reach `localhost:8765` directly. Use one of these.

### Option A: Cloudflare quick tunnel (recommended)

You need no account, no router changes and no firewall rules. The tunnel makes an outbound connection and gives you a public `https://....trycloudflare.com` link. WebSockets work through it, and the page switches to `wss://` automatically.

```powershell
mkdir D:\drone_swarm\.tools
Invoke-WebRequest https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe -OutFile D:\drone_swarm\.tools\cloudflared.exe
D:\drone_swarm\.tools\cloudflared.exe tunnel --url http://localhost:8765 --protocol http2
```

Look for the line with `https://something-random.trycloudflare.com`. Send that link to your friend.

- Keep both windows open: the simulation and the tunnel.
- The link changes every time you restart the tunnel. Anyone who has the link can view the page, so share it only with people you trust, and stop the tunnel when you are done.

### Option B: Tailscale (private, no public link)

1. You and your friend each install Tailscale and sign in to the same tailnet. Share your machine with them if they use a separate account.
2. Your friend opens `http://<your-tailscale-ip>:8765`.
3. Allow inbound TCP 8765 on the Private profile in Windows Firewall when asked.

## Part 3: Instructions for your friend

Copy and send this part.

> **How to watch the drone swarm**
>
> 1. Open the link I sent you in Chrome, Edge, Firefox or Safari. A phone works too.
> 2. Wait a few seconds for the city to load. The first load is about 10 MB. The page only works while I have my laptop on and the simulation running.
> 3. You will see 12 drones searching a ruined city for 3 people who need help (red figures). Each drone shows its current job (EXPLORE, DELIVER, RETURN and so on).
>
> **Controls**
>
> - Drag: rotate the view.
> - Scroll or pinch: zoom.
> - Right-drag or two-finger drag: pan.
> - **Overview** button: reset the camera.
> - **Follow swarm** button: the camera tracks the drones.
>
> **Panels**
>
> - Top left: mission time, sites located, supplies landed, and the current phase.
> - Right: battery and state for each drone.
> - Bottom left: live event log (a drone found a survivor, supplies dropped, and so on).
>
> A green dot next to the title means you are connected. If it turns red, the page reconnects by itself. If it stays red, ask me whether the simulation or the link is still running.
>
> When a mission ends, the scenario restarts after about 12 seconds. This is normal.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Page says "Loading district..." forever | Simulation is not running, or the port is blocked. Check <http://localhost:8765> yourself first. |
| Friend sees the page but the dot stays red | The tunnel or proxy is stripping WebSockets. Restart cloudflared with `--protocol http2`, or use Tailscale. |
| Buildings appear slowly | They are about 47 MB of raw meshes, sent compressed. They stream in behind the live view. |
| `Address already in use` | An old simulation is still running. Run `taskkill /F /IM python.exe` or pick another `-Port`. |
| Link stopped working | Quick-tunnel links last only while cloudflared runs. Start it again and send the new link. |

## What was added

- `legacy/pybullet/web_stream.py`: serves the page and the WebSocket on one port, and broadcasts frames at 12 Hz.
- `web/legacy/index.html` and `web/vendor/`: three.js viewer, with three.js vendored so it needs no CDN.
- `legacy/pybullet/disaster_demo.py --web`: runs the same physics headless in real time and streams it.
- `scripts/legacy/run-web.ps1`: launcher.
