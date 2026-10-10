# Folder layout: current problems, target, and a safe move plan

Written 2026-10-10 as a plan. **Executed 2026-10-10** in five commits ("Reorg 1/5" to "Reorg 5/5"); see section 0 for the final tree and the differences from the plan. Sections 1 to 6 are kept as the original plan.

## 0. Executed result

```
drone_swarm/
  README.md                 short overview, run/share/test commands
  ops_simulation.py         SHIM -> swarm_ops.engine (sys.modules alias; old imports keep working)
  operations_server.py      SHIM -> swarm_ops.server (`python operations_server.py --port N --fleet N` still works)
  swarm_ops/                __init__.py  engine.py  server.py
    integrations/           disruptionops_bridge.py  requirements-tests.txt
                            disruptionops_patch/backend/{app/drone/operations.py, migrations/039_swarm_operations_demo.sql}
  web/                      ops/ (operations.html/.css/.js)  legacy/ (index.html)  vendor/ (three.js)
  assets/                   operations/  legacy/ (city-ready/, apocalyptic-city/, CITY-SOURCE.md)
  scripts/                  run-operations.ps1  share-with-friend.ps1  setup-ops.ps1
    legacy/                 run-disaster.ps1  run-drones.ps1  run-web.ps1  setup.ps1  start-all.ps1
  tools/                    prepare_operations_{map,heights,terrain}.py  build_agency_catalog.py
  requirements/             requirements.txt (legacy)  requirements-ops.txt  requirements-geodata.txt
  tests/                    test_ops_simulation.py  test_operations_server.py  test_disruptionops_{bridge,contract}.py
                            operations_acceptance.py  operations_clients_acceptance.py  operations-browser.cjs  operations-client-example.mjs
    legacy/                 __init__.py  test_agentic_swarm.py  test_ai_planner.py  test_jev_planner.py
  examples/                 unchanged
  legacy/pybullet/          disaster_demo.py ... web_stream.py  plot-flight.py
    tools/                  prepare_city_assets.py
    docs/                   the seven legacy guides
    results/                generated JSON (git-ignored)
  docs/                     README.md (index)  PLAN.md  CAPABILITIES.md  ALGORITHMS.md  FOLDER-LAYOUT.md
                            implementation/  integration/  research/  reference/
    history/resume.md       local only, deliberately not committed (.git/info/exclude)
  gym-pybullet-drones/      vendored, not moved
```

Differences from the plan below:

- Acceptance scripts stay directly in `tests/` (not `tests/acceptance/`), and `docs/implementation/*.json` results were not moved; their commands and output paths are unchanged.
- The server serves `/` from `web/ops/operations.html` and `/legacy` from `web/legacy/index.html`; the old URLs `/web/operations.html|.css|.js` and `/web/index.html` are aliased to the new files (tested in `tests/test_operations_server.py`), so already-shared links work. Traversal is still rejected.
- Legacy modules import each other by bare name; `tests/legacy/*` put `legacy/pybullet` on `sys.path`. Legacy result files are written to `legacy/pybullet/results/`. `assets/legacy/city-ready/manifest.json` texture paths now start with `assets/legacy/`.
- The launch scripts start the server with `python -m swarm_ops.server` from the repository root.
- One command runs all unit tests: `python -m unittest discover -s tests -p "test_*.py"` (100 tests at the time of the move).
- The shims can be removed once nobody uses the old import or start commands.

## 1. What is wrong today

About 45 entries at the repository root mix three different projects:

1. the **Pune operations simulator** (new, the one that matters): `ops_simulation.py`, `operations_server.py`, `web/operations.*`, `prepare_operations_*.py`, `run-operations.ps1`, `share-with-friend.ps1`, `setup-ops.ps1`, `requirements-ops.txt`, two test files;
2. the **legacy 12-drone PyBullet exercise**: `disaster_demo.py`, `disaster_legacy.py`, `agentic_swarm.py`, `ai_planner.py`, `jev_planner.py`, `swarm_mission.py`, `swarm_demo.py`, `city_map.py`, `web_stream.py`, `plot-flight.py`, three tests, six guides, three JSON result files, `run-disaster.ps1`, `run-drones.ps1`, `run-web.ps1`, `setup.ps1`, `start-all.ps1`;
3. **docs and scratch**: `resume.md` (a chat transcript), `docs/`, `.tools/` (a Graphviz build, cloudflared, draw.io, browser captures), `.cache/`.

