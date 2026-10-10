"""DisruptionOps taxonomy (capabilities, resource kinds, incident categories) as data.

Source of truth: assets/operations/disruptionops-taxonomy.json, produced by
tools/build_taxonomy.py from the DisruptionOps migrations. Both the documented
list shape ({capabilities:[...], resource_kinds:[{capabilities:[{id,effectiveness}]}],
categories:[{needs:[{capability,qty}]}]}) and the dict shape the builder emits
({resource_kinds:[{capabilities:{id:eff}}], incident_categories:[{needs:{cap:qty}}]})
are accepted. If the file is missing, a built-in copy of the same migration rows
(001, 006, 012, 018, 019, 023, 032, 035) is used so the engine still runs offline.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "assets" / "operations" / "disruptionops-taxonomy.json"

# Simulator kind <-> DisruptionOps kind.
SIM_TO_DO = {"rescue": "rescue_team", "engineering": "jcb"}
DO_TO_SIM = {"rescue_team": "rescue", "jcb": "engineering"}

_BUILTIN = {
    "capabilities": [{"id": c, "label": c.replace("_", " ")} for c in (
        "water_rescue", "dewatering", "medical_transport", "medical_care", "mass_transport", "debris_clearance",
        "fire_suppression", "search_rescue", "supply_delivery", "water_supply", "field_assessment", "traffic_control")],
    "resource_kinds": [
        {"id": "boat", "display_name": "Rescue boat", "default_capacity": 6, "capabilities": {"water_rescue": 1.0, "search_rescue": 0.7, "field_assessment": 0.6}},
        {"id": "pump", "display_name": "Dewatering pump", "default_capacity": 1, "capabilities": {"dewatering": 1.0}},
        {"id": "ambulance", "display_name": "Ambulance", "default_capacity": 4, "capabilities": {"medical_transport": 1.0, "medical_care": 0.7, "field_assessment": 0.8}},
        {"id": "fire_engine", "display_name": "Fire engine", "default_capacity": 6, "capabilities": {"fire_suppression": 1.0, "dewatering": 0.5, "water_rescue": 0.6, "field_assessment": 0.8}},
        {"id": "rescue_team", "display_name": "Rescue team", "default_capacity": 8, "capabilities": {"search_rescue": 1.0, "water_rescue": 0.8, "debris_clearance": 0.6, "field_assessment": 1.0}},
        {"id": "bus", "display_name": "Bus", "default_capacity": 40, "capabilities": {"mass_transport": 1.0, "supply_delivery": 0.45}},
        {"id": "jcb", "display_name": "Earthmover", "default_capacity": 1, "capabilities": {"debris_clearance": 1.0}},
        {"id": "supply_truck", "display_name": "Relief supply truck", "default_capacity": 400, "capabilities": {"supply_delivery": 1.0, "mass_transport": 0.4}},
        {"id": "water_tanker", "display_name": "Water tanker", "default_capacity": 10000, "capabilities": {"water_supply": 1.0, "supply_delivery": 0.6}},
        {"id": "police", "display_name": "Police vehicle", "default_capacity": 4, "capabilities": {"traffic_control": 1.0, "field_assessment": 0.8}},
        {"id": "helicopter", "display_name": "Helicopter", "default_capacity": 6, "capabilities": {"water_rescue": 0.9, "search_rescue": 0.8, "medical_transport": 0.7, "supply_delivery": 0.5}},
    ],
    "incident_categories": [
        {"id": "flooded_road", "base_severity": 3, "life_safety": False, "needs": {"dewatering": 1}},
        {"id": "waterlogging", "base_severity": 2, "life_safety": False, "needs": {"dewatering": 1}},
        {"id": "fallen_tree", "base_severity": 2, "life_safety": False, "needs": {"debris_clearance": 1}},
        {"id": "blocked_drain", "base_severity": 2, "life_safety": False, "needs": {"dewatering": 1}},
        {"id": "structural_damage", "base_severity": 4, "life_safety": True, "needs": {"search_rescue": 1, "traffic_control": 1}},
        {"id": "person_stranded", "base_severity": 5, "life_safety": True, "needs": {"water_rescue": 1}},
        {"id": "power_line", "base_severity": 4, "life_safety": True, "needs": {"debris_clearance": 1}},
        {"id": "heat_casualty", "base_severity": 4, "life_safety": True, "needs": {"medical_transport": 1}},
        {"id": "supply_shortage", "base_severity": 3, "life_safety": False, "needs": {"supply_delivery": 1}},
        {"id": "shelter_full", "base_severity": 3, "life_safety": False, "needs": {"mass_transport": 1}},
        {"id": "fire", "base_severity": 5, "life_safety": True, "needs": {"fire_suppression": 1, "medical_transport": 1, "traffic_control": 1}},
        {"id": "unknown_report", "base_severity": 3, "life_safety": False, "needs": {"field_assessment": 1}},
        {"id": "air_quality", "base_severity": 3, "life_safety": False, "needs": {}},
        {"id": "earthquake_damage", "base_severity": 5, "life_safety": True, "needs": {"search_rescue": 1, "medical_transport": 1}},
        {"id": "evacuation", "base_severity": 4, "life_safety": True, "needs": {}},
        {"id": "traffic_corridor", "base_severity": 3, "life_safety": False, "needs": {}},
    ],
}

# Simulator categories that are not DisruptionOps ids map onto the nearest one.
CATEGORY_ALIASES = {"flood": "person_stranded", "trapped": "structural_damage", "medical": "heat_casualty",
                    "blocked_route": "fallen_tree", "chemical": "fire", "collapse": "structural_damage",
                    "live_wire": "power_line", "mass_casualty": "earthquake_damage"}


class Taxonomy:
    def __init__(self, raw):
        self.raw = raw
        self.capabilities = {c["id"]: c.get("label", c["id"]) for c in raw.get("capabilities", [])}
        self.kinds = {}
        for k in raw.get("resource_kinds", []):
            caps = k.get("capabilities") or {}
            if isinstance(caps, list):
                caps = {c["id"]: float(c.get("effectiveness", 1.0)) for c in caps}
            self.kinds[k["id"]] = {"id": k["id"], "display_name": k.get("display_name", k["id"]),
                                   "default_capacity": int(k.get("default_capacity") or 1),
                                   "capabilities": {c: float(e) for c, e in caps.items()}}
        self.categories = {}
        for c in raw.get("incident_categories", raw.get("categories", [])):
            needs = c.get("needs") or {}
            if isinstance(needs, list):
                needs = {n["capability"]: int(n.get("qty", 1)) for n in needs}
            self.categories[c["id"]] = {"id": c["id"], "base_severity": int(c.get("base_severity", 3)),
                                        "life_safety": bool(c.get("life_safety", False)), "needs": dict(needs),
                                        "hazard": c.get("hazard_id", c.get("hazard")),
                                        "keywords": [(k["phrase"].lower(), float(k.get("weight", 1.0)))
                                                     for k in c.get("keywords", []) if k.get("phrase")]}
        # capability -> {kind: effectiveness}
        self.providers = {}
        for kid, k in self.kinds.items():
            for cap, eff in k["capabilities"].items():
                self.providers.setdefault(cap, {})[kid] = eff

    def kind(self, kind):
        """Canonical DisruptionOps kind id for a DisruptionOps or simulator kind, or None."""
        kind = SIM_TO_DO.get(kind, kind)
        return kind if kind in self.kinds else None

    def category(self, category):
        category = CATEGORY_ALIASES.get(category, category)
        return self.categories.get(category) or self.categories.get("unknown_report") or {
            "id": "unknown_report", "base_severity": 3, "life_safety": False, "needs": {}, "keywords": []}

    def classify(self, text):
        """Keyword-weighted category scores from the DisruptionOps keyword table (data, not code)."""
        text = (text or "").lower()
        scores = {}
        for cid, c in self.categories.items():
            s = sum(w for phrase, w in c["keywords"] if re.search(r"(?<!\w)" + re.escape(phrase), text))
            if s:
                scores[cid] = s
        return dict(sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])))


@lru_cache(maxsize=4)
def _load(path_str):
    path = Path(path_str)
    try:
        return Taxonomy(json.loads(path.read_text(encoding="utf8")))
    except (OSError, ValueError, KeyError, TypeError):
        return Taxonomy(_BUILTIN)


def load_taxonomy(override=None):
    """Taxonomy object. `override` may be a Taxonomy, a raw dict, or a path."""
    if isinstance(override, Taxonomy):
        return override
    if isinstance(override, dict):
        return Taxonomy(override)
    return _load(str(override or TAXONOMY_PATH))


def builtin_taxonomy():
    return Taxonomy(_BUILTIN)
