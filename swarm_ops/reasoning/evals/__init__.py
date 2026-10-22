"""Scenario suite for the onboard decision engine.

Each case is a situation (as the command center / engine would send it) plus soft
expectations; pair checks prove decisions change with crowd size, injuries,
vulnerability and trend *within the same category*. Run:

    python -m swarm_ops.reasoning.evals [--json] [--solver greedy|cpsat]
"""
from __future__ import annotations

import time

from ..planner import Reasoner

ESC = {"single_unit": 0, "multi_unit": 1, "full_team": 2}


def kinds(plan):
    return {q["kind"]: q["qty"] for q in plan["agency_requests"]}


def urgent(plan):
    return {q["kind"]: q["qty"] for q in plan["agency_requests"] if q["urgency"] == "immediate"}


def roles(plan):
    return {(d["role"], d["payload"]): d["count"] for d in plan["drones"]}


def has(*ks):
    return (f"requests {'/'.join(ks)}", lambda p: any(k in kinds(p) for k in ks))


def lacks(*ks):
    return (f"no {'/'.join(ks)}", lambda p: not any(k in kinds(p) for k in ks))


def role(r):
    return (f"drone role {r}", lambda p: any(k[0] == r for k in roles(p)))


def at_least(kind, n):
    return (f">= {n} {kind}", lambda p: kinds(p).get(kind, 0) >= n)


def esc_at_least(level):
    return (f"escalation >= {level}", lambda p: ESC[p["escalation"]] >= ESC[level])


def esc_at_most(level):
    return (f"escalation <= {level}", lambda p: ESC[p["escalation"]] <= ESC[level])


def action(word):
    return (f"public action mentions '{word}'", lambda p: any(word in a.lower() for a in p["public_actions"]))


def total_units_at_most(n):
    return (f"<= {n} ground units", lambda p: sum(kinds(p).values()) <= n)


ROOFTOP = {"disruptionops_category": "person_stranded", "report_text": "Family of 5 on the rooftop, water rising fast, grandmother cannot walk",
           "callers": 2, "reported_people": 5, "vulnerable": ["elderly", "children"], "hazards_reported": ["rising_water"], "height_m": 6.0}

