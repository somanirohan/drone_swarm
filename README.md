# Agentic disaster-response drone swarm

Twelve Crazyflie CF2X drones explore an earthquake district built from textured CC0 ruined-city assets, discover rescue sites, coordinate supply deliveries and return to base. The simulation uses the vendored gym-pybullet-drones motor physics and PID controller.

Two mission modes are available:

- **Rule agents (default):** independent frontier memory, delayed peer messages, distributed rescue auctions and reassignment after agent unavailability.
- **AI agents (optional):** an OpenAI or local Ollama model chooses each drone's exploration frontier, rescue-bid participation and return goal. Validated mission tools feed the same auctions and flight controller. Requires a configured model backend; there is no silent rule-search fallback in AI mode.

## Windows setup and run

Install Python 3.12, then run in PowerShell:

```powershell
git clone https://github.com/tusharsingh-spring/drone_swarm.git
cd drone_swarm
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\run-disaster.ps1
```

Use `-Overview` for the whole district, or press F to change the camera. Use `-FailureDemo` to demonstrate task reassignment. Close the simulation window to stop.

For AI mode, set `OPENAI_API_KEY` privately in your PowerShell environment and select a function-calling model available to your API account:

```powershell
.\run-disaster.ps1 -AI openai -Model YOUR_MODEL_NAME
```

Or start Ollama with an installed instruction model:

```powershell
.\run-disaster.ps1 -AI ollama -Model YOUR_INSTALLED_MODEL
```

API usage may incur charges. The default AI run caps requests at 120; `-AIMaxRequests` changes the cap. Read [AI setup and behavior](AI-SWARM-GUIDE.md).

## Verification

```powershell
.\.venv-swarm\Scripts\python.exe -m unittest test_agentic_swarm test_ai_planner -v
.\run-disaster.ps1 -Headless -Duration 220
```

The rule-mode normal and unavailability runs previously completed all three deliveries and returned all twelve drones with zero unsafe contact steps. AI integration tests use mocked model responses; live AI performance has not yet been validated.

## Scope and sources

Flight control runs at 48 Hz; physics runs at 240 Hz. Sensing uses idealized simulator range/line-of-sight checks, battery is an illustrative estimate, and all drone agents execute in one process. This is a simulation, without camera recognition, SLAM or a learned flight policy. The map is assembled from prebuilt assets rather than a complete third-party disaster simulator.

- [Agent behavior](AGENTIC-GUIDE.md)
- [Disaster map](DISASTER-GUIDE.md)
- [Map source and CC0 attribution](assets/CITY-SOURCE.md)
- Vendored drone code: [gym-pybullet-drones](https://github.com/learnsyslab/gym-pybullet-drones), retaining its [MIT license](gym-pybullet-drones/LICENSE). Local upstream files include controller modifications.

Original FBX/Blender authoring files are excluded; prepared meshes and required textures are included. Download the original CC0 pack from the linked source to regenerate assets with `prepare_city_assets.py` (requires `assimp_py`). Local Python environments, caches, logs and credentials are excluded.
