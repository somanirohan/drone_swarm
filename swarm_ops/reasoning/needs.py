"""Needs inference: belief -> capability demands (amount, risk margin, value, urgency).

Two sources, combined per capability:

* Belief-driven demand models. Each life-safety capability has a small physical
  model over the fused belief: e.g. medical transport is needed by the expected
  critical casualties plus half the other injured; water rescue by the expected
  immobile people times the share of immobility explained by water; evacuation
  (mass transport) by mobile people times a smooth hazard-pressure factor. The
  amount carries its own standard deviation, which becomes the risk margin.
* The DisruptionOps taxonomy (incident_category_needs). When no evidence speaks
  to a capability's drivers, the category's listed need stands as a prior (in
  units, weighted by `category_prior_weight`), so an unassessed report still
  gets the response DisruptionOps would give it, and evidence can then raise,
  lower or remove it.

Value `weight` is in people-equivalents of serious harm avoided if the demand is
fully covered; it is what the optimiser trades against unit cost and ETA.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .belief import effective_hazard


@dataclass
class Demand:
    incident_id: str
    capability: str
    amount: float          # mean demand (people, or work units)
    margin: float          # z * sd: extra cover for uncertainty
    weight: float          # people-equivalents of harm avoided at full cover
    tau_s: float           # time constant of harm growth
    measure: str           # "people" | "work" | "units"
    source: str            # "belief" | "category"
    drivers: dict = field(default_factory=dict)


# Which belief variables tell us whether a capability is needed (used to decide when the
# taxonomy's category prior should stand in for missing evidence).
DRIVERS = {
    "medical_transport": ("injured", "critical"), "medical_care": ("injured", "critical"),
    "water_rescue": ("immobile", "@water"), "search_rescue": ("immobile", "@structural"),
    "mass_transport": ("people", "@crowding"), "fire_suppression": ("@fire",), "dewatering": ("@water",),
    "debris_clearance": ("@debris", "@structural", "@electrical"), "traffic_control": (),
    "supply_delivery": ("$food",), "water_supply": ("$water",), "field_assessment": (),
}


def _observed(belief, driver):
    if driver.startswith("@"):
        return driver[1:] in belief.hazard_observed
    if driver.startswith("$"):
        return belief.shortages[driver[1:]].n > 0
    return belief.observed(driver)


def _sq(*xs):
    return math.sqrt(sum(x * x for x in xs))


def infer_demands(incident_id, belief, taxonomy, p, assessing_drone=False):
    """List[Demand] for one incident."""
    b, h = belief, p["harm"]
    N, Nsd = b.mean("people"), b.sd("people")
    I, Isd = b.mean("injured"), b.sd("injured")
    C, Csd = b.mean("critical"), b.sd("critical")
    M, Msd = b.mean("immobile"), b.sd("immobile")
    vm = 1.0 + p["vulnerability_weight"] * b.vulnerable_share()
    hz = {k: b.hazard(k) for k in b.hazards}
    worse = b.trend_label == "worsening"
    tau_f = p["trend_tau_factor"][b.trend_label]
    z = p["margin_z"]
    treated = min(b.treated, I)
    I_open, C_open = max(0.0, I - treated), max(0.0, C - min(treated, C))
    raw = {}

    def add(cap, amount, sd, weight, measure="people", tau=None, **drivers):
        if amount > 1e-6 and weight > 1e-6:
            if tau:
                drivers["_tau"] = tau
            raw[cap] = (amount, z * sd, weight, measure, drivers)

    # Medical: transport for critical + half the other injured; on-site care for all injured.
    minor = max(0.0, I_open - C_open)
    add("medical_transport", C_open + 0.5 * minor, _sq(Csd, 0.5 * Isd),
        (C_open * h["critical"] + minor * h["injured"]) * vm, critical=round(C_open, 2), injured=round(I_open, 2))
    add("medical_care", I_open, Isd, (C_open * h["critical"] * 0.5 + minor * h["injured"] * 0.5) * vm,
        injured=round(I_open, 2))

    # Immobile people, split by the hazard that explains their immobility.
    imm = {k: p["immobile_per_hazard"][k] * effective_hazard(k, hz[k]) for k in p["immobile_per_hazard"]}
    tot = sum(imm.values()) or 1.0
    water_share = imm["water"] / tot if imm["water"] > 0.02 else 0.0
    struct_share = (imm["structural"] + imm["fire"]) / tot if (hz["structural"] >= 0.15 or hz["fire"] >= 0.15) else 0.0
    if water_share + struct_share == 0 and M > 0.5:
        struct_share = 1.0          # immobile for an unknown reason: search and rescue
    pressure_w = 0.5 + hz["water"] + (0.3 if worse else 0.0)
    add("water_rescue", M * water_share, Msd * water_share, M * water_share * h["stranded_water"] * pressure_w * vm,
        immobile=round(M, 2), water=round(hz["water"], 2))
    add("search_rescue", M * struct_share, Msd * struct_share, M * struct_share * h["trapped"] * vm,
        immobile=round(M, 2), structural=round(hz["structural"], 2))

    # Evacuation of mobile people: smooth in the strongest hazard, bumped when worsening.
    H = max(hz["water"], hz["fire"], hz["gas"], hz["structural"], hz["electrical"], hz["crowding"])
    e = max(min(1.0, max(0.0, (H + (0.15 if worse else 0.0) - 0.4) / 0.4)), hz["crowding"])   # crowding is direct pressure
    mobile = max(0.0, N - M - C_open)
    add("mass_transport", mobile * e, Nsd * e * (mobile / N if N > 0 else 0.0), mobile * e * h["evacuation"] * vm, mobile=round(mobile, 2),
        hazard_pressure=round(e, 2))

    # Fire (with gas as an aggravating factor): engines as work units.
    if hz["fire"] >= 0.15:
        add("fire_suppression", 1 + 2 * hz["fire"] + hz["gas"] * hz["fire"], 0.6 * b.hazards["fire"].sd * 3,
            (N * h["fire_exposed"] + N * h["gas_exposed"] * hz["gas"] + 1.0) * hz["fire"] * vm, measure="work",
            fire=round(hz["fire"], 2), gas=round(hz["gas"], 2))

    # Route blockage / debris; a blocked emergency route is worth more than a blocked lane.
    acc = b.access
    debris = max(hz["debris"], 0.7 if acc["road_blocked"] else 0.0)
    if debris >= 0.2 or (hz["structural"] >= 0.4 and M > 0.5):
        w = 0.25 * debris + (1.2 if acc["blocks_emergency_route"] else 0.0) + 0.15 * hz["structural"] * M
        add("debris_clearance", 1.0, 0.0, w, measure="work", tau=1200.0 if acc["blocks_emergency_route"] else None, debris=round(debris, 2),
            blocks_emergency_route=acc["blocks_emergency_route"])

    # Scene control / cordon: proportional to hazards that endanger bystanders and to crowd size.
    danger = max(hz["fire"], hz["gas"], hz["electrical"], hz["crowding"], hz["structural"])
    if danger >= 0.3:
        add("traffic_control", 1.0 + (1.0 if N > 50 else 0.0), 0.0,
            0.1 + 0.3 * danger + N * (h["electrical_exposed"] * hz["electrical"] + h["crowd_crush"] * hz["crowding"]) * 0.2,
            measure="work", danger=round(danger, 2))

    # Shortages (people-days of supply).
    fw, ff = b.shortages["water"].mean, b.shortages["food"].mean
    if fw >= 0.2:
        add("water_supply", N * fw, Nsd * fw, N * fw * h["no_water_per_day"] * vm, water_shortage=round(fw, 2))
    if ff >= 0.2:
        add("supply_delivery", N * ff, Nsd * ff, N * ff * h["no_food_per_day"] * vm, food_shortage=round(ff, 2))

    # Unclassified or poorly known scene: a responder to go and look (cheaper if a drone already looks).
    U = b.uncertainty()
    if b.category["id"] == "unknown_report" or (U > 0.55 and not assessing_drone):
        add("field_assessment", 1.0, 0.0, U * (0.3 + 0.1 * math.log1p(N)) * (0.3 if assessing_drone else 1.0), measure="work",
            uncertainty=U)

    # Dewatering is infrastructure work; evidence of standing water scales it.
    # (interlock: not while a live electrical hazard is in the water)
    if hz["water"] >= 0.2 and M < 0.5:
        add("dewatering", 1.0 + hz["water"], 0.0, (0.2 + 0.4 * hz["water"] + 0.01 * N) * (1 - hz["electrical"]), measure="work",
            water=round(hz["water"], 2))

    # Taxonomy prior: the category's needs stand in for capabilities whose drivers nobody has observed.
    cat = b.category
    for cap, qty in cat.get("needs", {}).items():
        drivers = DRIVERS.get(cap, ())
        if drivers and any(_observed(b, d) for d in drivers):
            continue
        if cap in raw:
            continue
        if cap == "field_assessment" and assessing_drone:
            continue
        w = (cat["base_severity"] / 5.0) * (1.0 if cat["life_safety"] else 0.5) * p["category_prior_weight"]
        raw[cap] = (float(qty), 0.0, w, "units", {"category_need": qty})
        raw[cap][4]["source"] = "category"

    out = []
    for cap, (amount, margin, weight, measure, drivers) in sorted(raw.items()):
        src = drivers.pop("source", "belief")
        tau = drivers.pop("_tau", None) or p["tau_s"].get(cap, 1800)
        out.append(Demand(incident_id, cap, round(amount, 3), round(margin, 3), round(weight, 4),
                          tau * tau_f, measure, src, drivers))
    return out
