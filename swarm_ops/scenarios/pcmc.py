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
              "jcb": "engineering", "water_tanker": "water_tanker", "supply_truck": "supply_truck", "bus": "bus", "police": "police"}

TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "assets" / "operations" / "disruptionops-taxonomy.json"

# The DisruptionOps scenarios.py report groups, in order, carrying only what a real report carries:
# category, text, caller count, people the text states (None = not stated), vulnerable/injury hints and
# hazards the text implies, and the reported height (terrace). No unit kinds: the response is derived
# (REASONER's plan_response, or until it lands the labelled taxonomy fallback below).
PAWANA_NIGHT = [
    {"hotspot": "ravet-bridge", "category": "person_stranded", "text": "Pawana water entering houses near Ravet bridge, family on terrace",
     "callers": 3, "reported_people": None, "vulnerable": ["family"], "injuries": [], "hazards": ["rising_water"], "height_m": 9.0},
    {"hotspot": "morya-ghat", "category": "person_stranded", "text": "River water at Morya Gosavi ghat lanes, elderly couple inside",
     "callers": 2, "reported_people": 2, "vulnerable": ["elderly"], "injuries": [], "hazards": ["rising_water"], "height_m": 4.0},
    {"hotspot": "akurdi-underpass", "category": "flooded_road", "text": "Akurdi station underpass fully under water, two-wheelers stuck",
     "callers": 2, "reported_people": None, "vulnerable": [], "injuries": [], "hazards": ["deep_water"], "height_m": 0.0},
    {"hotspot": "pimpri-camp", "category": "waterlogging", "text": "Pimpri camp market waterlogged, shops flooding",
     "callers": 1, "reported_people": None, "vulnerable": [], "injuries": [], "hazards": ["standing_water"], "height_m": 0.0},
    {"hotspot": "kudalwadi-chemical", "category": "fire", "text": "Fire at a chemical unit in MIDC, strong gas smell spreading",
     "callers": 2, "reported_people": None, "vulnerable": [], "injuries": [], "hazards": ["fire", "gas", "chemical"], "height_m": 0.0},
    {"hotspot": "sane-chowk", "category": "air_quality", "text": "Smoke and gas smell, people coughing near Sane chowk",
     "callers": 1, "reported_people": None, "vulnerable": [], "injuries": ["respiratory"], "hazards": ["smoke", "gas"], "height_m": 0.0},
    {"hotspot": "nigdi-wall", "category": "structural_damage", "text": "Compound wall collapsed on parked cars after rain, someone trapped",
     "callers": 2, "reported_people": 1, "vulnerable": [], "injuries": ["trapped"], "hazards": ["collapse", "debris"], "height_m": 0.0},
    {"hotspot": "pccoe-lane", "category": "power_line", "text": "Live wire fallen in waterlogged lane near PCCOE, sparks",
     "callers": 1, "reported_people": None, "vulnerable": [], "injuries": [], "hazards": ["live_wire", "standing_water"], "height_m": 0.0},
    {"hotspot": "birla-marg", "category": "fallen_tree", "text": "Tree across the road near Aditya Birla hospital, ambulances blocked",
     "callers": 1, "reported_people": None, "vulnerable": [], "injuries": [], "hazards": ["road_blocked"], "height_m": 0.0},
    {"hotspot": "sb-patil-shelter", "category": "shelter_full", "text": "S B Patil school shelter full, more families arriving",
     "callers": 1, "reported_people": None, "vulnerable": ["families"], "injuries": [], "hazards": [], "height_m": 0.0},
    {"hotspot": "ravet-relief-camp", "category": "supply_shortage", "text": "Ravet relief camp short of drinking water",
     "callers": 1, "reported_people": None, "vulnerable": [], "injuries": [], "hazards": ["no_drinking_water"], "height_m": 0.0},
]
POLICE_KIND = {"transport": "road", "agency": "Pimpri Chinchwad Police", "capacity": 4, "model": "police", "carries_people": False}


@lru_cache(maxsize=1)
def taxonomy():
    try:
        return json.loads(TAXONOMY_PATH.read_text(encoding="utf8"))
    except (OSError, ValueError):
        return {}


def fallback_required(category, fleet_kinds):
    """Labelled fallback until REASONER's plan_response lands: for each capability the DisruptionOps
    taxonomy says the category needs, the fleet kind that provides it best (kinds_providing score).
    Pure data lookup; no per-category table here."""
    tax = taxonomy()
    needs = next((c.get("needs") or {} for c in tax.get("incident_categories", []) if c["id"] == category), {})
    out = []
    for capability in needs:
        offers = [(score, kind) for kind, score in (tax.get("kinds_providing", {}).get(capability) or {}).items()
                  if UNIT_KINDS.get(kind, kind) in fleet_kinds]
        if offers:
            kind = UNIT_KINDS.get(max(offers)[1], max(offers)[1])
            if kind not in out:
                out.append(kind)
    return out


def base_severity(category, default=3):
    return next((c["base_severity"] for c in taxonomy().get("incident_categories", []) if c["id"] == category), default)


def _reasoner():
    try:
        from swarm_ops.reasoning import plan_response  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def region():
    """The PCMC region world dict (read once; the engine deep-copies it per simulation)."""
    return json.loads(WORLD_PATH.read_text(encoding="utf8"))


def _place_units(sim, world):
    """All PC-* units (migration 029) at their real stations; boats on the Pawana at their ghats."""
    for unit in world["units"]:
        kind = UNIT_KINDS.get(unit["kind"])
        if kind is None:
            continue  # a kind the simulator does not know
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
    fleet_kinds = {r["kind"] for r in sim.resources}
    reasoner = _reasoner()
    for report in PAWANA_NIGHT:
        spot = spots[report["hotspot"]]
        category = report["category"]
        x, y = spot["site_xy"]
        if "rising_water" not in report["hazards"]:
            # Reports from the street are located on the open road nearest the real place.
            snapped = sim.nearest_road_point([x, y], max_distance=120)
            if snapped is not None:
                x, y = snapped[0], snapped[1]
        # Hint only: REASONER (plan_response) derives the response; until it exists, the
        # DisruptionOps taxonomy needs -> best providing kind (data, not a per-category table).
        required = fallback_required(category, fleet_kinds)
        people = report["reported_people"] or report["callers"]
        incident = sim.add_incident([x, y, report["height_m"]], DISRUPTIONOPS_CATEGORIES[category], title=report["text"],
                                    severity=base_severity(category), people=people, needs=["assessment"],
                                    required=required or None, source="sos")
        incident.update(disruptionops_category=category, report_text=report["text"], callers=report["callers"],
                        reported_people=report["reported_people"], vulnerable=list(report["vulnerable"]), injuries=list(report["injuries"]),
                        hazards_reported=list(report["hazards"]), reported_at=round(sim.t, 2), ward_id=spot["site_ward_id"],
                        named_ward=spot["named_ward"], place=spot["name"], hotspot_id=report["hotspot"],
                        required_hint_only=True,
                        required_source="reasoner" if reasoner else ("fallback:disruptionops-taxonomy" if required else "fallback:engine-template"))


def build_random(sim):
    world = sim.world
    _configure(sim, world)
    _place_docks(sim, world, roles=("core", "coverage"))
    _place_units(sim, world)


def register():
    engine.register_world(WORLD_ID, region)
    # PC-POL units (migration 032). The taxonomy gives police traffic_control (+ field_assessment);
    # they are only tasked where a category's needs ask for that.
    engine.RESOURCE_KINDS.setdefault("police", dict(POLICE_KIND))
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