Also: two test styles (root `test_*.py` and `tests/`), and `docs/implementation/` holding both guides and generated result JSON.

## 2. Target layout

```
drone_swarm/
  README.md                      short: what it is, how to run, where docs are
  swarm_ops/                     the simulator package (new code lives here)
    __init__.py
    engine.py                    <- ops_simulation.py (later split, see PLAN Phase 2)
    server.py                    <- operations_server.py
    (later) world.py fleet.py dispatch.py handoff.py signals.py energy.py faults.py scenarios/
    integrations/                <- integrations/ (bridge + patch proposal)
  web/
    ops/                         <- operations.html/.css/.js
    legacy/                      <- index.html (PyBullet viewer)
    vendor/                      three.js etc. (shared, unchanged)
  assets/
    operations/                  unchanged (pune-world.json, terrain.json, height overlay, agency-catalog.json)
    legacy/city-ready/           <- assets/city-ready
    legacy/apocalyptic-city/     <- assets/apocalyptic-city
    CITY-SOURCE.md               -> assets/legacy/CITY-SOURCE.md
  tools/                         one-off data preparation
    prepare_operations_map.py  prepare_operations_heights.py  prepare_operations_terrain.py
    (later) build_agency_catalog.py   generates the catalog from DisruptionOps migration 029
  scripts/                       all PowerShell
    run-operations.ps1  share-with-friend.ps1  setup-ops.ps1
    legacy/ run-disaster.ps1  run-drones.ps1  run-web.ps1  setup.ps1  start-all.ps1
  tests/
    test_ops_simulation.py  test_operations_server.py        <- root
    test_disruptionops_bridge.py  test_disruptionops_contract.py   (already here)
    acceptance/ operations_acceptance.py  operations_clients_acceptance.py  operations-browser.cjs  operations-client-example.mjs
    legacy/ test_agentic_swarm.py  test_ai_planner.py  test_jev_planner.py
  examples/                      unchanged (WebSocket clients)
  legacy/pybullet/               12-drone exercise
    disaster_demo.py  disaster_legacy.py  agentic_swarm.py  ai_planner.py  jev_planner.py
    swarm_mission.py  swarm_demo.py  city_map.py  web_stream.py  plot-flight.py
    results/ agentic-events.json agentic-ai-events.json disaster-validation.json
    docs/ AGENTIC-GUIDE.md AI-SWARM-GUIDE.md DISASTER-GUIDE.md DRONES-GUIDE.md FRIEND-GUIDE.md WEB-GUIDE.md PRODUCTION-ARCHITECTURE.md
  gym-pybullet-drones/           vendored upstream; DO NOT MOVE (see risks)
  docs/                          see docs/README.md
    PLAN.md CAPABILITIES.md FOLDER-LAYOUT.md README.md
    integration/  implementation/  research/  reference/
    history/resume.md            <- resume.md (transcript, kept for expectations)
  requirements/                  requirements.txt (legacy) requirements-ops.txt requirements-geodata.txt
  .tools/  .cache/  .conda-drones/  .venv*/   unchanged, git-ignored where already
```

## 3. Move map

