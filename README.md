# Disaster-response drone swarm

A Pune/PCMC flood and fire **operations simulator**: a 3D map viewer, 1–100 modeled drones, concurrent ground response from real PCMC units, randomized scenarios and a bidirectional WebSocket (`disruptionops.swarm.v2`). It is a kinematic mission demonstrator that runs entirely on this machine; DisruptionOps integration is a documented, disabled proposal. The older 12-drone PyBullet exercise lives in [`legacy/pybullet/`](legacy/pybullet/docs/).

## Run

```powershell
.\scripts\setup-ops.ps1                    # first time only (or use the bundled .conda-drones)
.\scripts\run-operations.ps1 -Fleet 50     # open http://localhost:8787
```

Direct alternative from the repository root: `python -m swarm_ops.server --port 8787 --fleet 50` (the old `python operations_server.py ...` command still works).

## Share with a friend

```powershell
.\scripts\share-with-friend.ps1            # read-only Cloudflare link + wss:// address, copied to clipboard
.\scripts\share-with-friend.ps1 -NoTunnel  # local check only
```

Friends and other applications: read the [WebSocket handoff](docs/implementation/FRIEND-WEBSOCKET-HANDOFF.md) (copyable Python/JavaScript clients in `examples/`).

## Test

```powershell
.\.conda-drones\python.exe -m unittest discover -s tests -p "test_*.py" -q
```

This runs the engine, server, DisruptionOps contract and legacy tests. Browser and client acceptance: `node tests/operations-browser.cjs` (set `OPS_TEST_URL`) and `python tests/operations_clients_acceptance.py`; see [validation](docs/implementation/validation.md).

## Layout

| Path | What |
|---|---|
| `swarm_ops/` | The simulator package: `engine.py`, `server.py`, `integrations/` (DisruptionOps bridge + patch proposal) |
| `web/ops/`, `web/legacy/`, `web/vendor/` | Operations viewer, legacy PyBullet viewer, vendored three.js |
| `assets/operations/` | Pune world, terrain, heights, agency catalog (`assets/legacy/` holds the PyBullet city) |
| `scripts/` | PowerShell launchers (`scripts/legacy/` for the PyBullet exercise) |
| `tools/` | Data preparation: map, terrain, heights, `build_agency_catalog.py` |
| `tests/`, `examples/` | Unit/acceptance tests; WebSocket client examples |
| `requirements/` | `requirements-ops.txt`, `requirements-geodata.txt`, `requirements.txt` (legacy) |
| `legacy/pybullet/` | 12-drone PyBullet exercise: code, guides (`docs/`), results |
| `docs/` | All documentation: start at the [documentation index](docs/README.md) |
| `gym-pybullet-drones/` | Vendored upstream ([MIT](gym-pybullet-drones/LICENSE)); used by the legacy exercise |

`ops_simulation.py` and `operations_server.py` at the root are thin compatibility shims for `swarm_ops.engine` and `swarm_ops.server`.

## Legacy PyBullet exercise

Twelve Crazyflie drones in a CC0 ruined-city district with rule or optional AI agents: `.\scripts\legacy\setup.ps1`, then `.\scripts\legacy\run-disaster.ps1` (`-Overview`, `-FailureDemo`, `-AI openai|ollama -Model NAME`). See the [agent](legacy/pybullet/docs/AGENTIC-GUIDE.md), [AI](legacy/pybullet/docs/AI-SWARM-GUIDE.md) and [map](legacy/pybullet/docs/DISASTER-GUIDE.md) guides and the [asset attribution](assets/legacy/CITY-SOURCE.md).
