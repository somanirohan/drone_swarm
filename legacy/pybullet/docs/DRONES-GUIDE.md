# Drone simulation: local Windows setup

Repository: https://github.com/learnsyslab/gym-pybullet-drones (main snapshot downloaded October 4, 2026).

From this simulation folder, use:

```powershell
.\scripts\legacy\run-drones.ps1                         # Three drones following circles, graphical window
.\scripts\legacy\run-drones.ps1 -Headless               # Same flight without a window
.\scripts\legacy\run-drones.ps1 -Example pid_velocity   # Velocity command example
.\scripts\legacy\run-drones.ps1 -Example downwash       # Airflow interaction between stacked drones
.\scripts\legacy\run-drones.ps1 -Example mrac           # Model reference adaptive control
.\scripts\legacy\run-drones.ps1 -Example learn          # PPO hover training (potentially long)
.\scripts\legacy\run-drones.ps1 -Example learn -Multiagent
.\scripts\legacy\run-drones.ps1 -Example play -ModelPath 'results\YOUR_RUN\best_model.zip'
```

Flight logs are saved in gym-pybullet-drones/results (NumPy archives and CSV folders). The initial headless run is also saved in results at the workspace root. flight-results.png shows its trajectories and altitude.

## What you can do

- Learn drone dynamics and tune PID gains; compare tracking error and motor RPMs.
- Change circular waypoints to squares, helices, takeoff/landing, or custom routes in examples/pid.py.
- Run multiple drones and design custom formation or coordination controllers.
- Compare ordinary physics with ground effect, drag, downwash, or their combination through Physics in utils/enums.py.
- Train PPO hover policies for one or two drones; the bundled multi-drone learner uses one policy controlling the joint drone actions.
- Build custom Gymnasium tasks by extending BaseRLAviary and defining observations, actions, rewards, termination, and reset behavior. Navigation, obstacle avoidance, racing, and cooperative tasks require your own task logic/training.
- Explore onboard RGB/depth imagery and segmentation capture; a camera observation alone does not provide a navigation policy.
- Change drone URDF parameters and investigate different mass, inertia, thrust, or geometry. Check controller compatibility for each drone model.
- Save telemetry, plot tracking performance, and record simulation images/video.
- Explore Betaflight software-in-the-loop on Ubuntu: the upstream example is explicitly Ubuntu-only.

## Setup notes

The upstream pip installation failed because PyBullet needed Microsoft C++ Build Tools. This workspace instead uses an isolated .conda-drones environment with conda-forge's prebuilt PyBullet/Bullet 3.25 package. The source repository was left unchanged; it is imported through PYTHONPATH rather than installed with its strict PyBullet version requirement. This is a Windows compatibility setup, not an exact reproduction of upstream dependencies.

The launcher sets the required DLL search path and a local Matplotlib cache. Run it from PowerShell. Closing the demo after its default duration is normal.

This is a physics/control research simulator. Real-drone deployment, photorealistic worlds, autonomous navigation, and full drone-racing courses are additional integration or development work.

Learning launcher compatibility: official PyTorch 2.8 CPU build is loaded before NumPy/PyBullet to avoid DLL conflicts with the conda math libraries. The learning test is a short smoke run, not evidence of a fully trained hover policy.