CASES = [
    {"id": "elderly_fall", "situation": {"disruptionops_category": "heat_casualty", "report_text": "Elderly man collapsed in the heat, conscious but injured",
                                          "callers": 1, "reported_people": 1, "vulnerable": ["elderly"], "injuries": ["injured"], "hazards_reported": ["heat"]},
     "expect": [has("ambulance"), lacks("boat", "bus", "fire_engine"), esc_at_most("multi_unit")]},
    {"id": "rooftop_family_rising_water", "situation": ROOFTOP,
     "expect": [has("boat", "rescue_team"), lacks("bus", "pump"), esc_at_least("multi_unit"), action("highest floor")]},
    {"id": "crowd_full_shelter", "situation": {"disruptionops_category": "shelter_full", "report_text": "Shelter full, 40 people waiting outside in the rain",
                                                "callers": 1, "reported_people": 40, "vulnerable": ["families"], "hazards_reported": ["crowding"],
                                                "nearby_lifelines": [{"kind": "shelter", "name": "S B Patil school"}]},
     "expect": [has("bus"), lacks("boat", "fire_engine"), action("shelter")]},
    {"id": "chemical_fire_two_casualties", "situation": {"disruptionops_category": "fire", "report_text": "Fire at chemical unit, strong gas smell, 2 injured workers",
                                                          "callers": 3, "reported_people": 6, "injuries": ["burns", "respiratory"],
                                                          "injured": 2, "hazards_reported": ["fire", "gas", "chemical"]},
     "expect": [at_least("fire_engine", 2), has("ambulance"), has("police"), lacks("boat"), esc_at_least("multi_unit"), action("upwind")]},
    {"id": "wall_collapse_one_trapped", "situation": {"disruptionops_category": "structural_damage", "report_text": "Compound wall collapsed, someone trapped under rubble",
                                                       "callers": 2, "reported_people": 1, "injuries": ["trapped"], "hazards_reported": ["collapse", "debris"]},
     "expect": [has("rescue_team"), lacks("boat", "bus"), action("collapse zone")]},
    {"id": "live_wire_waterlogged_lane", "situation": {"disruptionops_category": "power_line", "report_text": "Live wire fallen in waterlogged lane, sparks",
                                                        "callers": 1, "hazards_reported": ["live_wire", "standing_water"]},
     "expect": [has("police", "jcb"), lacks("boat", "bus"), action("live wire")]},
    {"id": "fallen_tree_blocking_ambulances", "situation": {"disruptionops_category": "fallen_tree", "report_text": "Tree across the road near the hospital, ambulances blocked",
                                                             "callers": 1, "hazards_reported": ["road_blocked"]},
     "expect": [has("jcb", "rescue_team"), lacks("ambulance", "boat", "bus"), action("divert")]},
    {"id": "relief_camp_out_of_water", "situation": {"disruptionops_category": "supply_shortage", "report_text": "Relief camp with 200 people out of drinking water",
                                                      "callers": 1, "reported_people": 200, "hazards_reported": ["no_drinking_water"]},
     "expect": [has("water_tanker"), lacks("boat", "fire_engine")]},
    {"id": "improving_after_first_responders", "situation": {**ROOFTOP, "t_s": 1500, "onset_s": 0, "observations": [
        {"t_s": 600, "source": "drone", "confidence": 0.85, "people": 5, "immobile": 5, "hazards": {"water_depth_m": 1.2}},
        {"t_s": 1400, "source": "ground", "confidence": 0.95, "people": 5, "immobile": 0, "injured": 0, "trend": "improving",
         "hazards": {"water_depth_m": 0.5}, "responders_on_scene": ["boat"]}]},
     "expect": [esc_at_most("multi_unit"), total_units_at_most(1),
                ("trend improving", lambda p: p["assessment"]["trend"] == "improving")]},
    {"id": "worsening_flood", "situation": {**ROOFTOP, "t_s": 1200, "onset_s": 0, "report_text": "Families stranded, water rising",
                                            "reported_people": 8, "observations": [
        {"t_s": 300, "source": "drone", "confidence": 0.8, "people": 8, "immobile": 8, "hazards": {"water_depth_m": 0.6}},
        {"t_s": 900, "source": "drone", "confidence": 0.8, "people": 8, "immobile": 8, "hazards": {"water_depth_m": 1.1}},
        {"t_s": 1150, "source": "drone", "confidence": 0.8, "people": 9, "immobile": 9, "injured": 1, "hazards": {"water_depth_m": 1.5}}]},
     "expect": [esc_at_least("full_team"), at_least("boat", 2), role("overwatch"),
                ("trend worsening", lambda p: p["assessment"]["trend"] == "worsening")]},
    {"id": "unknown_report", "situation": {"disruptionops_category": "unknown_report", "report_text": "Loud noise near the market, not sure what happened", "callers": 1},
     "expect": [role("assess"), total_units_at_most(1), lacks("boat", "bus", "fire_engine")]},
    {"id": "mass_casualty", "situation": {"disruptionops_category": "earthquake_damage", "report_text": "Building collapse, 25 injured, 6 critical, several trapped",
                                           "callers": 6, "reported_people": 40, "injured": 25, "critical": 6, "trapped": 5,
                                           "hazards_reported": ["collapse", "debris"]},
     "expect": [at_least("ambulance", 3), has("rescue_team"), esc_at_least("full_team")]},
    {"id": "dry_street_flood_report", "situation": {"disruptionops_category": "person_stranded", "report_text": "Report of people stranded, street is dry",
                                                     "callers": 1, "reported_people": 2, "observations": [
        {"source": "drone", "confidence": 0.9, "people": 2, "immobile": 0, "hazards": {"water_depth_m": 0.0}}]},
     "expect": [lacks("boat")]},
    {"id": "air_quality_coughing", "situation": {"disruptionops_category": "air_quality", "report_text": "Smoke and gas smell, people coughing near the chowk",
                                                  "callers": 1, "injuries": ["respiratory"], "hazards_reported": ["smoke", "gas"]},
     "expect": [has("ambulance", "police"), lacks("boat"), action("upwind")]},
]

