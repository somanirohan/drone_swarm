# PCMC region and the `pccoe_pawana_night` scenario

This is the DisruptionOps PCMC zone around PCCOE (migration `029_pcmc_pccoe_zone.sql`), set up as a
simulator world of 13.7 km x 14.0 km. The DisruptionOps disaster scenario `pccoe_pawana_night` is placed
on that map.

| File | Role |
|---|---|
| `tools/prepare_pcmc_region.py` | Builds the world from a read-only DisruptionOps checkout (migrations 029/030/032 and `data/raw/osm`). |
| `assets/operations/pcmc-region.json` | The world (about 1.5 MB). |
| `swarm_ops/scenarios/pcmc.py` | Registers world `pcmc-region` and the scenarios `pccoe_pawana_night` and `pcmc_random`. |
| `swarm_ops/scenarios/__init__.py` | Importing this package registers the plug-ins. |
| `tests/test_pcmc_region.py` | Checks the data and the scenario definition. |

Rebuild the world:

    python tools/prepare_pcmc_region.py --source D:/drone_research_reference/DisruptionOps-latest

Run the server on this world:

    python -m swarm_ops.server --world pcmc-region [--scenario pcmc_random]

`GET /ops/scenarios` lists every registered scenario together with its `world_id`.

## World (`world.kind = "region"`, `world.id = "pcmc-region"`)

- **Projection.** Positions are local metres east/north of PCCOE (18.6526632 N, 73.7624378 E). This is the
  same projection as `tools/build_agency_catalog.py`. The envelope is 73.715–73.845 E, 18.585–18.712 N, and
  `bounds` is `[-5009.4, -7481.5, 8718.6, 6560.8]`.
- **Roads.**
  - Kept everywhere: motorway, trunk, primary, secondary, tertiary, their links, and unclassified roads.
  - Kept near places that matter: residential and living_street roads within 550 m of a hotspot, unit,
    lifeline or dock; service roads within 140 m of a unit, lifeline or dock.
  - Ways are split at shared OSM nodes, so a junction exists only where the roads really meet. Flyovers stay
    separate from the roads under them.
  - Chains with the same name and class are merged, then simplified with Douglas–Peucker at 3 m. Only the
    largest connected component is kept.
  - `road_network.prebuilt = true` tells the engine that polyline end points are the junctions, so it can
    skip the segment intersection pass.
- **Water.**
  - `river` is the Pawana centreline simplified to 15 m, with `river_width` 50.
  - `waterways` holds the Pawana at 4 m detail, plus the Mula, Indrayani, Kasarsai, streams and drains, each
    with a width.
  - `water_areas` holds the riverbank, lake and pond polygons.
  - `river_crossings` lists the 13 real Pawana bridges where they cross the road network.
  - `flood_zones` mark the overflowed bank in the 3 reaches where migration 029 expects the Pawana to
    overflow: Ravet (±700 m along the river), Chinchwad Gaon (±600 m) and Pimpri (±500 m). Each reach is a
    chain of 100 m circles placed every 120 m on the centreline, so the corridor reaches about 100 m either
    side of the river. Streets set back from the river, such as the Pimpri camp market, stay dry.
  - The engine floods only inside `flood_zones`, and it keeps the real bridges in `river_crossings`
    usable.
- **Wards.** These are the 14 Voronoi cells of the ward centroids, clipped to the envelope. They are
  computed in lng/lat degrees, exactly as migration 029 does. Each ward carries its population, elderly
  share and elevation (planning estimates from DisruptionOps) and its sector and hazards from migration 030.
- **Lifelines.** 16 from migration 029, with ward, capacity and specialities.
- **Units.** All 32 PC-* units from migration 029, plus the 4 PC-POL police units from migration 032. Each
  unit records:
  - its real `local_xy`;
  - `road_xy`, the nearest kept road point, or `river_xy` for a boat;
  - its `ward_id`.

  `stations` groups the units by station.
- **Other.**
  - `dock_sites` come in two roles:
    - 8 `core` docks: PCCOE campus; the Nigdi, Pimpri, Thergaon, Rahatani and Chikhali fire stations; YCM
      Hospital; Global Hospital Ravet.
    - 15 `coverage` docks: real OSM hospitals, schools and a police station, chosen greedily until 98.8% of
      road junctions are within 1.8 km of a dock. That is one battery's reach under the engine's energy
      model (110 Wh, 0.018 Wh/m, 29 Wh reserve).

    `pccoe_pawana_night` uses the core docks; `pcmc_random` uses all 23.
  - `no_fly`: the Dehu Road military area, plus the edge of CME (OSM `landuse=military`). Migration 030 tags
    the Dehu Road sector with the hazard `airspace`.
  - `buildings: []`. The OSM context extract has no footprints, so procedural blocks are left to the
    frontend.
  - `terrain` is flat.

## Scenario `pccoe_pawana_night`

These are the report groups from DisruptionOps `scenarios.py`, in the same order, with the same text and
caller counts.

**What each report carries.** Only what a caller would say:

| Field | Meaning |
|---|---|
| `disruptionops_category` | The DisruptionOps category id |
| `report_text` | The report text |
| `callers` | Caller count |
| `reported_people` | People the text states, else None |
| `vulnerable` | Vulnerability hints from the text, e.g. `elderly`, `family` |
| `injuries` | Injury hints from the text, e.g. `trapped`, `respiratory` |
| `hazards_reported` | Hazards from the text, e.g. `rising_water`, `gas`, `live_wire`, `chemical` |
| `reported_at` | Time of the report |
| `ward_id`, `named_ward` | The ward the site lies in, and the ward DisruptionOps names |

There is no per-category response table.

**How the response is derived.** The units an incident needs come from REASONER's `plan_response`, in
`swarm_ops.reasoning`. Until that lands, `fallback_required()` looks them up in
`assets/operations/disruptionops-taxonomy.json`. For each capability the category needs
(`incident_categories[].needs`), it picks the fleet kind with the highest `kinds_providing` score.

Every incident records:
- `required_hint_only = true`;
- `required_source`, set to `reasoner`, `fallback:disruptionops-taxonomy`, or `fallback:engine-template`.

`air_quality` has no needs in the taxonomy, so it falls back to the engine template.

**Police.** The PC-POL units from migration 032 are spawned as kind `police` (model `police`). The
taxonomy gives police `traffic_control`, and only `fire` and `structural_damage` need that capability, so
those are the only incidents where police are tasked.

**Ward note.** By the Voronoi cells of migration 029, the real Ravet relief camp lies in w-pc-01, while
DisruptionOps files the report under Ravet (w-pc-03). The incident sits at the real camp and records both
wards.

**Docks and launch caps.** Docks are at the 8 dock sites, and the fleet is spread over them round-robin.
`sim.dispatch_cap = {"per_incident": 3, "total": 8}`. The engine applies this only once it reads it;
the global `DOCK_DISPATCH_CAP` is 3.

`pcmc_random` sets up the same docks and units with no incidents.

## What is approximate

- Incident points are plausible real places, not reported coordinates. Sane chowk and the Kudalwadi
  chemical unit are placed from local knowledge, not from OSM names.
- The river width is one constant per river.
- Ward populations and elevations are DisruptionOps planning estimates.
- Unit inventories are the DisruptionOps demo fleet.
- Two boats are placed well away from the river: PC-BOAT-01 is mapped 374 m off the Pawana and PC-BOAT-03
  498 m off it. They launch from the nearest river point instead.
