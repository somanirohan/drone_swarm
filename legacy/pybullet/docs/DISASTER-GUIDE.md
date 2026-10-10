# Swarm disaster simulation

`disaster_demo.py` uses the repository's unmodified CF2X Crazyflie URDF and mesh, mass, inertia, motor thrust and torque parameters. No spheres or oversized crosses cover the drones. The default overview camera shows the imported district. Press F to switch between follow and overview cameras.

Twelve drones fly as three four-drone teams at separated cruise altitudes. A feedback controller advances sector search lanes when the measured team positions reach them. Formation cohesion and neighbour velocity constraints use actual position and velocity. A shared coordinator assigns the nearest available drone after a survivor is sensed. Delivery requires arrival and low speed; supplies are then released as dynamic PyBullet bodies. Landing is confirmed from their measured position and velocity. Once all deliveries land, the fleet returns to its launch pads.

Flight uses the repository's cascaded PID controller to command four motor RPMs. Motion is integrated by PyBullet with the repo drag model. Downwash is disabled because the repository approximation becomes singular for near-level neighbours.

Scope: the environment is a local district assembled from imported ruined-city assets. Detection uses an ideal range-limited line-of-sight sensor with simulator survivor positions; it is not camera recognition. Communication is a centralized shared state, with no packet loss or range model. Planning assumes known safe cruise height, with local drone separation; it is not a general building avoidance planner. Payloads are created on release; carried payload mass and mounting are not modeled. Fire, smoke and flood are visual effects. This is a simulated swarm controller, not deployable rescue autonomy.

Run using the environment configured by `scripts\legacy\run-drones.ps1`, or use `scripts\legacy\run-disaster.ps1`. Headless validation: `scripts\legacy\run-disaster.ps1 -Headless -Duration 110`. Validation metrics are written to `legacy/pybullet/results/disaster-validation.json` and telemetry is printed every ten simulated seconds.

## Imported earthquake map
The default scene now uses seven textured prebuilt ruined-city buildings by Majadroid (CC0), replacing the primitive diorama. Source and conversion details are in assets/CITY-SOURCE.md. The district covers 32.5 x 25.5 m; imported buildings are up to 8.46 m high. Drones retain their original size and physics. Cruise height is selected from the tallest imported building plus 1.5 m clearance. Teams climb before traversing buildings. The overview camera is the default; press F for close follow. Range-limited simulated sensing now uses a 3.6 m horizontal radius. Run headless for 230 seconds to validate the longer mission.

This is a locally arranged map using prebuilt asset geometry, not the original Blender scene and not an earthquake dynamics simulator. All imported ruined buildings have matching static triangle-mesh collision geometry. The archived primitive demonstration is disaster_legacy.py.


Visibility update: launch now defaults to close follow so the small drones are visible. Use scripts\legacy\run-disaster.ps1 -Overview for the whole map. Each mission takes approximately 3.5 minutes, then the drones hold at the rescue pads; restart the script for another mission.

Agentic mode is now the default. See AGENTIC-GUIDE.md for independent drone decisions, peer auctions, failure recovery, test results and simulation limits. Use -Scripted for the prior central controller.
