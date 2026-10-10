# Drone Swarm → Production Disaster-Response System
Research and architecture brief. **Nothing is implemented yet.** This is the learning and decision document.

Status of claims: items marked **[verified]** came from a web search result. Items marked **[my recommendation]** or **[verify]** are engineering judgement or background knowledge and need checking before you rely on them. I have **not watched** the videos below. Titles and descriptions come from search results only.

---

## 1. Where the current project stands (and why it feels rigged)

| Today (PyBullet demo) | Production reality |
|---|---|
| Perfect sensor, fixed 3 survivors, fixed map | Noisy, partial, contradictory data |
| One process simulates drones, comms, planner and UI | Separate processes and machines connected by unreliable links |
| Message bus with 0.15 s latency and no loss | Packet loss, dropouts, partitions, stale data |
| Jev picks a frontier from a pre-sorted menu | Humans + AI decide under uncertainty and constraints |
| Safety = a pairwise separation filter | Onboard failsafes, geofences, airspace rules, regulators |
| Restarts the same scenario | Long-lived missions, audit trail, handover between shifts |

The core idea survives: independent agents, auctions, a typed AI planner and deterministic safety underneath. The *runtime around it* has to change completely.

---

## 2. Design principles (the "core concepts" to learn)

1. **Autonomy at the edge, coordination at the centre.** Each drone must stay safe and useful with **zero** connection to the backend. The link is an optimisation, not a dependency. Disaster zones are exactly where networks are down. DARPA SubT teams had to run robots for long stretches with no comms. **[verified]** (see §7)
2. **AI is advisory; deterministic code has the veto.** The LLM or Jev proposes mission goals (where to search, who bids). Validators and a safety layer accept or reject them. Never let it emit motor, attitude or waypoint commands directly. Your repo already does this. Keep it, and harden it. Research on LLM-assisted ISR swarms describes the same split: the LLM handles mission reasoning while conventional controllers keep navigation, collision avoidance and flight safety. **[verified]**
3. **Human-on-the-loop.** The incident commander sees what the swarm intends to do and can veto, pause or recall at any time. Notre Dame's DroneResponse work distinguishes human-in-the-loop (wait for a human), human-on-the-loop (act, human can cancel) and joint decision-making. **[verified]**
4. **Everything is an event.** Every observation, proposal, decision, override and command is an immutable, timestamped event. This gives replay, audit, debugging and "why did it do that?" for the post-incident review. **[my recommendation]**
5. **Assume the network lies.** Messages are late, duplicated, reordered or lost. Commands must be idempotent, carry sequence numbers and have expiry times. State must converge after a partition (CRDT-style or last-writer-wins with versions).
6. **Standards over custom formats** at every boundary (MAVLink, CAP/EDXL, Remote ID, CoT/TAK), so it plugs into real emergency systems.

---

## 3. Target architecture (layered)

```
┌─────────────────────────────────────────────────────────────────────┐
│ L6  OPERATOR CONSOLE (web)   map · fleet · AI proposals · approve/veto│
└───────────────▲─────────────────────────────────────────────────────┘
                │ WebSocket (live push) + REST (commands, history)
┌───────────────┴─────────────────────────────────────────────────────┐
│ L5  INTEGRATION GATEWAY   CAD/911 · ICS roles · CAP/EDXL · TAK/CoT   │
├─────────────────────────────────────────────────────────────────────┤
│ L4  DECISION LAYER                                                   │
│    Jev / LLM planner (advisory) → Validator → Task allocator (auction│
│    / CBBA) → Policy engine (airspace, battery, priority rules)       │
├─────────────────────────────────────────────────────────────────────┤
│ L3  MISSION ORCHESTRATOR   event-sourced world model, mission state, │
│    task queue, command issuing (idempotent, expiring), audit log     │
├─────────────────────────────────────────────────────────────────────┤
│ L2  LINK / TRANSPORT   LTE/5G · mesh radio · satellite · local WiFi  │
│    fleet pub/sub bus (MQTT / NATS / Zenoh)  ·  store-and-forward     │
├─────────────────────────────────────────────────────────────────────┤
│ L1  ONBOARD AGENT (companion computer)  MAVSDK / ROS 2              │
│    local planner · local task auction · detection · lost-link logic  │
├─────────────────────────────────────────────────────────────────────┤
│ L0  FLIGHT CONTROLLER  PX4 or ArduPilot   (RC-loss, battery, geofence│
│    failsafes live HERE and cannot be overridden by anything above)   │
└─────────────────────────────────────────────────────────────────────┘
```

