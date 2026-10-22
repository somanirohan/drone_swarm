# Understanding a 100+ drone disaster-response system

**10 October 2026 · architecture proposal · humanitarian use**

## 1. The main idea

DisruptionOps should tell the fleet **what outcome responders need**: search a village, inspect a bridge, revisit a suspected stranded-person location, or deliver a small approved package. A field coordinator decides **which aircraft can do it and when**. Each aircraft decides **how to fly its approved mission safely**, using its own sensors and flight controller.

For example, “search polygon A for people and return observations within ten minutes” is a fleet-level mission. “Apply this motor thrust now” belongs entirely inside the aircraft. An internet outage must not interrupt stabilization, local avoidance, or the aircraft's configured contingency behavior.

**Recommended structure: central incident coordination + field coordination + onboard autonomy.** This is an engineering recommendation, not evidence that an existing humanitarian organization operates exactly this arrangement.

![Architecture](01-architecture.png)

## 2. What real disaster operations show

There is good evidence for drones supporting emergency mapping, damage assessment, and responder decisions. WFP describes collecting and stitching aerial imagery after cyclones Idai and Kenneth in Mozambique. UNICEF describes drone maps used by Malawi's Emergency Operations Centre after Cyclone Freddy. These are operational examples of drones improving information; neither source establishes a fully autonomous 100-drone humanitarian swarm. [WFP case study](https://www.wfp.org/stories/joining-dots-how-ai-and-drones-are-transforming-emergencies), [UNICEF case study](https://www.unicef.org/malawi/stories/adda-alumni-aid-cyclone-response-swift-tech-solutions).

There is also research on much larger coordinated robot systems. DARPA's OFFSET programme investigated air and ground swarms at scales up to 250 systems; its final field experiment reports real robot trials. That military research is evidence about coordination experiments, not proof of civilian disaster readiness, safe one-person supervision, or regulatory approval. [Programme](https://www.darpa.mil/research/programs/offensive-swarm-enabled-tactics), [final field experiment](https://www.darpa.mil/news/2021/offset-swarms-take-flight).

