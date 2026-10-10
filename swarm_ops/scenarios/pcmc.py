"""PCMC zone round PCCOE (DisruptionOps migration 029) as a simulator region with two scenarios.

pccoe_pawana_night  mirrors DisruptionOps backend/app/demo/scenarios.py::pccoe_pawana_night: the
                    same reports (ward, category, note, caller count) placed at plausible real
                    points in the named wards, the 32 PC-* units at their real stations, drone
                    docks at PCCOE, the PCMC fire stations and two hospitals.
pcmc_random         same docks and units, no incidents; ``random_incident`` creates them.

World: assets/operations/pcmc-region.json (tools/prepare_pcmc_region.py). Positions are local
metres east/north of the PCCOE anchor.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from swarm_ops import engine

WORLD_ID = "pcmc-region"
WORLD_PATH = Path(__file__).resolve().parents[2] / "assets" / "operations" / "pcmc-region.json"
# Launch caps for a 14 km region: up to 3 aircraft over one incident area, ~8 airborne in total.
DISPATCH_CAP = {"per_incident": 3, "total": 8}

# DisruptionOps category -> swarm category (engine INCIDENT_TEMPLATES), registered as aliases so
# operators/integrations may use the DisruptionOps ids directly. setdefault: ENGINE may map better.
DISRUPTIONOPS_CATEGORIES = {
    "person_stranded": "flood", "flooded_road": "blocked_route", "waterlogging": "blocked_route",
    "fire": "fire", "air_quality": "medical", "structural_damage": "trapped", "power_line": "blocked_route",
    "fallen_tree": "blocked_route", "shelter_full": "trapped", "supply_shortage": "medical",
    "evacuation": "trapped", "traffic_corridor": "blocked_route",
}
# DisruptionOps resource kind -> simulator resource kind.
UNIT_KINDS = {"fire_engine": "fire_engine", "ambulance": "ambulance", "boat": "boat", "rescue_team": "rescue", "pump": "pump",
              "jcb": "engineering", "water_tanker": "water_tanker", "supply_truck": "supply_truck", "bus": "bus"}

# One row per DisruptionOps report group (scenarios.py order): hotspot, DO category, note, callers,
# then the swarm side: severity (DO base_severity), people, needs, required unit kinds, height (m).
# Required kinds follow the DO incident_category_needs capability: water_rescue->boat,
# dewatering->pump, fire_suppression->fire_engine, medical_transport->ambulance,
# search_rescue->rescue, debris_clearance->engineering (JCB), mass_transport->bus,
# supply_delivery->water_tanker.
PAWANA_NIGHT = [
    ("ravet-bridge", "person_stranded", "Pawana water entering houses near Ravet bridge, family on terrace", 3,
     5, 5, ["food", "rescue"], ["boat"], 9.0),
    ("morya-ghat", "person_stranded", "River water at Morya Gosavi ghat lanes, elderly couple inside", 2,
     5, 2, ["first_aid", "rescue"], ["boat"], 4.0),
    ("akurdi-underpass", "flooded_road", "Akurdi station underpass fully under water, two-wheelers stuck", 2,
     3, 3, ["assessment"], ["pump", "rescue"], 0.0),
    ("pimpri-camp", "waterlogging", "Pimpri camp market waterlogged, shops flooding", 1,
     2, 4, ["assessment"], ["pump"], 0.0),
    ("kudalwadi-chemical", "fire", "Fire at a chemical unit in MIDC, strong gas smell spreading", 2,
     5, 6, ["assessment", "first_aid"], ["fire_engine", "ambulance"], 0.0),
    ("sane-chowk", "air_quality", "Smoke and gas smell, people coughing near Sane chowk", 1,
     3, 4, ["first_aid", "medical"], ["ambulance"], 0.0),
    ("nigdi-wall", "structural_damage", "Compound wall collapsed on parked cars after rain, someone trapped", 2,
     4, 1, ["assessment", "first_aid"], ["rescue", "engineering", "ambulance"], 0.0),
    ("pccoe-lane", "power_line", "Live wire fallen in waterlogged lane near PCCOE, sparks", 1,
     4, 2, ["assessment"], ["engineering"], 0.0),
    ("birla-marg", "fallen_tree", "Tree across the road near Aditya Birla hospital, ambulances blocked", 1,
     2, 2, ["assessment"], ["engineering"], 0.0),
    ("sb-patil-shelter", "shelter_full", "S B Patil school shelter full, more families arriving", 1,
     3, 30, ["food"], ["bus"], 0.0),
    ("ravet-relief-camp", "supply_shortage", "Ravet relief camp short of drinking water", 1,
     3, 150, ["food"], ["water_tanker"], 0.0),
]


@lru_cache(maxsize=1)
def region():
    """The PCMC region world dict (read once; the engine deep-copies it per simulation)."""
    return json.loads(WORLD_PATH.read_text(encoding="utf8"))


def _place_units(sim, world):
    """All PC-* units (migration 029) at their real stations; boats on the Pawana at their ghats."""
    for unit in world["units"]:
        kind = UNIT_KINDS.get(unit["kind"])
        if kind is None:
            continue  # police (migration 032) have no simulator kind yet
        real = unit["local_xy"]
        if kind == "boat":
            position = list(unit["river_xy"])
        else:
            position = sim.nearest_road_point(real, max_distance=250) or sim.nearest_road_point(unit["road_xy"], max_distance=400)
            if position is None:
                continue
        station = unit["name"][unit["name"].find("(") + 1:-1] if unit["name"].endswith(")") else ""
        sim.add_resource(kind, position, name=unit["name"], id=unit["source_id"], agency=unit["operator"], capacity=unit["capacity"],
                         catalog_kind=unit["kind"], agency_id=unit["agency_id"], station=station, ward_id=unit["ward_id"],
                         real_position=[real[0], real[1], 0.0], source_catalog_id=unit["source_id"])


def _place_docks(sim, world, roles=("core",)):
    """Core docks: PCCOE, fire stations, two hospitals. Coverage docks (real OSM hospitals, schools, police)
    put every road within one battery's reach (~1.8 km) for incidents anywhere in the region."""
    for site in world["dock_sites"]:
        if site.get("role", "core") not in roles:
            continue
        position = sim.nearest_road_point(site["local_xy"], max_distance=200) or [*site["local_xy"], 0.0]
        dock = sim.add_dock(position, name=site["name"])
        dock["site_id"] = site["id"]