**How this maps to your current files**

| Current file | Becomes |
|---|---|
| `agentic_swarm.py` (`DroneAgent`) | L1 onboard agent, runs on each drone's companion computer |
| `MessageBus` | L2 bus (real network instead of an in-memory heap) |
| `ai_planner.py` / `jev_planner.py` | L4 advisory planner, behind a service API |
| `AgenticSwarm.update` safety filter | L1 local avoidance and L0 failsafes (never only in the backend) |
| `disaster_demo.py` + PyBullet | A **simulator/scenario generator** for testing, not the flight stack |
| `web_stream.py` | L6 gateway, extended with command channels and auth |

---

## 4. Communication: what to use where **[my recommendation, verify against your constraints]**

| Link | Suggested tech | Why |
|---|---|---|
| Drone flight controller ↔ companion computer | **MAVLink** via MAVSDK or MAVROS | Standard for PX4/ArduPilot. MAVSDK supports multi-vehicle control. **[verified]** |
| Drone ↔ drone / drone ↔ edge | **Zenoh** or **MQTT** (ROS 2 + Zenoh if you go ROS) | Zenoh is reported to cope better than default DDS on lossy wireless and has far less discovery traffic. **[verified in papers and vendor-adjacent articles, so check the numbers]** |
| Backend internal | **NATS JetStream** or Kafka (event log) + Postgres/TimescaleDB | Replayable event stream, durable audit |
| Backend → browser | **WebSocket** (live telemetry/proposals), REST/gRPC for commands | You asked about a websocket: yes for the UI. **Do not** run flight-critical commands over a browser WebSocket path. Commands go UI → backend (authenticated) → orchestrator → drone, with acks. |
| Radio fallback | Mesh radio (e.g. Doodle Labs / Silvus class), LoRa for low-rate telemetry, satellite for command and control of last resort | SubT teams relied on mesh networking and dropped relay nodes. **[verified]** |
| Integration | **CAP** (alerts in), **EDXL** family (resource/sitrep out), **CoT/TAK** (situational picture) | Standards used by responders. CAP is ITU X.1303. **[verified]** |

**WebSocket vs alternatives for the UI:** WebSocket is right for a live operator console. Use SSE if you only push one-way. Always add reconnect with resume-from-sequence-number, because the operator's laptop on a field hotspot *will* drop.

---

## 5. Decision layer: making the AI decisions realistic

The "rigged" feeling comes from the planner choosing from a menu already sorted by our heuristic. Fixes:

1. **Give it facts, not a ranked menu.** Battery, ETA, wind, link quality, survivor priority and confidence, airspace restrictions, what peers are doing.
2. **Real trade-off questions** with no obvious answer. Which of two sites first? Hold a drone as a comms relay? Abort a search sector under degraded battery? Accept a low-confidence detection?
3. **Uncertainty everywhere.** Detections carry a confidence and can be false. Survivors have a triage priority. Information arrives late or never.
4. **Proposal → validate → approve → execute.** Each AI proposal is recorded with the observation it saw, the validator verdict, and (for high-impact actions) the human approval.
5. **Let the AI affect allocation.** Today Jev only enters a bid and the nearest-cost auction decides. Options: Jev ranks eligible drones, with the auction as a validity and consistency check.
6. **Always have a no-AI fallback.** If Jev or the LLM is slow or down, a deterministic planner takes over. Today an unreachable API means silence (`jev_planner.py` docstring). In production that must degrade gracefully and visibly.
7. **Evaluate it.** Randomised scenario suite, scoring (time to locate, time to aid, lives-at-risk proxy), with and without AI. This is how you prove it is not rigged *or* useless.