A field-robotics paper on OFFSET launch congestion shows why getting many vehicles out of a constrained base matters. This informs the proposed pad scheduler and dispersed bases; it does not certify their capacity. [Congestion study](https://arxiv.org/abs/2307.16788).

WFP's drone programme also explicitly includes coordination, equipment readiness, regulations, imagery, connectivity, and public trust. The lesson is that an emergency drone system includes people and logistics as well as aircraft software. [WFP Drones](https://www.wfp.org/wfp-drones).

## 3. What “swarm” means here

A fleet is a collection of aircraft. A useful response swarm additionally shares observations, divides work, avoids conflicting assignments, and adapts when vehicles or links become unavailable. It does not need to fly in a tight formation. Search drones should usually spread over different sectors.

The recurring loop is:

1. **Observe:** gather images, aircraft health, wind estimates, and position quality.
2. **Estimate:** create georeferenced observations with uncertainty, not declarations of truth.
3. **Share:** publish useful summaries to the field coordinator and relevant neighbors.
4. **Allocate:** match approved tasks to aircraft with the necessary payload and endurance.
5. **Execute:** fly locally, track progress, and handle immediate hazards onboard.
6. **Reconcile:** merge delayed observations, update incident state, and re-plan where useful.

An aircraft might detect a possible person, another aircraft inspect from a better angle, and a boat crew confirm and extract them. The camera result, corroboration, supply delivery, and extraction are four different events.

## 4. What your current repositories really provide

These findings come from source inspection, not claims in a production marketing page. Full file references and inspected commits are in [repository-integration.md](repository-integration.md).

| Area | Current behavior | Meaning for the proposal |
|---|---|---|
| Swarm runtime | Twelve Crazyflie CF2X drones in PyBullet, all agents in one Python process | Useful simulation fixture; no physical fleet demonstrated |
| Decision making | Independent per-agent exploration, bids, claims, peer heartbeats | Preserve the concepts behind transport and runtime interfaces |
| Scene | Fixed three survivor locations and a seeded scene | Current results are not generalization evidence |
| Sensing | Range and geometric line-of-sight check, then exact survivor position | Replace with perception, localization error, misses, and false detections |
| Communications | An in-memory broadcast heap with fixed 0.15-second delay | No realistic packet loss, partitions, or competing authorities |
| Battery | Illustrative scalar depletion and fixed thresholds | Does not establish payload-dependent range or safe return feasibility |
| Flight safety | Simulation runtime sees every position and applies pairwise separation | Needs local sensing, onboard monitors, airspace planning, and validated failsafes |
| AI | Validated mission proposals; Jev serializes requests with a shared lock | Keep AI advisory; avoid one remote model call per aircraft decision |
| Browser feed | `web_stream.py` publishes init/frame/log messages; received messages are ignored | Telemetry visualization, not a command protocol |
| DisruptionOps bridge | Existing read-only swarm listener files discoveries through incident intake | Integration is already present in demo form |
| Ground response | Existing incident trust/dedup, policy gate, event log, CP-SAT allocation | Reuse these incident coordination components |

“Rigged” needs precision: there is real agent logic, but the environment supplies convenient, deterministic information and simplified failures. The claim to challenge is “this proves field autonomy,” not “there are no decisions.”

The backend receives the simulator's full survivor array in its init message, including sites not yet discovered. The agents themselves are not given that array. Keep simulator ground truth in evaluation tooling and keep the operational console limited to actual observations.

## 5. Three architectural choices

| Approach | Strength | Main weakness | Fit |
|---|---|---|---|
| Central scheduler assigns every task through cloud | Easy global planning and demonstration | Depends on wide-area connectivity; cloud cannot safely provide continuous control | Early simulation experiments |
| Fully peer-to-peer allocation | Can coordinate without cloud when peers remain connected | Partitions create conflicting beliefs; difficult authorization, audit, and multi-team handoff | Specialized research or bounded local behaviors |
| **Hierarchical hybrid** | Global incident priorities, local continuity, onboard safety | More explicit boundaries and reconciliation work | **Recommended target** |

Use central planning for incident priority and broad sector ownership, field planning for aircraft assignments and pads, and onboard logic for immediate flight decisions. Do not make the cloud and edge run competing authoritative allocators for the same task.

## 6. Hypothetical Assam flood: 100 aircraft, five field cells

Imagine flooding near Morigaon on the Brahmaputra floodplain: some villages are isolated, mobile service is intermittent, roads have uncertain status, and helicopters and boats are responding. The district operations team has 100 aircraft available across five dry staging locations. The study area is **100 km²**, split initially into five **20 km² responsibility polygons**. These are planning assumptions; base access, river conditions, airspace, terrain, and measured aircraft range would change the partition.

The Assam scenario is motivated by real government flood planning and mapping resources. ASDMA publishes disaster information and NRSC provides flood mapping support. Historical flood hazard maps help set search priorities but do not establish today's navigable roads or people locations. [ASDMA](https://asdma.assam.gov.in/), [ASDMA/NRSC inundation mapping](https://asdma.assam.gov.in/resource/inundation-mapping-nrsc), [NRSC disaster support](https://www.nrsc.gov.in/nrscnew/Apps_DMS.php?lang_code=en).

Each illustrative twenty-aircraft cell has:

| Role | Per cell | Fleet total | Purpose |
|---|---:|---:|---|
| Survey/search | 10 | 50 | Cover search strips; inspect roofs, islands, roads and bridges |
| Verification/inspection | 4 | 20 | Revisit uncertain detections with appropriate RGB/thermal payloads |
| Small aid delivery | 2 | 10 | Carry payloads that fit the selected aircraft's tested envelope |
| Communications relay | 1 | 5 | Improve a measured local link where useful |
| Ready reserve | 3 | 15 | Replace failures and support urgent jobs or maintenance rotation |
| **Total** | **20** | **100** | |

Roles describe a possible mix of capabilities, not software magically converting any aircraft into a cargo or relay drone. An airborne relay extends only the radios and networks it actually supports; it does not automatically restore mobile service.

**100 deployed aircraft does not mean 100 airborne continuously.** Each cell rotates sorties, batteries, operators, and payload preparation. “100 concurrently airborne” is a separate requirement demanding substantially different ground logistics and demonstrated supervisory capacity. No one-operator-per-20-aircraft assumption is made.

![Deployment](02-assam-deployment.png)

### A first-four-hours story

- **0–20 minutes, illustrative:** command defines priorities and flight restrictions; field teams verify weather, pads, aircraft health, and available authorizations. Previously mapped flood extent and local reports seed tasks. Launch waits until these checks pass.
- **20–60 minutes:** available search aircraft survey high-priority populated sectors; detections create candidate observations with coordinates, uncertainty, time, and evidence. Human triage and corroboration update incidents. Ground responders receive tasks through DisruptionOps.
- **60–120 minutes:** verification aircraft revisit uncertain or urgent sites; approved small deliveries are scheduled; boats continue extraction. An aircraft detour becomes an aerial hazard observation rather than automatically closing a road.
- **120–240 minutes:** sorties rotate, sectors are revisited according to flood change and risk, interrupted cells synchronize when links recover, and field crews record outcomes. The mission stays open until the relevant outcome is confirmed.

These times describe a workflow, not promised throughput. A thunderstorm may stop all flying. The system must still support ground coordination.

## 7. The components and their responsibilities

### District command: DisruptionOps

Retain the existing intake, incident clustering, policy/delegation decisions, event history, officer interface, and ground-resource allocator. Add a mission service that turns approved incident needs into capability-based drone tasks with explicit scope and expiry.

The district view shows sector coverage age, unverified observations, available capabilities, mission progress, telemetry freshness, and failures. It should surface situations needing attention instead of requiring the commander to watch 100 videos. Crew staffing and permissible supervision must be established independently.

The Indian Incident Response System provides relevant command, operations, planning, and logistics roles. Map actual district responsibilities onto software permissions; do not substitute a US emergency-command model or assume NDRF is always the incident commander. [NDMA IRS guidelines, hosted by NIDM](https://nidm.gov.in/PDF/guidelines/Incident_Response_System.pdf).

### Field cell: local edge computer and operator station

Each base has a local mission coordinator, authenticated aircraft gateway, cached maps and restrictions, a durable event journal, media storage, task allocator, and pad/battery scheduler. A UPS and locally reachable operator UI are part of the design.

The coordinator receives delegated tasks for its polygon, checks feasibility, assigns eligible aircraft, and records acknowledgments. It coordinates launch/landing slots and sends delayed observations upstream. During a cloud outage it can continue within the cached delegation and valid operational limits. It cannot authorize itself to expand the mission into new airspace.

Logical cells need not always map one-to-one to a physical server. Start with one service per base, measure demand, then add redundancy with a single active issuing authority. A standby that cannot prove authority remains read-only.

### Aircraft: companion computer

Runs payload processing, georeferencing, mission execution, local obstacle/neighbor awareness, and the link manager. It accepts only authenticated, current, feasible tasks. It stores observations when disconnected. It does not need an LLM to follow its already approved mission.

Localization combines the actual platform's GNSS, inertial estimates and any validated auxiliary sensing. Visual localization may help, but your map-patch-finder's accepted tile match is not an established navigation solution. Floodwater and damaged buildings can invalidate reference imagery.

### Aircraft: flight controller

PX4 or ArduPilot manages stabilization and configured flight failsafes. Validate the chosen firmware, hardware, configuration, and companion interface together. PX4 documents battery, position, geofence and link-loss behaviors; MAVSDK provides interfaces for vehicle telemetry and missions. This supports the component boundary, not a promise of collision avoidance out of the box. [PX4 safety](https://docs.px4.io/main/en/config/safety), [MAVSDK](https://mavsdk.mavlink.io/v3/en/).

Collision avoidance and hazard sensing are additional systems. A geofence alone cannot detect a rescue helicopter, cable, bird, or nearby aircraft.

## 8. Task allocation and airspace coordination

First filter aircraft by capability, payload availability, navigation health, weather envelope, energy budget, and authorized operating volume. Then score eligible assignments using priority, expected time to useful observation or aid, uncertainty reduction, energy cost, and disruption to an already committed task.

Use a deterministic assignment solver for the first field implementation. CP-SAT or a simpler bounded assignment solver is reasonable; keep a visible fallback when the solve times out. DisruptionOps' current road ETA matrix is for ground travel. Aircraft ETA needs an air-route model including approved paths, climb/descent, wind, weather, and energy.

Distributed auctions such as CBBA are relevant research: the original paper presents decentralized auction and bundle allocation. Their convergence properties depend on algorithm assumptions and connectivity; they do not solve arbitrary disconnection, evidence quality, aviation approval, or collision avoidance. Consider them later for local multi-task coordination after fault testing. [MIT CBBA paper](https://dspace.mit.edu/entities/publication/b0bf0a05-be3b-433b-9f4b-ce314ed5178b).

Plan search strips within approved polygons, reserve launch and recovery windows, and use strategic path/time separation plus onboard tactical avoidance. Boundary handoffs transfer both task ownership and a valid flight-volume reservation. Search allocations across adjacent cells must respect shared boundaries.

Altitude bands can reduce conflicts but cannot guarantee safety. Terrain clearance, measurement errors, climb/descent crossings, helicopter activity, and simultaneous returns remain hazards. A fleet-wide recall requires recovery scheduling and contingencies, not 100 aircraft converging on one point.

## 9. Reliable commands and partitions

**Incident facts can merge eventually; flight/task authority cannot be resolved by “latest timestamp wins.”** Observations can be appended and reconciled after an outage. Active mission ownership uses one designated authority, monotonic generations, durable assignment records and fencing against an old issuer.

Use the command sequence:

`proposed → authorized → queued → received → accepted/rejected → executing → completed/failed/aborted`

HTTP 202, broker delivery, or flight-controller ACK is not evidence that a rescue happened. Persist acknowledgments separately from outcomes. Expired or superseded commands are rejected; retries carry the same command ID. Payload release additionally uses durable execution deduplication and physical confirmation, because network retries must not cause another release.

During a partition:

- Cloud losing contact does **not** prove an aircraft is free for reassignment.
- An offline cell works only inside its current delegation, task scope, flight volumes, and validity limits.
- The aircraft's mission contains a safe expiry/abort behavior; the onboard runtime uses bounded clock uncertainty and monotonic timing for elapsed deadlines.
- A coordinator must not reassign an exclusive operation solely because a lease timer elapsed. It needs positive release/quiescence confirmation or a prevalidated expiry behavior and conservative isolation that ensure the old executor cannot continue conflicting work. Otherwise mark ownership **unknown** and hold the conflicting task.
- Cross-cell tasks and new shared-airspace entries pause when authority cannot be established.
- On reconnection, replay missing events, reconcile observations, and reject stale mission generations. Do not replay old commands blindly.

There is no unrestricted availability-plus-consistency guarantee during arbitrary partitions. The proposal chooses bounded continuity and conservative authority for safety-relevant actions.

## 10. Networking: separate the traffic

| Path | Candidate technology | Design requirement |
|---|---|---|
| Companion ↔ flight controller | MAVLink via MAVSDK/MAVROS or native integration | Remains local; validated firmware-specific behavior |
| Aircraft ↔ field gateway | Secured IP radio/LTE; MQTT 5 or routed Zenoh | Commands and critical events before routine telemetry/media |
| Nearby aircraft | Bounded local neighbor/intent exchange | No fleet-wide raw-video broadcast |
| Base ↔ district | Available wired/cellular/satellite backhaul | Durable store-and-forward and resumable event synchronization |
| Backend ingestion | Existing Redis Streams and Postgres initially | Verify durability settings and crash recovery; memory queue is insufficient for critical accepted work |
| Console | REST commands plus WebSocket/SSE updates | Authenticated authorization path; freshness and sequence-resume support |
| Media | Local recording, resumable object upload; selected WebRTC previews | Bounded subscriptions and no competition with critical traffic |

Radio mesh is a possible physical network, not a guarantee of range, throughput, or survivability. Measure line-of-sight, channel interference, node density, antenna placement, and each hop's airtime. LoRa/BLE may carry short delayed alerts within applicable limits; they are not proposed as fleet video or primary real-time safety links.

If ROS 2 is adopted, evaluate explicitly routed Zenoh for the field network, with bounded discovery and deliberate topic exposure. The upstream `rmw_zenoh` design documents routing/discovery and configuration; this report does not claim a vendor benchmark proves superiority on your radio. Application deadlines and stale-data rejection remain necessary. [Upstream design](https://github.com/ros2/rmw_zenoh/blob/rolling/docs/design.md).

Do not add Kafka, Kubernetes, Redis, NATS and Zenoh all at once simply because there are 100 aircraft. Start from the existing backend and measure what its ingest, storage and event-processing paths can sustain. Use one chosen field transport and an adapter at the backend boundary.

## 11. Sizing calculations: assumptions, not measurements

### Metadata and video

Assume 100 aircraft each sends a **500-byte application telemetry record at 2 Hz**:

`100 × 500 × 2 = 100,000 bytes/s = 0.8 Mbit/s` before overhead.

That is **360 MB/hour**, or **1.44 GB over four hours** before replication, indexes and protocol overhead. Per twenty-aircraft cell it is 0.16 Mbit/s of application telemetry. Allow a planning margin such as 3× for this telemetry class, but verify the actual packet format, radio retransmissions, security overhead, and added events. This margin excludes video.

Assume each video stream averages **4 Mbit/s**: 100 streams would be **400 Mbit/s**, around **180 GB/hour** if all recorded at that bitrate. Watching only four selected streams still takes approximately **16 Mbit/s** upstream. These figures explain edge inference, local recording and selective previews; they are not camera specifications.

All-to-all exchange among 100 aircraft has **4,950 undirected pairs**. Five disjoint twenty-aircraft groups have **950 within-group pairs**, plus gateway and boundary exchanges. These are potential logical relationships, not radio throughput measurements. The current `MessageBus` expands every message into recipients in Python, so merely increasing `n` amplifies processing. Local spatial indexing can reduce neighbor work, but does not solve perception or radio contention.

### Coarse search throughput

Assume a search aircraft moves at **8 m/s**, has an **effective 40 m search swath** after overlap and sensing requirements, and spends **15 minutes** on the search portion of a sortie:

`8 × 40 × 900 = 288,000 m² = 0.288 km² per sortie`.

Fifty search aircraft each doing one such sortie give **14.4 km² of ideal coverage**. A hypothetical 50% utilization factor gives **7.2 km² of useful coverage**. Therefore 100 km² would require about **14 equivalent fleet search cycles** at that utilization. This is not a total-duration estimate: rotation, unavailable aircraft, travel, charging, verification and weather may dominate.

The swath must be validated for the task. Terrain mapping, finding people on rooftops, seeing under canopy, and thermal inspection have different resolution requirements. Coverage does not mean every person was detected.

### Battery, pads and power

For an illustrative aircraft that is airborne **20 minutes** and takes **10 minutes** to recover, inspect, swap and relaunch, the duty fraction is 20/30. A 100-aircraft inventory could sustain approximately **67 airborne aircraft** under ideal assumptions. Sustaining 100 airborne would need at least **150 aircraft** at that duty cycle before reserve and maintenance margins.

Illustrative battery demand: with a **20-minute flight** and **60-minute charge/cool cycle**, a pack has an 80-minute loop. A continuously flying slot consumes **three packs/hour** and needs **at least four packs in circulation** with no margin. Charge rate, battery cooling, ageing, field power and actual endurance must be measured. A fixed “return at 20%” policy is insufficient; use predicted energy for the validated recovery route plus reserve.

Illustrative pad calculation: twenty recovering aircraft, **four independent pads**, **90 seconds occupancy per landing/swap operation** gives a lower-bound **7.5 minutes** to process twenty operations. Physical separation, approach clearance, crew capacity, payload preparation and launch operations may make the real time longer. Reserve airborne energy for recovery queues and provide tested alternate pads.

## 12. Safety, failures, and supervision

| Failure | Required behavior in the proposal |
|---|---|
| Internet/cloud outage | Aircraft continue valid local missions; edge operator remains available; events spool locally |
| Aircraft radio loss | Aircraft follows its approved mission-specific contingency; operator sees unknown/stale state |
| Companion crash | Flight controller detects the relevant loss and executes its tested configured behavior |
| Low remaining energy | Divert/recover using a conservative route and reserve; no arbitrary universal battery threshold |
| Navigation integrity loss | Enter the tested platform-specific contingency; GNSS-dependent return is not assumed safe |
| Base becomes inaccessible | Use validated alternate recovery or safe landing; cease launches |
| Partition between cells | Avoid new cross-boundary/exclusive tasks until authority is clear |
| Perception uncertainty | Preserve candidate status, evidence and uncertainty; seek corroboration |
| Weather outside aircraft envelope | Stop launching and apply the approved recovery plan |
| Helicopter enters operational area | Yield according to the coordinated incident air-operations procedure |
| Replay/compromised credential | Reject stale or unauthorized missions; revoke identities and record the incident |

PX4's offboard mode requires local proof-of-life and has configured offboard-loss behavior. That is another reason remote cloud timing must not supply aircraft setpoints directly. Pin and test the actual firmware version rather than copying parameter names from changing documentation. [PX4 offboard documentation](https://docs.px4.io/main/en/flight_modes_fw/offboard).

An application “pause” stops new autonomous assignments. An aircraft hold, return, land, or flight termination has different physical consequences. Show the action, affected aircraft, authority, dispatch acknowledgments and resulting state. Do not make a web emergency-stop button indiscriminately disarm airborne vehicles. A remote recall can fail when disconnected; onboard contingencies remain essential.

Separate command credentials per device/base, use encrypted authenticated transport, enforce replay protection and scoped roles, and retain software/configuration identity in the audit trail. MAVLink 2 signing authenticates messages but does not encrypt them; protection depends on the complete link and key configuration. [MAVLink signing](https://mavlink.io/en/guide/message_signing.html), [MAVLink FAQ](https://mavlink.io/en/about/faq.html).

## 13. India: build authorization into the mission

This is an architecture study, not a determination that the example operation is permitted. The Ministry publishes the Drone Rules, 2021 and amendments; airspace permissions depend on the applicable rules and actual operation. The published green-zone definition includes the ordinary 120 m limit and airport-related differences. A green map location alone does not establish permission for the proposed 100-aircraft/BVLOS operation. [Ministry rules page](https://www.civilaviation.gov.in/ministry-documents/rules/drones-rules-2021-dated-25-august-2021), [government airspace-map explanation](https://www.pib.gov.in/newsite/PrintRelease.aspx?lang=2&reg=48&relid=225084).

Store the approved operating polygon, altitude reference/limits, dates, responsible operator, aircraft eligibility, weather conditions, communications contingencies and any relevant permission references with the mission. Coordinate with district air operations and the relevant aviation authorities for the actual aircraft, payload and supervision model. An emergency objective is not assumed to waive these requirements.

Research found a **draft Civil Drone Bill, 2025**. It is a draft and must not be treated as enacted law. I did not establish a current blanket BVLOS or one-person-100-aircraft authorization from accessible primary sources. Recheck operative notifications, platform processes and the live airspace map when planning flights. [Draft bill](https://www.civilaviation.gov.in/sites/default/files/2025-09/Draft%20Civil%20Drone%20%28Promotion%20and%20Regulation%29%20Bill%202025.pdf), [DigitalSky](https://digitalsky.aai.aero/).

Government weather and water sources belong in the situational picture. IMD publishes district nowcasts; CWC provides flood forecasting/situation information. These support planning but do not replace local measurements or aircraft-specific weather limits. Public websites are not a promise of a supported integration API. [IMD district nowcast](https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php), [CWC bulletin](https://cwc.gov.in/sites/default/files/dfsitrepca08062024.pdf).

## 14. Conflict and alleged war-crime documentation

For humanitarian conflict settings, retain civilian search, damage documentation and responder support, while using a separately governed evidence workflow. Commercial-looking drones can be confused with military systems, creating risk for humanitarian access and civilians. Local coordination and a decision that a flight is appropriate are necessary; an aircraft is not automatically perceived as humanitarian. [ICRC discussion](https://www.icrc.org/en/article/faq-international-humanitarian-law-drones-armed-conflict).

Suggested evidence design: preserve original imagery and metadata; record capture time with uncertainty, aircraft/sensor identity and position confidence; hash originals; keep an access-controlled original store and redacted sharing copies; log each transfer and analytical transformation. An image hash proves consistency with the hashed bytes, not truth, authorship or capture time. AI can flag material for review; it cannot establish that a war crime occurred.

The Berkeley Protocol supplies professional principles for collecting, verifying, preserving and analyzing digital open-source material. Applying related preservation principles to private drone captures is a design inference, not a claim that those captures fall within the protocol's entire scope or become legally admissible automatically. Qualified investigators decide relevance and handling. [Berkeley Protocol](https://humanrights.berkeley.edu/publications/berkeley-protocol-on-digital-open-source-investigations/).

Keep sensitive survivor locations and originals out of public maps. Sharing and retention should follow an explicit humanitarian purpose and access policy. Routine disaster imagery and evidentiary originals can require different retention handling.

## 15. How to make the demo a useful proving ground

1. **Contracts and repeatability:** label simulator messages, separate ground truth, give missions/events stable identities, and route both simulator and future hardware through the same proposed interfaces.
2. **Randomized task-level simulation:** vary incident locations, wind/energy, sensor false positives and misses, network loss/delay/duplication, partitions, pad capacity and edge outages. Evaluate 100–200 logical aircraft without requiring high-fidelity physics for every run.
3. **Selected high-fidelity/SITL validation:** use PyBullet for chosen physical interactions and PX4/ArduPilot SITL for real autopilot semantics. Benchmark actual simulation performance; current twelve-drone success is not a 100-drone benchmark.
4. **Hardware-in-the-loop and controlled small flights:** validate identities, commands, perception/geolocation and every relevant failsafe on the selected platform. Expand by evidence: one aircraft, a small team, multiple cells, then authorized larger trials.
5. **Operational exercises:** include responders, battery crews, a failed base, lost communications and helicopter deconfliction. Verify incident outcomes and operator workload, not just robot trajectories.

Compare independent baselines under the same held-out scenario distributions: simple sector sweep, deterministic allocation, auctions/CBBA variants, and advisory AI. Use enough seeds to report variability; keep planner-visible observations separate from simulator truth. Do not choose a favorable seed as the performance claim.

Measure time to first credible observation, detection precision/recall, coverage age, time to aid/extraction confirmation, wasted duplicate work, unsafe separation events, stale-command rejection, partition recovery, operator interventions and battery/pad utilization. Proposed acceptance criteria require task, aircraft and operating-context inputs; none are represented as validated thresholds here.

## 16. What must be specified next

The remaining design inputs are: 100 owned versus 100 simultaneously airborne; aircraft and payload classes; intended geographic footprint; measured endurance; available radio/backhaul; required offline duration; pilot and crew organization; actual flight permissions; detection quality requirements; ground-response workflows; retention and evidence obligations; budget; and whether the next objective is simulation, an academic demonstrator, or field trials.

Until these are known, **five twenty-aircraft cells is a useful architecture example, not a final fleet procurement or operating plan**. The strongest first integration step is an explicit mission/event adapter around the simulator and DisruptionOps' existing incident pipeline.
