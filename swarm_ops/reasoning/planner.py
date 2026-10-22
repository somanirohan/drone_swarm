"""Onboard planner: belief -> needs -> optimised response -> explained ResponsePlan.

`Reasoner.plan_all(situations, context)` re-optimises every concurrent incident
together (shared units and drones); `plan(situation, context)` is the single-
incident view. A Reasoner keeps a little memory per incident (last escalation
level, committed units) for hysteresis and stand-down reporting; with a fresh
Reasoner the output is a pure function of the inputs.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from .belief import Belief
from .guardrails import check_plan
from .needs import infer_demands
from .optimizer import Unit, allocate
from .params import merged
from .taxonomy import DO_TO_SIM, load_taxonomy

CATALOG = Path(__file__).resolve().parents[2] / "assets" / "operations" / "agency-catalog.json"
LIFE_CAPS = ("medical_transport", "medical_care", "water_rescue", "search_rescue", "mass_transport", "fire_suppression",
             "traffic_control")
LEVELS = ("single_unit", "multi_unit", "full_team")
SPEED = {"helicopter": 50.0, "boat": 4.0}
CAP_LABEL = {"medical_transport": "casualties to transport", "medical_care": "casualties to treat on scene",
             "water_rescue": "people to extract from water", "search_rescue": "trapped people to extract",
             "mass_transport": "mobile people to evacuate", "fire_suppression": "fire-fighting work",
             "debris_clearance": "route clearance", "traffic_control": "cordon / scene control",
             "dewatering": "standing water", "water_supply": "people short of drinking water",
             "supply_delivery": "people short of food", "field_assessment": "unknown scene to assess"}


def escalation_level(R, prev, p):
    """Level from risk with hysteresis: escalate immediately (safety), step down only once R is
    clearly (escalation_hysteresis) below the boundary of the current level."""
    lo, hi = p["escalation_thresholds"]
    level = LEVELS[(R >= lo) + (R >= hi)]
    if prev in LEVELS and LEVELS.index(level) < LEVELS.index(prev):
        if R > (lo, hi)[LEVELS.index(prev) - 1] - p["escalation_hysteresis"]:
            return prev
    return level


def default_pool(tx, p):
    """Fleet when the caller gives none: unit counts and mean approach times from the PCMC agency catalog
    (DisruptionOps 029), plus police/helicopter from params['extra_pool']."""
    pool = {}
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf8"))
        for r in cat.get("resources", []):
            k = tx.kind(r.get("kind"))
            if k is None:
                continue
            e = pool.setdefault(k, {"available": 0, "_d": []})
            e["available"] += 1
            e["_d"].append(float(r.get("distance_from_anchor_m") or 0.0))
    except (OSError, ValueError):
        pass
    for k, e in pool.items():
        d = e.pop("_d")
        e["eta_s"] = round(sum(d) / len(d) * p["road_detour"] / p["ground_speed_mps"], 1) if d else p["default_unit_eta_s"]
    for k, e in p["extra_pool"].items():
        if tx.kind(k) and k not in pool:
            pool[k] = dict(e)
    if not pool:
        pool = {k: {"available": p["default_pool_per_kind"]} for k in tx.kinds}
    return dict(sorted(pool.items()))


def _dist(a, b):
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _eta(entry, iid, pos, speed, default, detour=1.0):
    e = entry.get("eta_s")
    if isinstance(e, dict) and iid in e:
        return float(e[iid])
    if isinstance(e, (int, float)) and not isinstance(e, bool):
        return float(e)
    if entry.get("position") is not None and pos is not None:
        return _dist(entry["position"], pos) * detour / speed + float(entry.get("approach_s", 0.0))
    return default


class Reasoner:
    def __init__(self, taxonomy=None):
        self.taxonomy = load_taxonomy(taxonomy)
        self.memory = {}

    # ------------------------------------------------------------ pieces ---
    def _units(self, context, ids, positions, beliefs, p):
        tx, out = self.taxonomy, []
        prev_units = {u: i for i, m in self.memory.items() for u in m.get("units", [])}
        if context.get("resources"):
            for r in context["resources"]:
                k = tx.kind(r.get("kind"))
                if k is None:
                    continue
                busy = r.get("state", "available") != "available" or r.get("available") is False
                prev = r.get("assigned_to") if r.get("assigned_to") in ids else prev_units.get(r.get("id"))
                if busy and prev is None:
                    continue
                speed = SPEED.get(k, p["ground_speed_mps"])
                eta = {i: _eta(r, i, positions.get(i), speed, p["default_unit_eta_s"], p["road_detour"]) for i in ids}
                out.append(Unit(str(r.get("id")), k, int(r.get("capacity") or tx.kinds[k]["default_capacity"]), eta,
                                previous=prev, label=r.get("name", "")))
        else:
            summary = context.get("resource_summary")
            if summary is None:
                summary = default_pool(tx, p)
            for kind, s in sorted(summary.items()):
                k = tx.kind(kind)
                if k is None:
                    continue
                prev_qty = {i: self._committed(i, context).get(k, self.memory.get(i, {}).get("kinds", {}).get(k, 0)) for i in ids}
                for n in range(int(s.get("available", 0))):
                    prev = None
                    for i in ids:
                        if prev_qty[i] > 0:
                            prev, prev_qty[i] = i, prev_qty[i] - 1
                            break
                    eta = {i: _eta(s, i, None, 1.0, p["default_unit_eta_s"]) for i in ids}
                    out.append(Unit(f"{k}#{n + 1}", k, int(s.get("capacity") or tx.kinds[k]["default_capacity"]), eta,
                                    virtual=True, previous=prev))
        for i in ids:   # responders already on scene contribute but are not requested
            for n, kind in enumerate(sorted(beliefs[i].responders)):
                k = tx.kind(kind)
                if k:
                    out.append(Unit(f"onscene:{i}:{k}:{n}", k, tx.kinds[k]["default_capacity"], {i: 0.0}, virtual=True, fixed=i))
        return out

    def _drones(self, context, ids, positions, p):
        ds = context.get("drones")
        if ds is None:
            pool = context.get("drone_pool") or {"available": 4, "stock": {"first_aid": 1, "food": 1, "medical": 1, "water": 1}, "eta_s": 120.0}
            ds, n = [], 0
            for payload, cnt in sorted((pool.get("stock") or {}).items()):
                for _ in range(int(cnt)):
                    n += 1
                    ds.append({"id": f"drone#{n}", "payload": payload, "stock": 1, "eta_s": pool.get("eta_s", 120.0)})
            while n < int(pool.get("available", n)):
                n += 1
                ds.append({"id": f"drone#{n}", "payload": "none", "stock": 0, "eta_s": pool.get("eta_s", 120.0)})
        out = []
        for d in sorted(ds, key=lambda d: str(d.get("id"))):
            if d.get("available") is False or d.get("link") is False:
                continue
            eta = {i: _eta(d, i, positions.get(i), p["drone_speed_mps"], 120.0) for i in ids}
            energy = float(d.get("energy_frac", 1.0))
            ok = {i: energy >= (2 * eta[i] + p["drone_onscene_s"]) / p["drone_endurance_s"] + p["drone_energy_reserve"] for i in ids}
            out.append({"id": str(d.get("id")), "payload": d.get("payload") or "none",
                        "stock": int(d.get("stock", 1 if d.get("payload") not in (None, "none") else 0)), "eta": eta, "ok": ok})
        return out

    def _risk(self, iid, belief, demands, p):
        L = sum(d.weight for d in demands if d.capability in LIFE_CAPS)
        Hmax = max(belief.hazard(h) for h in belief.hazards)
        tf = p["trend_risk_factor"][belief.trend_label]
        p_life = 1 - math.exp(-L)
        sev = max(1, min(5, 1 + round(4 * (1 - math.exp(-1.2 * (L + 0.4 * Hmax))) * min(tf, 1.15))))
        R = min(1.0, p_life * (0.5 + 0.1 * sev) * tf)
        level = escalation_level(R, self.memory.get(iid, {}).get("level"), p)
        return {"L": L, "R": R, "severity": sev, "level": level, "p_life": p_life, "trend_factor": tf, "Hmax": Hmax}

    # -------------------------------------------------------------- main ---
    def plan_all(self, situations, context=None):
        t0 = time.perf_counter()
        context = context or {}
        p = merged(context.get("params"))
        tx = self.taxonomy if context.get("taxonomy") is None else load_taxonomy(context["taxonomy"])
        ids, sits, beliefs, positions = [], {}, {}, {}
        for n, s in enumerate(situations):
            iid = str(s.get("incident_id") or s.get("id") or f"S{n + 1}")
            ids.append(iid)
            sits[iid] = s
            beliefs[iid] = Belief(s, tx, p)
            positions[iid] = s.get("position")
        drones = self._drones(context, ids, positions, p)
        caps = context.get("drone_caps") or {}
        per_inc = int(caps.get("per_incident", 3))
        total_cap = int(caps.get("available_total", len(drones)))
        # 1) provisional needs and risk; 2) value of information of an assessing drone.
        demands, risk, voi = {}, {}, {}
        for i in ids:
            b = beliefs[i]
            ds = infer_demands(i, b, tx, p)
            risk[i] = self._risk(i, b, ds, p)
            U = b.uncertainty()
            margin_val = sum(d.weight * d.margin / (d.amount + d.margin) for d in ds if d.amount + d.margin > 0)
            fa = sum(d.weight for d in ds if d.capability == "field_assessment")
            voi[i] = (1 - p["assess_sd_factor"]) * (margin_val + 0.5 * U * (0.2 + risk[i]["L"])) + 0.7 * fa
            assessing = voi[i] > p["drone_cost"] and any(d["ok"][i] for d in drones)
            demands[i] = infer_demands(i, b, tx, p, assessing_drone=assessing) if assessing else ds
            if assessing:   # a drone look arrives in minutes: hold less ground margin for today's uncertainty
                for d in demands[i]:
                    d.margin = round(d.margin * p["assess_sd_factor"], 3)
        # 3) ground units: one optimisation over all incidents.
        depth = {}
        for i in ids:
            b = beliefs[i]
            depth[i] = b.access["road_water_m"] if b.access["road_water_m"] is not None else b.hazard("water") * p["water_depth_full_m"]
        units = self._units(context, ids, positions, beliefs, p)
        cost_factor = {i: p["escalation_cost_factor"][risk[i]["level"]] for i in ids}
        alloc = allocate([d for i in ids for d in demands[i]], units, tx, p, depth, cost_factor, p.get("solver", "auto"))
        by_id = {u.id: u for u in units}
        # 4) drones: candidate actions valued per incident, assigned greedily to feasible aircraft.
        actions = []
        for i in ids:
            b, r = beliefs[i], risk[i]
            vm = 1 + p["vulnerability_weight"] * b.vulnerable_share()
            if voi[i] > p["drone_cost"]:
                actions.append((voi[i], i, "assess", "none", f"value of information {voi[i]:.2f}: uncertainty {b.uncertainty():.2f}"))
            assigned = [by_id[u] for u, ii in alloc.assignment.items() if ii == i] + [u for u in units if u.fixed == i]
            def ground_eta(capset):
                etas = [0.0 if u.fixed else u.eta.get(i, p["default_unit_eta_s"]) for u in assigned
                        if any(c in tx.kinds[u.kind]["capabilities"] for c in capset)]
                return min(etas) if etas else 7200.0
            I, C, N, M = b.mean("injured"), b.mean("critical"), b.mean("people"), b.mean("immobile")
            I, C = max(0.0, I - b.treated), max(0.0, C - min(b.treated, C))
            need = {"first_aid": (I, ground_eta({"medical_care", "medical_transport"})),
                    "medical": (C + N * b.shortages["medical"].mean * 0.3, ground_eta({"medical_care", "medical_transport"})),
                    "water": (N * b.shortages["water"].mean + M * max(b.hazard("water"), b.hazard("heat")),
                              ground_eta({"water_rescue", "search_rescue", "water_supply", "supply_delivery"})),
                    "food": (N * b.shortages["food"].mean + M * 0.5 * b.hazard("water"),
                             ground_eta({"water_rescue", "search_rescue", "supply_delivery"}))}
            for payload, (people, g_eta) in sorted(need.items()):
                kit = p["kit_people"][payload]
                left = people
                while left >= 0.5 and len([a for a in actions if a[1] == i]) < per_inc + 2:
                    served = min(kit, left)
                    d_eta = min((d["eta"][i] for d in drones if d["payload"] == payload and d["stock"] > 0), default=None)
                    if d_eta is None:
                        break
                    gain_h = max(0.0, min(g_eta, 7200.0) - d_eta) / 3600.0
                    val = served * p["drop_relief_per_h"][payload] * gain_h * vm
                    if val <= p["drone_cost"]:
                        break
                    actions.append((val, i, "supply", payload,
                                    f"{served:.0f} people need {payload}; drone arrives {gain_h * 60:.0f} min before ground help"))
                    left -= served
            ov = p["overwatch_value"] * r["L"] * b.p_worsening * r["Hmax"] * (1.5 if b.trend_label == "worsening" else 0.5)
            if ov > p["drone_cost"] * 3:
                actions.append((ov, i, "overwatch", "none", f"hazard {r['Hmax']:.2f}, P(worsening) {b.p_worsening:.2f}: keep eyes on scene"))
            if b.link < p["relay_link_threshold"]:
                actions.append(((p["relay_link_threshold"] - b.link) * (0.5 + r["L"]), i, "relay", "none",
                                f"link quality {b.link:.2f}: relay for the ground teams"))
        actions.sort(key=lambda a: (-a[0], a[1], a[2], a[3]))
        free = list(drones)
        used = {i: 0 for i in ids}
        dplan = {i: [] for i in ids}
        stock = {d["id"]: d["stock"] for d in drones}
        supply_need = {(a[1], a[3]) for a in actions if a[2] == "supply"}
        for val, i, role, payload, why in actions:
            if used[i] >= per_inc or sum(used.values()) >= total_cap:
                continue
            if role == "supply":
                cands = [d for d in free if d["ok"][i] and d["payload"] == payload and stock[d["id"]] > 0]
            else:   # prefer aircraft whose payload nobody needs here
                cands = sorted((d for d in free if d["ok"][i]), key=lambda d: ((i, d["payload"]) in supply_need, d["eta"][i], d["id"]))
            if not cands:
                continue
            d = min(cands, key=lambda d: (d["eta"][i], d["id"])) if role == "supply" else cands[0]
            free.remove(d)
            used[i] += 1
            dplan[i].append((role, payload if role == "supply" else "none", d["id"], why, val))
        # 5) assemble plans.
        plans = []
        for i in ids:
            plans.append(self._assemble(i, sits[i], beliefs[i], demands[i], risk[i], alloc, by_id, units, dplan[i], tx, p, context))
        ms = (time.perf_counter() - t0) * 1000
        for pl in plans:
            if pl is not None:
                pl["latency_ms"] = round(ms, 2)
        return plans

    def _committed(self, i, context, sit=None):
        """kind -> units already requested / assigned / en route for incident i (for idempotent deltas)."""
        tx, out = self.taxonomy, {}
        src = (sit or {}).get("committed")
        if src is None:
            src = (context.get("committed") or {}).get(i)
        if isinstance(src, dict):
            src = [{"kind": k, "qty": v} for k, v in src.items()]
        for e in src or []:
            k = tx.kind(e.get("kind") if isinstance(e, dict) else e)
            if k:
                out[k] = out.get(k, 0) + int(e.get("qty", 1) if isinstance(e, dict) else 1)
        if src is None:
            for r in context.get("resources") or []:
                if r.get("assigned_to") == i and tx.kind(r.get("kind")):
                    k = tx.kind(r["kind"])
                    out[k] = out.get(k, 0) + 1
        return out

    def _substitute_note(self, i, kind, cap, units, alloc, depth, tx, p):
        """Why a less effective kind was chosen for a capability (e.g. fire_engine for water rescue)."""
        provs = tx.providers.get(cap, {})
        eff = provs.get(kind, 0.0)
        better = sorted((k for k, e in provs.items() if e > eff), key=lambda k: (-provs[k], k))
        if not better:
            return ""
        notes = []
        for k in better:
            pool = [u for u in units if u.kind == k and not u.fixed]
            free = [u for u in pool if u.id not in alloc.assignment]
            if not pool:
                notes.append(f"no {k} in the fleet")
            elif not free:
                notes.append(f"all {k} committed elsewhere")
            elif k == "boat" and depth < p["boat_min_water_m"]:
                notes.append("boat cannot reach (too little water)")
            elif k != "helicopter" and depth > p["road_max_water_m"] and k != "boat":
                notes.append(f"{k} blocked by water on the road")
            else:
                best_eta = min(u.eta.get(i, p["default_unit_eta_s"]) for u in free)
                notes.append(f"{k} {best_eta / 60:.0f} min away / lower net value")
        return f" (substitute for {', '.join(better)}: {'; '.join(notes)})"

    def _assemble(self, i, sit, b, demands, r, alloc, by_id, units, dlist, tx, p, context):
        # Agency requests grouped by kind.
        groups = {}
        cap_need = {d.capability: d for d in demands}
        for uid, ii in sorted(alloc.assignment.items()):
            if ii != i:
                continue
            u = by_id[uid]
            parts = alloc.contributions.get(uid, [])
            g = groups.setdefault(u.kind, {"units": [], "parts": {}, "eta": [], "modes": set()})
            g["units"].append(u)
            g["eta"].append(u.eta.get(i, p["default_unit_eta_s"]))
            for cap, amt, mode in parts:
                g["parts"][cap] = g["parts"].get(cap, 0.0) + amt
                g["modes"].add((cap, mode))
        reqs = []
        for kind, g in sorted(groups.items()):
            cap = max(g["parts"], key=lambda c: (cap_need[c].weight * g["parts"][c] / max(cap_need[c].amount, 1e-6), c)) if g["parts"] \
                else max(tx.kinds[kind]["capabilities"], key=tx.kinds[kind]["capabilities"].get)
            d = cap_need.get(cap)
            mean_caps = {c for c, m in g["modes"] if m == "mean"}
            if not mean_caps:
                urgency = "standby"
            elif r["level"] == "full_team" or any(cap_need[c].weight >= 0.3 and cap_need[c].tau_s <= 1800 for c in mean_caps):
                urgency = "immediate"
            else:
                urgency = "soon"
            eff = tx.kinds[kind]["capabilities"].get(cap, 0)
            if d is not None and d.source == "category":
                why = f"DisruptionOps lists {cap} for {b.category['id']}; no evidence yet on its drivers"
            elif d is not None:
                why = (f"{CAP_LABEL.get(cap, cap)}: ~{d.amount:.1f}" + (f" (+{d.margin:.1f} margin)" if d.margin > 0.05 else "") +
                       f"; covers {g['parts'].get(cap, 0):.1f} at effectiveness {eff:.1f}, ETA {min(g['eta']) / 60:.0f} min")
            else:
                why = f"supports {', '.join(sorted(g['parts']))}"
            depth = b.access["road_water_m"] if b.access["road_water_m"] is not None else b.hazard("water") * p["water_depth_full_m"]
            why += self._substitute_note(i, kind, cap, units, alloc, depth, tx, p)
            others = sorted(c for c in g["parts"] if c != cap)
            if others:
                why += "; also " + ", ".join(others)
            reqs.append({"kind": kind, "qty": len(g["units"]), "urgency": urgency, "capability": cap, "why": why,
                         "unit_ids": [u.id for u in g["units"] if not u.virtual], "eta_s": round(min(g["eta"]), 1),
                         "sim_kind": DO_TO_SIM.get(kind, kind)})
        reqs.sort(key=lambda q: (("immediate", "soon", "standby").index(q["urgency"]), q["kind"]))
        # Drones grouped by role/payload.
        dgroups = {}
        for role, payload, did, why, val in dlist:
            g = dgroups.setdefault((role, payload), {"ids": [], "why": why, "val": 0.0})
            g["ids"].append(did)
            g["val"] += val
        drones = [{"role": role, "payload": payload, "count": len(g["ids"]), "why": g["why"], "drone_ids": g["ids"]}
                  for (role, payload), g in sorted(dgroups.items(), key=lambda kv: -kv[1]["val"])]
        # Explanations: contribution of each need to the expected harm at stake, plus multipliers.
        L = max(r["L"], 1e-9)
        factors = [{"factor": f"{CAP_LABEL.get(d.capability, d.capability)} ({d.capability})", "contribution": round(d.weight / L, 3),
                    "evidence": d.drivers, "source": d.source} for d in sorted(demands, key=lambda d: (-d.weight, d.capability))]
        vm = 1 + p["vulnerability_weight"] * b.vulnerable_share()
        mult = [{"factor": "vulnerable share", "multiplier": round(vm, 3)},
                {"factor": f"trend {b.trend_label}", "multiplier": r["trend_factor"]}]
        risks = []
        for d in sorted(demands, key=lambda d: -d.weight)[:4]:
            if d.weight >= 0.05:
                risks.append(f"{CAP_LABEL.get(d.capability, d.capability)}: {d.amount:.1f}")
        for h, g in sorted(b.hazards.items(), key=lambda kv: -kv[1].mean):
            if g.mean >= 0.4:
                risks.append(f"{h} hazard {g.mean:.2f}")
        unknowns = []
        for name, label in (("people", "how many people"), ("injured", "injuries"), ("critical", "critical casualties"),
                            ("immobile", "who cannot self-evacuate")):
            if not b.observed(name) and (b.sd(name) / (b.mean(name) + 1.5) > 0.3 or name == "people"):
                unknowns.append(label)
        if b.category["id"] == "unknown_report":
            unknowns.append("what the incident is")
        if b.hazard("electrical") >= 0.4:
            unknowns.append("power isolation (no utility kind in the DisruptionOps taxonomy)")
        U = b.uncertainty()
        reassess = max(p["reassess_min_s"], p["reassess_base_s"] * (1 - 0.6 * U) *
                       {"worsening": 0.5, "stable": 1.0, "improving": 1.5}[b.trend_label])
        # Idempotent deltas: qty is the total the incident should have; delta is net of what is already
        # requested / assigned / en route (from the caller, else this reasoner's last plan). Negative -> stand down.
        committed = self._committed(i, context, sit)
        if not committed and sit.get("committed") is None and not (context.get("committed") or {}).get(i):
            committed = dict(self.memory.get(i, {}).get("kinds", {}))
        kinds_now = {q["kind"]: q["qty"] for q in reqs}
        for q in reqs:
            q["already"] = committed.get(q["kind"], 0)
            q["delta"] = q["qty"] - q["already"]
        stand_down = [{"kind": k, "qty": q - kinds_now.get(k, 0), "why": "belief improved / need covered"}
                      for k, q in sorted(committed.items()) if q > kinds_now.get(k, 0)]
        top = factors[0]["factor"] if factors else "no identified need"
        plan = {
            "incident_id": i,
            "assessment": {"severity": r["severity"], "trend": b.trend_label, "key_risks": risks, "unknowns": unknowns},
            "drones": drones,
            "agency_requests": reqs,
            "public_actions": _public_actions(b, sit),
            "escalation": r["level"],
            "reassess_after_s": round(reassess, 1),
            "confidence": round(1 - U, 3),
            "rationale": (f"{b.category['id']}: ~{b.mean('people'):.0f} people, {b.mean('injured'):.1f} injured "
                          f"({b.mean('critical'):.1f} critical), {b.mean('immobile'):.1f} cannot self-evacuate; trend {b.trend_label}; "
                          f"risk {r['R']:.2f} -> {r['level']}; main driver: {top}."),
            "stand_down": stand_down,
            "requests_delta": [{"kind": q["kind"], "delta": q["delta"], "urgency": q["urgency"]} for q in reqs if q["delta"] > 0] +
                              [{"kind": s["kind"], "delta": -s["qty"], "urgency": "standby"} for s in stand_down],
            "explanation": {"risk": round(r["R"], 3), "harm_at_stake": round(r["L"], 3), "p_life_threat": round(r["p_life"], 3),
                            "factors": factors[:6], "multipliers": mult, "solver": alloc.solver},
            "belief": b.summary(),
            "engine": "onboard-v1",
            "fallback": False,
        }
        plan, violations = check_plan(plan, context, tx, p, b)
        if plan is None:
            return None
        plan["guardrail_violations"] = violations
        self.memory[i] = {"level": r["level"], "R": r["R"], "kinds": {q["kind"]: q["qty"] for q in plan["agency_requests"]},
                          "units": [u for q in plan["agency_requests"] for u in q.get("unit_ids", [])]}
        return plan

    def plan(self, situation, context=None):
        context = context or {}
        others = list(context.get("concurrent") or [])
        return self.plan_all([situation] + others, context)[0]

    def forget(self, incident_id):
        self.memory.pop(incident_id, None)


def _public_actions(b, sit):
    out = []
    if b.hazard("gas") >= 0.4:
        out.append("Keep the public 200 m away and upwind; no ignition sources (gas/chemical).")
    if b.hazard("fire") >= 0.4:
        out.append("Clear adjacent buildings and keep the access road open for engines.")
    if b.hazard("electrical") >= 0.4:
        out.append("Cordon the area around the live wire; nobody enters standing water; ask the utility to isolate supply.")
    if b.hazard("water") >= 0.4 and b.mean("immobile") >= 0.5:
        out.append("Tell people to move to the highest floor or terrace and signal; do not wade.")
    if b.hazard("structural") >= 0.4:
        out.append("Keep bystanders clear of the collapse zone; no unqualified digging.")
    if b.access["blocks_emergency_route"] or (b.hazard("debris") >= 0.4 and b.access["road_blocked"]):
        out.append("Divert ambulances and traffic to the alternate route until the road is cleared.")
    if b.hazard("crowding") >= 0.4:
        names = [l.get("name") for l in (sit.get("nearby_lifelines") or []) if l.get("kind") in ("shelter", "relief_centre", "hotel")]
        out.append("Direct new arrivals to the next shelter with capacity" + (f" ({names[0]})." if names else "."))
    if b.shortages["water"].mean >= 0.4:
        out.append("Ration remaining drinking water to children, elderly and the sick first.")
    return out