| Old path | New path | Notes |
|---|---|---|
| `ops_simulation.py` | `swarm_ops/engine.py` | Keep a one-line shim `ops_simulation.py` for one release if external code imports it. |
| `operations_server.py` | `swarm_ops/server.py` | `ROOT` becomes the parent of the package. |
| `integrations/` | `swarm_ops/integrations/` | Rename patch migration to `030_...` at the same time (upstream 029 is taken, `bd21179`). |
| `web/operations.html`, `.css`, `.js` | `web/ops/…` | Update `<script>`/`<link>` and the three.js import path to `../vendor/`. |
| `web/index.html` | `web/legacy/index.html` | Legacy viewer; adjust relative imports. |
| `prepare_operations_map.py`, `prepare_operations_heights.py`, `prepare_operations_terrain.py` | `tools/` | Each has `ROOT = Path(__file__).resolve().parent`; becomes `.parent.parent`. |
| `prepare_city_assets.py` | `legacy/pybullet/tools/prepare_city_assets.py` | `root=Path(__file__).parent/'assets'/…` must point at `assets/legacy/…`. |
| `test_ops_simulation.py`, `test_operations_server.py` | `tests/` | Imports become `from swarm_ops.engine import …`. |
| `test_agentic_swarm.py`, `test_ai_planner.py`, `test_jev_planner.py` | `tests/legacy/` | Import from `legacy.pybullet` (add `__init__.py`) or add the folder to `sys.path`. |
| `disaster_demo.py`, `disaster_legacy.py`, `agentic_swarm.py`, `ai_planner.py`, `jev_planner.py`, `swarm_mission.py`, `swarm_demo.py`, `city_map.py`, `web_stream.py`, `plot-flight.py` | `legacy/pybullet/` | They import each other by bare name (`from swarm_mission import …`), so moving them **together** keeps imports working when run from that folder. |
| `AGENTIC-GUIDE.md`, `AI-SWARM-GUIDE.md`, `DISASTER-GUIDE.md`, `DRONES-GUIDE.md`, `FRIEND-GUIDE.md`, `WEB-GUIDE.md`, `PRODUCTION-ARCHITECTURE.md` | `legacy/pybullet/docs/` | Root README links to several. |
| `agentic-events.json`, `agentic-ai-events.json`, `disaster-validation.json` | `legacy/pybullet/results/` | Generated by legacy runs. |
| `assets/city-ready`, `assets/apocalyptic-city`, `assets/CITY-SOURCE.md` | `assets/legacy/…` | Used by `city_map.py`, `prepare_city_assets.py`, `web_stream.py` (serves `assets/`). |
| `run-operations.ps1`, `share-with-friend.ps1`, `setup-ops.ps1` | `scripts/` | They use `$PSScriptRoot` as the repo root: change to `Split-Path $PSScriptRoot`. |
| `run-disaster.ps1`, `run-drones.ps1`, `run-web.ps1`, `setup.ps1`, `start-all.ps1` | `scripts/legacy/` | Same `$PSScriptRoot` fix; they call the legacy `.py` files by path. |
| `requirements.txt`, `requirements-ops.txt`, `requirements-geodata.txt` | `requirements/` | `setup*.ps1` reference them. |
| `resume.md` | `docs/history/resume.md` | Contains no secrets as far as read; it is the user's chat. Keep out of the root. |
| `docs/implementation/*.json` (acceptance, browser results) | `docs/implementation/results/` | `tests/operations_acceptance.py` writes to `docs/implementation/acceptance-results.json`; update. |

Stay as they are: `gym-pybullet-drones/`, `examples/`, `.tools/`, `.cache/`, `.gitignore`, `.env`, `.env.example` (do not read or move `.env`).

## 4. References that must change