# Same category, one factor changed: the decision must change in the stated direction.
PAIRS = [
    ("more stranded people -> more water-rescue capacity",
     {**ROOFTOP, "report_text": "2 people stranded on roof, water rising", "reported_people": 2, "vulnerable": []},
     {**ROOFTOP, "report_text": "14 people stranded on roof, water rising", "reported_people": 14},
     lambda a, b: sum(kinds(b).get(k, 0) for k in ("boat", "rescue_team", "fire_engine", "helicopter")) >
                  sum(kinds(a).get(k, 0) for k in ("boat", "rescue_team", "fire_engine", "helicopter"))),
    ("injuries -> ambulances",
     {"disruptionops_category": "structural_damage", "report_text": "Wall collapsed, nobody hurt", "reported_people": 6, "injured": 0, "callers": 2, "hazards_reported": ["collapse"]},
     {"disruptionops_category": "structural_damage", "report_text": "Wall collapsed, 5 injured, 2 critical", "reported_people": 6, "injured": 5, "critical": 2, "callers": 2, "hazards_reported": ["collapse"]},
     lambda a, b: kinds(b).get("ambulance", 0) > kinds(a).get("ambulance", 0)),
    ("vulnerability raises risk",
     {"disruptionops_category": "heat_casualty", "report_text": "Adult feeling faint in heat", "reported_people": 1, "injuries": ["injured"], "callers": 1},
     {"disruptionops_category": "heat_casualty", "report_text": "Bedridden elderly woman feeling faint in heat", "reported_people": 1, "vulnerable": ["elderly", "disabled"], "injuries": ["injured"], "callers": 1},
     lambda a, b: b["explanation"]["risk"] > a["explanation"]["risk"]),
    ("worsening trend escalates at least as high",
     {**ROOFTOP, "report_text": "Family of 5 on rooftop, water level steady", "hazards_reported": ["deep_water"], "trend": "stable"},
     {**ROOFTOP, "report_text": "Family of 5 on rooftop, water rising fast", "hazards_reported": ["deep_water"], "trend": "worsening"},
     lambda a, b: ESC[b["escalation"]] >= ESC[a["escalation"]] and b["reassess_after_s"] < a["reassess_after_s"]),
    ("bigger crowd -> more buses",
     {"disruptionops_category": "shelter_full", "report_text": "Shelter full, 30 people outside", "reported_people": 30, "hazards_reported": ["crowding"], "callers": 1},
     {"disruptionops_category": "shelter_full", "report_text": "Shelter full, 150 people outside", "reported_people": 150, "hazards_reported": ["crowding"], "callers": 3},
     lambda a, b: kinds(b).get("bus", 0) + kinds(b).get("supply_truck", 0) > kinds(a).get("bus", 0) + kinds(a).get("supply_truck", 0)),
]


def run_evals(solver="greedy"):
    ctx = {"params": {"solver": solver}}
    results, lat = [], []
    for case in CASES:
        r = Reasoner()
        t0 = time.perf_counter()
        plan = r.plan({**case["situation"], "incident_id": case["id"]}, ctx)
        lat.append((time.perf_counter() - t0) * 1000)
        checks = [(name, bool(plan) and bool(fn(plan))) for name, fn in case["expect"]]
        results.append({"id": case["id"], "plan": plan, "checks": checks})
    pairs = []
    for name, a, b, fn in PAIRS:
        pa = Reasoner().plan({**a, "incident_id": "A"}, ctx)
        pb = Reasoner().plan({**b, "incident_id": "B"}, ctx)
        pairs.append({"name": name, "ok": bool(pa and pb and fn(pa, pb)), "a": pa, "b": pb})
    passed = sum(ok for r in results for _, ok in r["checks"])
    total = sum(len(r["checks"]) for r in results)
    return {"cases": results, "pairs": pairs, "checks_passed": passed, "checks_total": total,
            "pairs_passed": sum(p["ok"] for p in pairs), "pairs_total": len(pairs),
            "latency_ms": {"max": round(max(lat), 2), "mean": round(sum(lat) / len(lat), 2)}}
