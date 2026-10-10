# Verified local build

Validation date: 10 October 2026. Runtime: the repository's local Python 3.12 environment, Chrome/Playwright on this Windows laptop. DisruptionOps reference checkout remains clean; no live backend or production agency was contacted.

| Suite | Passed |
|---|---:|
| Real localhost HTTP/WebSocket transport, authorization and replay | 22 |
| Mission decisions, handoffs, evacuation, energy, supplies and failure handling | 27 |
| Optional future connector and copied adapter contract, local stubs only | 19 |
| Existing agentic/AI/Jev planner regression tests | 15 |
| **Total automated tests** | **83** |

Reproducible commands:

The friend handoff's JavaScript/Python reference clients additionally passed **20 acceptance checks** (8 Python, 12 JavaScript) against an isolated real localhost server. These cover read-only access, cached world geometry, operator permission, commands and observed state, duplicate/stale-run protection, reset handling and automatic JavaScript reconnection. This is separate from the 83 unit/transport tests above. The live 50-aircraft demo was also read without issuing a command.

```powershell
.\.conda-drones\python.exe -m unittest discover -s tests -p "test_*.py" -q   # everything (ops, server, DisruptionOps, legacy)
.\.conda-drones\python.exe -m unittest discover -s tests -p 'test_disruptionops_*.py' -q
.\.conda-drones\python.exe -m unittest tests.legacy.test_agentic_swarm tests.legacy.test_ai_planner tests.legacy.test_jev_planner -q
.\.conda-drones\python.exe tests/operations_acceptance.py
node tests/operations-browser.cjs
.\.conda-drones\python.exe tests/operations_clients_acceptance.py
```

The actual prepared Pune world passed **nine end-to-end deterministic replays**, each with 50 drones: all three scenarios at seeds 42/91, plus representative crash, battery and link-loss runs. Every required ground service and modeled person transfer completed within 600 simulated seconds. Link-loss delivery/recall uncertainty remained recorded. See `acceptance-results.json` for case results and matching engine/world hashes. All 1,678 prepared road points lie within the terrain bounds.

Browser acceptance passed against the running localhost service with **50 aircraft** and zero JavaScript errors. It exercised all three scenario selectors, randomization, simultaneous ground allocation, pause/resume, route/coverage controls, selection/cameras, crash reassignment, offline control disabling/reconnection and desktop/mobile layouts. It recorded actual WebSocket frames, rather than substituting fixture telemetry. Evidence: `browser-results.json` and `screenshots/`.

Performance is qualified by workload. A normal 100-aircraft tracked fleet, with up to 26 airborne, averaged 6.693 ms combined CPU simulation/snapshot serialization with 18.131 ms p95. A dense coverage stress with up to 90 airborne averaged 39.412 ms, with 149.420 ms p95 and ten of 100 samples exceeding the 100 ms telemetry budget under concurrent desktop load. **The current engine does not meet a strict 100-aircraft airborne real-time deadline.** The measurement excludes rendering, network transfer, perception and flight controllers. These are local kinematic demonstrations, not hardware flight validation.

The original drone exercise's regression tests also pass; its existing code was preserved. The new interface uses prepared geographic assets, predicted/estimated heights and procedural details. It is not an exact textured Google 3D reconstruction.

Public deployment was attempted only after local verification. Automatic execution approval review rejected launching `cloudflared` with the stated reason **“blocked by policy.”** No public URL was created. The optional `-Tunnel` runner and development instructions are present; the current verified service is local at `http://localhost:8787`.
