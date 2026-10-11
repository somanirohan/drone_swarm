# Deploying the swarm operations server

Service: `swarm_ops.cli` (`swarm-ops`). Runtime dependency: `websockets` only. No AI key or model is used or needed.
Configuration is environment-only (`swarm_ops/config.py`, names in `.env.example`).

| Variable | Meaning |
|---|---|
| `HOST`, `PORT` | Bind address and port. Render injects `PORT`; the image defaults to `0.0.0.0:8787`. |
| `OPS_CONTROL_TOKEN` | Operator token for control commands (use 24+ random characters). |
| `OPS_INTEGRATION_TOKEN` | Token DisruptionOps uses for `agency_request` / `external_resource_update`. |
| `ALLOWED_ORIGINS` | Comma separated browser origins allowed to open `/ops/ws` cross-origin (same-origin and non-browser clients always pass). |
| `FLEET`, `WORLD` (`pune-district`/`pcmc-region`), `SEED`, `SCENARIO` | Simulation start. |
| `DISRUPTIONOPS_URL`, `DISRUPTIONOPS_TOKEN` | Live reference data (see `integration/DATA-SOURCES.md`). Optional. |
| `LOG_LEVEL` | Python logging level. |

Check the effective configuration (secrets shown only as booleans): `GET /ops/config`; data source: `GET /ops/data-source`; health: `GET /health`.

## Local
`python -m swarm_ops.cli --world pcmc-region` (or `swarm-ops` after `pip install .`). Open http://localhost:8787 on the same machine to be granted control.

## Verified PowerShell launcher for DisruptionOps

```powershell
.\scripts\launch-for-disruptionops.ps1 -Fleet 20
# Optional map and peer coordination:
.\scripts\launch-for-disruptionops.ps1 -World pcmc-region -Coordination decentralized
# Isolated local verification; exits and stops its own server after checking:
.\scripts\launch-for-disruptionops.ps1 -Port 8879 -Fleet 5 -NoTunnel -VerifyOnly
```

Requires Windows PowerShell/PowerShell 7, Python 3.12+ with the requirements installed,
and `cloudflared` in `.tools/cloudflared.exe` or PATH. The launcher uses `swarm_ops.server`,
binds to loopback, and forwards it through a temporary Cloudflare Quick Tunnel.

Before advertising a connection it verifies `/health`, a real snapshot, view-only command
rejection, integration-token authorization and the requested ground-orchestrator control.
For a public URL it also verifies that `/ops/session` cannot disclose the operator token
and that the WebSocket reaches the same exercise as localhost. It fails rather than
publishing an unverified address. Python preflight: `scripts/verify-swarm-connection.py`.

The default `-Orchestrator external` disables the built-in ground dispatcher so DisruptionOps
can own agency allocation. Aerial coordination defaults to `central`; select `decentralized`
or `orchestrated` explicitly, or change it using authorized controls. To demonstrate the
built-in ground dispatcher instead, pass `-Orchestrator internal`. Resetting/changing a
scenario can reset engine authority; an external client should reassert its control after
the run changes. A reused server retains its fleet/world/coordination; the launcher verifies
and reports its actual snapshot rather than assuming the command-line values applied.

After verification, these **private, git-ignored** files are written:

| File under `.cache/share/` | Use |
|---|---|
| `disruptionops.env` | Copy `SWARM_AGENCY`, `SWARM_WS_URL`, `SWARM_INTEGRATION_TOKEN` into the DisruptionOps backend environment |
| `integration-token.txt` | Reused private integration secret; never paste it into public chat/logs |
| `friend-operator-link.txt` | Private browser link granting exercise controls |
| `connection.json` | Secret-free verified viewer/WS URL, actual fleet/mode and owned process IDs; `stopped_at` means that launcher session ended |
| `launch-<timestamp>-<pid>/` | Logs for this invocation |

Tokens are not printed or copied to the clipboard. Keep the launcher terminal open.
Ctrl+C cleans up only the server/tunnel processes started by that invocation; a server
reused from another session is left running. `-VerifyOnly` verifies and then exits, so
its URLs are not a persistent share session. An existing server with another integration
token fails preflight: restart it yourself with the shared token or use a free port.

On the DisruptionOps side, the environment settings require the existing adapter to be
installed/mounted as described in [the patch README](../swarm_ops/integrations/disruptionops_patch/README.md).
The launcher does not apply database migrations or modify that other repository.
Client calls and examples: [integration kit](INTEGRATION-KIT.md) and [friend guide](FRIEND-TEST-GUIDE.md).

Quick Tunnel addresses change on restart and stop working when the tunnel stops; they are
for testing. [Cloudflare Quick Tunnel documentation](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Docker
```
docker build -t swarm-ops .
docker run -p 8787:8787 -e OPS_CONTROL_TOKEN=... -e OPS_INTEGRATION_TOKEN=... swarm-ops
docker compose up            # needs OPS_CONTROL_TOKEN in the environment or .env
docker compose --profile command-center up   # also runs the DisruptionOps stand-in
```
The event journal lives in `/app/.cache` (a named volume in compose). Not built in the authoring environment (Docker was not installed).

## Render
To match the detailed local 3D scene, use `WORLD=pune-district` and leave `SCENARIO`
unset (or set `SCENARIO=rooftop_flood`). The Blueprint defaults to this world.
`WORLD=pcmc-region` intentionally loads the much larger 14 km district map, whose
initial overview hides close-up building detail. Existing Render services must change
their Environment values and redeploy; changing the Blueprint does not override a
manually configured service. Keep `OPS_PUBLIC_DEMO=true` for public read/write controls.

For an open simulation where anyone can use dashboard controls and all WebSocket commands,
set `OPS_PUBLIC_DEMO=true` in the Render service Environment tab and deploy the change.
Open the normal service URL; no control link or tokens are needed. Connect API clients to
`wss://<service>.onrender.com/ops/ws` without a token. `/ops/session` reports
`can_control: true, public_demo: true` without exposing either secret. This mode also opens
run datasets and telemetry to readers. Command validation, rate limits and browser origin
checks still apply. Add external browser origins to `ALLOWED_ORIGINS` as needed.
Set the flag to `false` and deploy again to restore token permissions.

Push the repository, then Render -> New -> Blueprint -> pick the repo (`render.yaml`). It generates both tokens; read them in the service Environment tab. Set `ALLOWED_ORIGINS` if a browser UI on another origin connects, and `DISRUPTIONOPS_*` if used. Health check: `/health`. Free/starter instances sleep or restart: the simulation resets and the journal is lost unless a disk is attached at `/app/.cache`.

Operators on a public URL open `https://<service>.onrender.com/#control=<OPS_CONTROL_TOKEN>`. Public viewers are read-only.

## Connecting DisruptionOps
- WebSocket URL: `wss://<service>.onrender.com/ops/ws?token=<OPS_INTEGRATION_TOKEN>`.
- Swarm side: `OPS_INTEGRATION_TOKEN`, optionally `DISRUPTIONOPS_URL` / `DISRUPTIONOPS_TOKEN` (reads taxonomy, wards, lifelines, units, incidents).
- DisruptionOps side (patch in `swarm_ops/integrations/disruptionops_patch/`, apply migration 040): `SWARM_AGENCY=1`, `SWARM_WS_URL=wss://<service>/ops/ws`, `SWARM_INTEGRATION_TOKEN=<same value>`.
- Protocol: `docs/integration/how-disruptionops-calls-the-swarm.md`.
