# Primary-source research register

Reviewed 10 October 2026. The report separates observed repository behavior, source-backed findings and proposed engineering choices. Search metadata dates are not used as publication dates. No source demonstrates the exact proposed civilian 50/100-aircraft architecture. No commercial swarm vendor's marketing claims are treated as performance evidence.

| Source | Supports | Limit |
|---|---|---|
| [WFP: AI and drones after Idai/Kenneth](https://www.wfp.org/stories/joining-dots-how-ai-and-drones-are-transforming-emergencies) | Operational aerial mapping and damage analysis | Not a 100-drone autonomous swarm deployment |
| [UNICEF: Cyclone Freddy drone response](https://www.unicef.org/malawi/stories/adda-alumni-aid-cyclone-response-swift-tech-solutions) | Detailed drone maps informing emergency decisions | No general swarm safety/performance proof |
| [WFP Drones](https://www.wfp.org/wfp-drones) | Humanitarian coordination, imagery, connectivity, ethics and regulations | Programme scope, not fleet benchmark |
| [DARPA OFFSET programme](https://www.darpa.mil/research/programs/offensive-swarm-enabled-tactics) | Large heterogeneous swarm research goals | Military programme; goal scale is not humanitarian readiness |
| [DARPA final field experiment](https://www.darpa.mil/news/2021/offset-swarms-take-flight) | Real coordinated robot field experimentation | Does not authorize civilian use or establish staffing ratios |
| [Congestion Analysis for OFFSET CCAST](https://arxiv.org/abs/2307.16788) | Launch/recovery congestion as a scaling issue | Study conditions differ from flood response |
| [MIT: Consensus-Based Decentralized Auctions](https://dspace.mit.edu/entities/publication/b0bf0a05-be3b-433b-9f4b-ce314ed5178b) | CBAA/CBBA distributed task-allocation methods | Guarantees depend on assumptions; no arbitrary-partition safety proof |
| [PX4 safety documentation](https://docs.px4.io/main/en/config/safety) | Configurable onboard failsafes | Pin firmware; correct configuration and validation remain necessary |
| [PX4 offboard mode](https://docs.px4.io/main/en/flight_modes_fw/offboard) | Proof-of-life and offboard-loss behavior | Does not supply fleet collision avoidance |
| [MAVSDK v3](https://mavsdk.mavlink.io/v3/en/) | Programmatic vehicle interfaces | API capability is not multi-aircraft operational approval |
| [MAVLink signing](https://mavlink.io/en/guide/message_signing.html) | Message authentication | Must configure keys and unsigned-message policy |
| [MAVLink FAQ](https://mavlink.io/en/about/faq.html) | Signing versus encryption distinction | Link security requires other mechanisms |
| [ROS 2 rmw_zenoh design](https://github.com/ros2/rmw_zenoh/blob/rolling/docs/design.md) | Routed discovery and middleware boundaries | No measured result on this project's radios |
| [NDMA IRS guidelines via NIDM](https://nidm.gov.in/PDF/guidelines/Incident_Response_System.pdf) | Indian command/operations/planning/logistics structure | Actual district assignments must be established locally |
| [ASDMA](https://asdma.assam.gov.in/) | Assam disaster-information context | Hypothetical Morigaon scenario is our assumption |
| [ASDMA/NRSC inundation maps](https://asdma.assam.gov.in/resource/inundation-mapping-nrsc) | Flood mapping resource | Maps have timestamps, scope and limitations |
| [NRSC disaster support](https://www.nrsc.gov.in/nrscnew/Apps_DMS.php?lang_code=en) | Remote-sensing disaster support | Not real-time ground traversability |
| [IMD district nowcast](https://mausam.imd.gov.in/responsive/districtWiseNowcastGIS.php) | Weather warning input | Website availability is not an API contract |
| [CWC flood bulletin](https://cwc.gov.in/sites/default/files/dfsitrepca08062024.pdf) | Flood forecasting/situation-report context | Historical bulletin, not current event evidence |
| [MoCA Drone Rules source page](https://www.civilaviation.gov.in/ministry-documents/rules/drones-rules-2021-dated-25-august-2021) | Official published rule reference | Gazette link failed in web tool; operation-specific current legal status not fully determined |
| [MoCA current rules listing](https://www.civilaviation.gov.in/index.php/ministry-documents/rules) | Current official listing and amendment references | Listing alone is not a consolidated legal review |
| [PIB airspace-map explanation](https://www.pib.gov.in/newsite/PrintRelease.aspx?lang=2&reg=48&relid=225084) | Published zoning framework | Recheck actual live map and conditions before flight |
| [MoCA 2023 amendment](https://www.civilaviation.gov.in/sites/default/files/2024-04/Drone%20%28Amendment%29%20Rules%2C%202023.pdf) | Existence of subsequent amendment | Not a blanket BVLOS/supervision authorization |
| [Draft Civil Drone Bill 2025](https://www.civilaviation.gov.in/sites/default/files/2025-09/Draft%20Civil%20Drone%20%28Promotion%20and%20Regulation%29%20Bill%202025.pdf) | Legislative proposal exists | Explicitly a draft; not treated as enacted law |
| [DigitalSky](https://digitalsky.aai.aero/) | Official airspace-planning entry point | Live map not validated for the hypothetical flight polygons |
| [ICRC: drones in armed conflict](https://www.icrc.org/en/article/faq-international-humanitarian-law-drones-armed-conflict) | Humanitarian confusion/access risks | No permission determination for a specific conflict flight |
| [Berkeley Protocol](https://humanrights.berkeley.edu/publications/berkeley-protocol-on-digital-open-source-investigations/) | Digital investigation and preservation principles | Open-source scope; private-drone application is an explicit inference |
| [Graphviz downloads](https://graphviz.org/download/) | Official diagram rendering distribution | Graphviz 12.2.1 archive checksum verified locally |

Additional sources located: [WFP ADAM](https://docs.adam.geospatial.wfp.org/) for complementary disaster situational information; [NRSC Assam hazard atlas](https://www.nrsc.gov.in/nrscnew/assets/pdf/Flood_Hazard_Zonation_Atlas_of_Assam_using_multi_sensor_satellite_data1998_2023.pdf) for historical risk planning; [ArduPilot failsafes](https://ardupilot.org/copter/docs/failsafe-landing-page.html) for the alternative autopilot boundary. These were not used to infer fleet-scale measured performance.

## Real-time mechanisms: additional primary research

- [Crazyswarm author paper](https://whoenig.github.io/publications/2016_IROS-LateBreaking_Preiss.pdf): 49 indoor nano-quadcopter architecture, onboard 500 Hz loop and external tracking/radio architecture. These rates and controlled indoor localization do not automatically transfer to outdoor disaster aircraft.
- [Crazyswarm documentation](https://crazyswarm.readthedocs.io/en/latest/): experimental platform and operating assumptions.
- [EGO-Swarm paper](https://arxiv.org/abs/2011.04183): decentralized asynchronous onboard trajectory optimization and trajectory sharing; reported millisecond planning under its experimental conditions. Not a certified universal collision-avoidance guarantee or a demonstrated 50-drone flood deployment.
- [Swarm-LIO2 paper](https://arxiv.org/abs/2409.17798): decentralized LiDAR-inertial estimation and compact inter-aircraft state/observation exchange. Relevant research direction; payload/compute cost and performance require platform validation.
- [PX4 controller diagrams](https://docs.px4.io/main/en/flight_stack/controller_diagrams): cascaded feedback control from position/velocity to attitude/angular rate and actuator allocation.
- [ArduPilot battery monitor](https://ardupilot.org/copter/docs/common-power-module-configuration-in-mission-planner.html): measuring voltage/current and calibration requirements.
- [Persistent Surveillance With Energy-Constrained UAVs](https://arxiv.org/abs/1908.05727): partitioning surveillance and scheduling recharging; research assumptions differ from fixed field bases.
- [Risk-aware recharging allocation](https://arxiv.org/abs/2209.06308): stochastic energy consumption in joint UAV/ground-vehicle scheduling. Supports accounting for uncertainty rather than using battery percentage alone.

Remaining uncertainty: aircraft performance, validated sensing, actual communications capacity, operator staffing, specific permissions and production runtime configuration. The report's bandwidth, coverage and ground-logistics figures are transparent example calculations rather than sourced measurements.