def _configure(sim, world):
    sim.dispatch_cap = dict(DISPATCH_CAP)
    sim.scenario.update(dispatch_cap=DISPATCH_CAP["total"], dispatch_cap_per_incident=DISPATCH_CAP["per_incident"],
                        region=world["id"], wards=len(world["wards"]))


def build_pawana_night(sim):
    world = sim.world
    _configure(sim, world)
    _place_docks(sim, world)
    _place_units(sim, world)
    spots = {h["id"]: h for h in world["hotspots"]}
    for hotspot_id, category, note, callers, severity, people, needs, required, height in PAWANA_NIGHT:
        spot = spots[hotspot_id]
        x, y = spot["site_xy"]
        if category not in ("person_stranded",):
            # Ground incidents sit on an open road (the real place is a few metres off it).
            snapped = sim.nearest_road_point([x, y], max_distance=120)
            if snapped is not None:
                x, y = snapped[0], snapped[1]
        incident = sim.add_incident([x, y, height], DISRUPTIONOPS_CATEGORIES[category], title=note, severity=severity, people=people,
                                    needs=needs, required=required, source="sos")
        incident.update(disruptionops_category=category, ward_id=spot["site_ward_id"], named_ward=spot["named_ward"],
                        place=spot["name"], callers=callers, hotspot_id=hotspot_id)


def build_random(sim):
    world = sim.world
    _configure(sim, world)
    _place_docks(sim, world, roles=("core", "coverage"))
    _place_units(sim, world)


def register():
    engine.register_world(WORLD_ID, region)
    for do_category, swarm_category in DISRUPTIONOPS_CATEGORIES.items():
        engine.CATEGORY_ALIASES.setdefault(do_category, swarm_category)
    engine.register_scenario("pccoe_pawana_night", "PCMC: Pawana night round PCCOE",
                             "DisruptionOps pccoe_pawana_night on the 14 km PCMC region: Pawana over its banks at Ravet and Chinchwad Gaon, "
                             "Akurdi underpass, Pimpri camp waterlogging, Chikhli MIDC chemical fire and smoke, Nigdi wall collapse, live wire "
                             "near PCCOE, Thergaon tree, Ravet shelter full and relief camp short of water. 32 PC-* units at real stations; "
                             "drones docked at PCCOE, fire stations and hospitals.",
                             build_pawana_night, world_id=WORLD_ID)
    engine.register_scenario("pcmc_random", "PCMC: docks and units only",
                             "The PCMC region with every PC-* unit at its station and the drone docks; add incidents with random_incident.",
                             build_random, world_id=WORLD_ID)


register()
