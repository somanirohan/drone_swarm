# Agentic drone swarm

Real LLM mission planning is now available with `-AI openai` or `-AI ollama`.
See [AI-SWARM-GUIDE.md](AI-SWARM-GUIDE.md) for setup. The default without that flag remains the rule mode described below.

The default launcher now runs 12 independent rule-based agents in the imported ruined-city map. Each agent has its own memory, task estimates, battery estimate, supply state, exploration frontier and decision loop. It receives its own measured position/velocity, sensor discoveries and messages; it does not receive the survivor list or other agents' task dictionaries.

Agent decisions run at 2 Hz. The existing repo PID controller runs at 48 Hz and motor/rigid-body physics runs at 240 Hz. All agents execute in one Python process. A delayed broadcast bus transports peer messages with 150 ms simulated latency. There is no LLM, learned policy, physical radio model or packet-loss model.

## Decision algorithms

- Greedy frontier selection: choose an unvisited grid cell using travel distance plus a soft sector preference. Share coverage and lease frontier claims to reduce duplicated search.
- Task auctions: each available agent bids from its own travel cost and battery estimate. Agents independently resolve bids; accepted claims use deterministic score/ID tie-breaking. This is a custom single-task auction, not an implementation of CBBA.
- Recovery: missing/negative peer heartbeats invalidate task ownership. Available agents rebid and take over uncompleted tasks. Low battery triggers return to base.
- Navigation: climb above the known static district before transit, settle over a sensed site before descent, and use forward range probes to trigger short local detours. This is not SLAM or a general 3D path planner.
- Safety: a shared deterministic velocity filter uses measured neighbour positions/velocities. High-level task decisions remain independent. PID converts the filtered goals into motor RPMs.

Sensing is ideal range/line-of-sight detection using simulator geometry, not camera recognition. Each agent is told the rescue objective has three sites. Battery consumption is an illustrative estimate. Supply parcels are dynamic bodies created on release; carried mass is not modeled.

## Run

In PowerShell from this folder:

    powershell -ExecutionPolicy Bypass -File .\scripts\legacy\run-disaster.ps1

To demonstrate autonomous task recovery:

    powershell -ExecutionPolicy Bypass -File .\scripts\legacy\run-disaster.ps1 -FailureDemo

The first task owner becomes unavailable for rescue work and returns under PID control. Another agent takes its task. This simulates mission-agent unavailability, not failed motors, a disabled flight controller or a drone crash.

Use -Overview to start with the full city visible, -Scripted for the previous central controller, and -Headless -Duration 210 -FailureDemo for physics validation. Press F in the simulator to switch cameras. Missions end with a hold at the launch pads; restart the launcher for another mission.

## Inspect decisions

Result files are written to `legacy/pybullet/results/`. agentic-events.json records discoveries, ownership changes, unavailability, detours, releases and landings. disaster-live-status.json contains live agent states, battery estimates, coverage, messages and replans. disaster-validation.json contains the latest headless physics metrics, including contacts and minimum separation. Run `.conda-drones\python.exe -m unittest tests.legacy.test_agentic_swarm -v` from the repository root to check message latency, ownership recovery, frontier memory and detour/low-battery decisions.

Final validation: normal and availability-recovery runs both completed all three deliveries and returned all 12 drones. Minimum drone separation was 0.382 m; unsafe contact steps were zero. Expected rescue-pad contact steps were 51 in normal mode and 70 in recovery mode. Drone 3's task transferred to Drone 1 after 0.52 simulated seconds. See agentic-normal-validation.json, agentic-failure-validation.json and agentic-failure-events.json. All four agent logic tests also passed.