| Where | What |
|---|---|
| `scripts/run-operations.ps1` line ~6, ~7, ~18 | `assets\operations\pune-world.json` stays; `prepare_operations_map.py` becomes `tools\prepare_operations_map.py`; `operations_server.py` becomes `python -m swarm_ops.server` (run from the repo root so the package imports). |
| `scripts/share-with-friend.ps1` line ~49-53, 106-107 | same two script paths; docs path `docs/implementation/FRIEND-WEBSOCKET-HANDOFF.md` unchanged. |
| `scripts/run-disaster.ps1`, `run-web.ps1` | `disaster_demo.py` path becomes `legacy\pybullet\disaster_demo.py`; `$PSScriptRoot\.conda-drones` becomes parent. |
| `swarm_ops/server.py` | `ROOT`, `/web/operations.html` → `/web/ops/operations.html`, `/legacy` → `/web/legacy/index.html`, `world_path`, `.cache` runtime dir (keep at repo root). `from ops_simulation import OperationsSimulation` → `from swarm_ops.engine import …`. |
| `swarm_ops/engine.py` | `CATALOG_PATH = Path(__file__).parent / "assets" / …` → repo root `assets/operations/agency-catalog.json` (`.parent.parent`). |
| `tests/test_operations_server.py`, `tests/test_ops_simulation.py` | imports; the `/web/%2e%2e/.env` traversal test still valid. |
| `tests/operations_acceptance.py` | `ROOT = parents[1]` becomes `parents[2]` if moved into `tests/acceptance/`; `engine_sha256` digest path `ops_simulation.py` → `swarm_ops/engine.py`; output path. |
| `tests/operations_clients_acceptance.py`, `operations-browser.cjs` | server start command and URL paths. |
| `tests/test_disruptionops_contract.py` line ~63 | path to `integrations/disruptionops_patch/...` becomes `swarm_ops/integrations/...`. |
| `tests/test_disruptionops_bridge.py` | `from integrations.disruptionops_bridge import …` becomes `swarm_ops.integrations…`. |
| `legacy/pybullet/web_stream.py` | `ROOT`, `SERVED = {'web':…, 'assets':…}` → repo-level `web/` and `assets/`. |
| `legacy/pybullet/city_map.py` | `READY = ROOT/'assets'/'city-ready'` → `assets/legacy/city-ready`. |
| `README.md` (root) | links to `docs/implementation/...`, `AI-SWARM-GUIDE.md`, `AGENTIC-GUIDE.md`, `DISASTER-GUIDE.md`, `assets/CITY-SOURCE.md`; run commands. |
| `docs/implementation/*.md` | mentions of `ops_simulation.py`, `operations_server.py`, `test_*.py` paths and PowerShell commands (validation.md lists the exact test commands). |
| `docs/research/.../repository-integration.md` and `README.md` | file path references (not checked in detail: **UNVERIFIED**). |
| `.gitignore` | nothing required; add `.cache/`, `results/` if wanted. |
| `.tools/*` | build helper scripts reference `docs/research/...` only; unaffected. |

Python editable install: `gym_pybullet_drones` is imported by bare name and there is no `sys.path` code in the repo, so it is presumably installed in `.conda-drones`/`.venv-swarm` in editable mode pointing at `gym-pybullet-drones/`. **UNVERIFIED.** Moving that folder could break the import; hence it stays.

## 5. Safe execution order

1. Wait until BACKEND and FRONTEND agents report done; run the full test suite and save the output (baseline).
2. Create a branch (do not work on `main`). Commit current work first so the move is its own commit.
3. `git mv` in this order, committing after each group so a failure points at one group:
   1. docs only (`resume.md`, guides to `legacy/pybullet/docs/`, results JSON) and fix doc links;
   2. legacy Python + tests + legacy PowerShell + legacy assets (they are self-contained);
   3. `tools/` and `scripts/` (PowerShell paths);
   4. `swarm_ops/` package: create `__init__.py`, move `ops_simulation.py` and `operations_server.py`, fix imports and `ROOT`;
   5. `web/` split into `ops/` and `legacy/`.
4. After each group run: `python -m unittest tests.test_ops_simulation tests.test_operations_server -q`; for group 2 also the legacy tests; for group 5 run `node tests/operations-browser.cjs`.
5. Run the three acceptance scripts and compare against the baseline (replay hashes in `acceptance-results.json` include `engine_sha256`, which will change only because the path/bytes changed; compare scenario outcomes, not the digest).
6. Start the server with the new script and open the viewer; also open `/legacy`.
7. Keep one-line shims (`ops_simulation.py`, `operations_server.py`) for a release if friends' clients start the server by the old command; remove after the README is updated.
8. Phase 2 splits `engine.py` into modules **after** the move, never in the same commit.

## 6. Risks

- Lost work if the move overlaps with edits to `ops_simulation.py` or `web/operations.*`.
- Windows long paths and `git mv` on case-only renames: none needed here.
- `__pycache__`, `.conda-drones`, `.venv*` are git-ignored and must not be moved.
- The friend handoff (`FRIEND-WEBSOCKET-HANDOFF.md`) names URLs and commands; update it so already-shared instructions are not silently wrong.