**Task allocation background.** The consensus-based bundle algorithm (CBBA) is a distributed two-stage method: agents build bundles of tasks, then run consensus to resolve conflicts. The max-sum version has a proven bound of at least 50% of optimal. The base algorithm assumes single-robot tasks and submodular scores, and has variants for multi-robot tasks, dynamic re-tasking (ACBBA) and reduced communication (event-driven CBBA). **[verified from papers]** Your current auction is a simpler single-item auction. CBBA is the natural upgrade path.

---

## 6. Safety, regulation, security **[verify with a domain expert before any real flight]**

- **Onboard failsafes (L0):** RC/link loss behaviour, low-battery return, geofence, max altitude. Configured in PX4/ArduPilot, tested in SITL.
- **Runtime assurance:** an independent monitor that can reject or override the AI and orchestrator. A 2025 paper on "cognition envelopes" for UAS argues for independent safeguards because LLM hallucinations can produce unsafe decisions. **[verified]**
- **Airspace:** operations beyond line of sight, swarm operations and emergency flights usually need waivers or authorisations. Remote ID (ASTM F3411) is required for most drones in the US. Network Remote ID goes through service providers, and UTM interoperability is covered by ASTM F3548. **[verified]** Rules differ by country and change, so check with a regulator.
- **Security:** Remote ID has no cryptographic protection. **[verified]** Treat the command link as hostile: mutual TLS, signed commands, per-drone keys, replay protection, role-based access (ICS roles), full audit log. MAVLink 2 supports message signing **[verify]**.
- **Data:** video of disaster victims is sensitive. Define retention and access rules early.
- **Human factors:** operators must understand why the AI proposed something. Firefighters' perception studies of autonomous drones exist from field trials. **[verified exists; see §7 papers]**

---

## 7. Learning resources

### 7.1 YouTube (links came from search results; I have not watched them)

