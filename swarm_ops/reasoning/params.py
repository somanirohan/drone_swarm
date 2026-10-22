"""Every fixed parameter of the onboard decision engine, in one place.

Nothing here is a per-category rule ("flood -> boat"). These are the constants
of the *models*: sensor noise, priors, harm rates, time constants and cost
weights. Which units a situation gets is decided by the belief, the DisruptionOps
taxonomy data and the optimiser; these numbers only set the scales. They are
listed honestly in docs/REASONING.md and can be overridden per call with
`context["params"]` (a shallow dict merge) for tuning or sensitivity tests.
"""

PARAMS = {
    # ------------------------------------------------------------ belief ---
    # Prior for the number of people at a report that says nothing (mean, sd).
    "people_prior": (3.0, 1.2),           # mean people, sd in log(1+n) units
    # Population base rates used only until evidence arrives.
    "injured_share_prior": {"life_safety": 0.08, "other": 0.005},
    "derived_count_sd_log": 1.0,         # prior spread of injured/critical/vulnerable/immobile (log units)
    "critical_share_of_injured": 0.25,
    "vulnerable_share_prior": 0.20,      # elderly + children + disabled + pregnant
    # Share of people who cannot leave on their own, per unit hazard intensity.
    "immobile_per_hazard": {"water": 0.85, "structural": 0.6, "fire": 0.35},
    # Default source confidence when a message does not state one.
    "source_confidence": {"report": 0.5, "orchestrator": 0.55, "drone": 0.8, "ground": 0.92, "text": 0.4},
    # Count observation noise in log(1+n) units: sd = (1 - confidence) * k + floor.
    "count_noise_k": 0.9,
    "count_noise_floor": 0.08,
    # Hazard intensity observation noise sd = (1 - confidence) * k + floor.
    "hazard_noise_k": 0.6,
    "hazard_noise_floor": 0.05,
    # Random-walk process noise per second (state drifts between observations).
    "people_drift_per_s": 0.00005,       # log-variance growth per s (people move)
    "hazard_drift_per_s": 0.000005,
    "rate_drift_per_s": 1e-9,            # hazard rate (per s) random walk
    # A reported "worsening" / "improving" is evidence of this hazard rate (per s).
    "trend_word_rate": 0.0006,
    # Trend classification: |rate| * horizon must exceed this many sd and this size.
    "trend_horizon_s": 900.0,
    "trend_min_change": 0.08,
    "trend_z": 1.0,
    # Water depth (m) that counts as intensity 1.0.
    "water_depth_full_m": 1.5,

    # ------------------------------------------------------- needs model ---
    # Expected serious harm per person exposed if nothing is done (people-equivalents).
    "harm": {
        "critical": 1.0, "injured": 0.2, "stranded_water": 0.35, "trapped": 0.6,
        "fire_exposed": 0.3, "gas_exposed": 0.25, "electrical_exposed": 0.15,
        "crowd_crush": 0.02, "no_water_per_day": 0.06, "no_food_per_day": 0.02,
        "evacuation": 0.03,
    },
    # Vulnerable people weigh more (elderly, children, disabled): multiplier per share.
    "vulnerability_weight": 0.8,
    # Time constants (s): value of serving a need decays as exp(-eta / tau).
    "tau_s": {
        "medical_transport": 1500, "medical_care": 900, "water_rescue": 1500, "search_rescue": 2400,
        "fire_suppression": 900, "mass_transport": 3600, "supply_delivery": 7200, "water_supply": 5400,
        "dewatering": 5400, "debris_clearance": 3600, "traffic_control": 1800, "field_assessment": 1800,
    },
    # Worsening trend shortens tau by this factor, improving lengthens it.
    "trend_tau_factor": {"worsening": 0.6, "stable": 1.0, "improving": 1.5},
    # People-measured capabilities use seat/patient capacity; others are "work units".
    "people_capabilities": ["water_rescue", "search_rescue", "medical_transport", "medical_care", "mass_transport"],
    # Patients one crew can treat on scene (medical_care), per unit.
    "care_per_unit": 3,
    # Seats when a goods vehicle carries people (taxonomy capacity is goods for these kinds).
    "people_capacity": {"supply_truck": 20, "water_tanker": 0},
    # Supply capacity is per person served: supply_truck capacity / ration size; tanker litres / litres per person.
    "litres_per_person": 15.0,
    "ration_people_per_capacity": 1.0,
    # Risk margin: cover mean + z * sd of the demand (uncertainty -> standby units).
    "margin_z": 1.0,
    # Category prior (taxonomy incident_category_needs) used when no evidence speaks to its driver.
    "category_prior_weight": 0.6,
    # Access: road units lose effectiveness in deep water; boats need water.
    "road_max_water_m": 0.6,
    "boat_min_water_m": 0.3,

    # -------------------------------------------------------- optimiser ---
    # Cost of committing one ground unit, in people-equivalents of harm.
    "unit_cost": 0.12,
    # Extra opportunity cost when a kind is scarce: cost += k / remaining_units.
    "scarcity_cost": 0.12,
    # Value of covering demand beyond the mean (the risk margin) relative to the mean.
    "margin_value": 0.15,                 # ~ P(demand beyond +1 sd)
    # Re-plans keep already-committed units unless the gain flips by this much (hysteresis).
    "retention_bonus": 0.5,
    # Commitment cost multiplier by escalation level (higher risk -> commit more readily).
    "escalation_cost_factor": {"single_unit": 1.0, "multi_unit": 0.8, "full_team": 0.6},
    "solver": "greedy",                # greedy (onboard, ~ms) | cpsat (exact cross-check, slower)
    "cpsat_det_time": 2.0,               # CP-SAT deterministic-time budget (not wall clock)
    "default_unit_eta_s": 900.0,
    "default_pool_per_kind": 3,           # only for kinds absent from the fleet catalog
    # Kinds not in the PCMC catalog: police (DisruptionOps 032 pcpolice), helicopter (035 aid offer, 90 min response).
    "extra_pool": {"police": {"available": 2, "eta_s": 600.0}, "helicopter": {"available": 1, "eta_s": 5400.0}},
    "ground_speed_mps": 9.0,
    "road_detour": 1.4,

    # ----------------------------------------------------------- drones ---
    "drone_cost": 0.01,
    "drone_speed_mps": 18.0,
    "drone_endurance_s": 1800.0,
    "drone_energy_reserve": 0.2,
    "drone_onscene_s": 240.0,
    # A drone look reduces count uncertainty to this fraction of its sd.
    "assess_sd_factor": 0.35,
    # People one payload unit serves.
    "kit_people": {"first_aid": 4, "medical": 1, "water": 6, "food": 6},
    # Harm a drop relieves per served person per hour of earlier arrival.
    "drop_relief_per_h": {"first_aid": 0.08, "medical": 0.25, "water": 0.04, "food": 0.01},
    "overwatch_value": 0.25,             # x risk x hazard volatility
    "relay_link_threshold": 0.5,

    # ------------------------------------------------------- escalation ---
    "escalation_thresholds": (0.30, 0.65),   # single|multi, multi|full
    "escalation_hysteresis": 0.07,
    "trend_risk_factor": {"worsening": 1.3, "stable": 1.0, "improving": 0.75},
    "reassess_base_s": 300.0,
    "reassess_min_s": 45.0,
}


def merged(overrides=None):
    if not overrides:
        return PARAMS
    out = dict(PARAMS)
    out.update(overrides)
    return out