**Incident command, the human system you plug into**
- [Introduction to the Incident Command System (ICS)](https://www.youtube.com/watch?v=P-dPBso2xPM)
- [ICS Explained: Military Command for Emergencies](https://www.youtube.com/watch?v=FseyOejZHcE)
- [ICS 100: Introduction to the Incident Command System](https://www.youtube.com/watch?v=DJ8l6jByOu0)
- [IS-700.b Lesson 4: The Incident Command System (Part 1)](https://www.youtube.com/watch?v=OBDcyUAaBqs) (NIMS context)
- [Incident Command System: Planning & Org Structure](https://www.youtube.com/watch?v=AcYQWr1WtxM)

**Emergency data standards**
- [Overview of EDXL Emergency Standards](https://www.youtube.com/watch?v=7eoV5XwZVO8)
- [Emergency Alerting: Introducing the Common Alerting Protocol (CAP)](https://www.youtube.com/watch?v=5IsNdN8ohyk)
- [The International Standard, CAP](https://www.youtube.com/watch?v=KlDWmW0nkMc)

**Situational awareness for responders**
- [Android Team Awareness Kit (ATAK)](https://www.youtube.com/watch?v=HdNy0YBfEvU)
- [ATAK Getting Started Tutorial](https://www.youtube.com/watch?v=EBSD3bn9sck)
- [ATAK Instructional Videos for First Responders: Settings](https://www.youtube.com/watch?v=QC4wQ0QWizI)
- [TAK Product Center channel](https://www.youtube.com/@takproductcenter4832)

**Drone fleet control (MAVLink / PX4 / MAVSDK)**
- [Controlling MAVLink drones with MAVSDK, PX4 Developer Summit 2019](https://www.youtube.com/watch?v=GXt-eXJ9vfg)
- [Advanced 12 Drone Swarm Multiple Leader/Follower using PX4 and MAVSDK, Dockerized Demo](https://www.youtube.com/watch?v=VTyb1E_ueIk)
- [Improving our PX4 Drone Show: Multiple Drone Show with Advanced MAVSDK Offboard Control](https://www.youtube.com/watch?v=Wtxsv9mLkEU)
- [PX4 MAVSDK Drone Show: GUI upgrades and swarm intelligence in v0.8](https://www.youtube.com/watch?v=E0xijEuTKtU)
- [Lab 6: Use MAVSDK for Autonomous Takeoff](https://www.youtube.com/watch?v=Vr1SwUGIC0c) (single-drone starting point)

**Robot networking (ROS 2 / Zenoh) for lossy links**
- [ROS 2 and Zenoh](https://www.youtube.com/watch?v=-WZAA35jLxc)
- [Zenoh: How to Make ROS 2 Work at any Scale and Integrate with Anything](https://www.youtube.com/watch?v=5ZR3zw_luc4)
- [No more broken comms! (ROS 2 + rmw_zenoh)](https://www.youtube.com/watch?v=fS0_rbQ6KKA)
- [Zenoh on PX4 for Multicast Distributed Networking (NXP)](https://www.youtube.com/watch?v=WJyB9JrkMvQ)
- [Mobile Gateways for ROS2 Systems with Zenoh](https://www.youtube.com/watch?v=DZKoP_sba5k)

**Multi-robot field systems in comms-denied environments**
- [DARPA Subterranean Robot Challenge Final Event (livestream)](https://www.youtube.com/watch?v=GgYCoaM72qA)
- [Team CERBERUS Wins the DARPA Subterranean Challenge](https://www.youtube.com/watch?v=fCHOU-fw2c0)
- [DARPA SubT Challenge: CERBERUS Team Concept Video](https://www.youtube.com/watch?v=1aWQPTfseIE)
- [DARPA SubT Urban Circuit: Autonomous Exploration in the Satsop Abandoned Power Plant](https://www.youtube.com/watch?v=Idmq_5hhMic)

**Operational drone programmes (Drone-as-First-Responder, closest to a real product)**
- [Drone as First Responder explained](https://www.youtube.com/watch?v=jy1_fNG6Zac)
- [Skydio DFR Command for Drone as First Responder Programs](https://www.youtube.com/watch?v=mgQvUXv4ryM)
- [From Contract to First Flight: How Skydio Launches DFR Programs](https://www.youtube.com/watch?v=sjTemr5fKN4)
- [Axon Air DFR playlist](https://www.youtube.com/playlist?list=PLjBVA4man3TTZTRNAv9zeDeY920kC6G7G)
- [Forsyth County Sheriff's Office DFR using DroneSense Remote](https://www.youtube.com/watch?v=hJVYmBGn7wA)
- (Vendor marketing, so useful for workflow and UX, not for engineering depth.)

**Airspace**
- [Why the FAA needs Remote ID: A Deep Dive into Unmanned Traffic Management](https://www.youtube.com/watch?v=FOpIgWhRlBI)

**Gaps: I found no good YouTube explainer for these**, so use the papers in 7.2:
CBBA / decentralised task allocation, ASTM F3411/F3548 internals, and LLM runtime assurance for UAS.

### 7.2 Papers and docs (read these for the actual core concepts)

| Topic | Source |
|---|---|
| Human-drone teaming in emergencies (**read first**) | Notre Dame DroneResponse: "Human-Drone Interactions with Semi-Autonomous Cohorts of Collaborating Drones" (arXiv 2010.04101); "Model-Driven Requirements for Humans-on-the-Loop Multi-UAV Missions" (arXiv 2009.10267); project page: strand.nd.edu "Agentic Drone Swarms: Open-World Decision-Making for Emergency Response" |
| Field results with firefighters | "Firefighters' Perceptions on Collaboration and Interaction with Autonomous Drones: Results of a Field Trial" (arXiv 2405.10153) |
| Multi-robot system in real comms-denied conditions | Team CERBERUS technical overview (arXiv 2207.04914); CoSTAR NeBula (arXiv 2103.11470); "Flexible Supervised Autonomy for Exploration in Subterranean Environments" (arXiv 2301.00771) |
| Task allocation | "Decentralized Multi-Robot Task Allocation Under Degraded Communication: A Benchmark" (arXiv 2609.13711); "Event Driven CBBA with Reduced Communication" (arXiv 2509.06481); original CBBA: Choi, Brunet, How (2009) |
| Safe LLM in UAS | "Cognition Envelopes for Bounded Decision Making in Autonomous UAS Operations" (arXiv 2510.26905); "Mission-Level Runtime Assurance for LLM-Assisted ISR Swarms" (arXiv 2607.23532) |
| Swarm SAR approaches | Decentralised null-space behavioural control (UCL); LLM-selected architecture central/hierarchical/holonic (Jyväskylä / Honda RI); PDSR multi-tier swarm (arXiv 2410.22982) |
| Fleet software example (open source) | `alireza787b/mavsdk_drone_show` on GitHub: PX4 + MAVSDK + FastAPI backend + React dashboard |
| Protocols | MAVLink survey (arXiv 1906.10641); OASIS CAP v1.2 spec (docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2.html); OASIS EDXL; ASTM F3411 (Remote ID), F3548 (UTM) |
| Middleware | "Performance Comparison of ROS2 Middlewares for Multi-robot Mesh Networks" (arXiv 2407.03091) |
| Courses | FEMA IS-100, IS-200, IS-700 (free) |

**Caveat:** several arXiv IDs came straight from search results and I have not opened them. Confirm each one resolves to the title shown before citing it.

### 7.3 Suggested viewing order (about 6–8 hours)
1. ICS intro (30 min). Learn who your users are and what a commander expects.
2. DroneResponse paper abstracts + one DFR video (1 h). Learn the human-drone teaming model.
3. MAVSDK Developer Summit talk + 12-drone Docker demo (1 h). See a real multi-vehicle control path.
4. Zenoh + "No more broken comms" (1 h). Understand why default networking fails in the field.
5. SubT Final livestream highlights + CERBERUS concept video (1 h). See autonomy under no-comms.
6. CAP + EDXL overview (45 min). Learn the integration vocabulary.
7. CBBA / degraded-comms allocation papers (1–2 h).

---

## 8. Proposed roadmap (do not start until you pick the options in §9)

| Phase | Goal | Exit criterion |
|---|---|---|
| 0. Decide | Answer §9; pick target users and scale | Written scope |
| 1. Contracts | Define message schemas (telemetry, proposal, command, ack, event), versioned. Event log format | Schemas + test vectors, no code on drones yet |
| 2. Backend skeleton | Orchestrator + event store + WebSocket gateway, driven by the **existing** sim as a fake fleet | Operator console shows live fleet and AI proposals, with approve/veto |
| 3. Decision hardening | Randomised scenarios, uncertain sensing, richer Jev questions, no-AI fallback, evaluation harness | Scored comparison: scripted vs rule vs Jev across 100s of seeds |
| 4. Real flight stack in sim | PX4 SITL (multi-vehicle) + MAVSDK onboard agent, Gazebo world. PyBullet becomes scenario/visual only | Same mission flown on PX4 SITL |
| 5. Failure injection | Link loss, partitions, GPS loss, battery fault, dead drone, duplicate/late commands | All failure modes handled with a recorded outcome |
| 6. Standards + integration | CAP in, EDXL/CoT out, ICS role-based access | A mock CAD or TAK client consumes the output |
| 7. Hardware-in-the-loop, then field | Bench test, tethered, then controlled outdoor trials with the correct authorisation | Safety case + regulator sign-off |

---

## 9. Decisions I need from you

1. **Scale and use case.** Dozens of drones searching a large disaster, or a few drones docked in a city (DFR style)? These lead to very different architectures.
2. **Real hardware ever?** Simulation and demo only, or a path to real aircraft (PX4/ArduPilot)? This decides whether L0/L1 matter now.
3. **Who is the user?** Incident commander, drone pilot, or a dispatcher? This decides the console design and the integrations.
4. **Where does it run?** Cloud, an on-site edge box (my suggestion for disaster zones), or both.
5. **Role of Jev.** Keep Jev as the advisory planner (needs a no-AI fallback and an internet dependency), or run a local model for offline operation?
6. **Jurisdiction.** Regulatory path depends on the country.

## 10. What I'd do next, once you answer §9
Phase 1 and 2 only: write the message contracts and a WebSocket orchestrator that drives the *existing* simulation as a fake fleet, so you can see an operator console with AI proposals and approvals. Nothing flight-critical is touched, and it gives you a visible architecture to react to.






https://www.youtube.com/watch?v=CZaAPu6VLlQ
https://youtu.be/i_EPlJE3CvE?si=4AA76ba97UKQZQ6P