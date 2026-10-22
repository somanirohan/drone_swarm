"""Action guardrails: invariant checks on any ResponsePlan before the engine applies it.

The planner is designed to satisfy these already; the guardrails are defence in
depth (and the gate for plans that come from anywhere else). They repair what can
be repaired (clamp quantities to availability, drop unknown kinds, trim drones to
caps and payload stock) and record every violation; a plan that is unsafe after
repair (life threat with no response at all, or schema-invalid) is rejected.
"""
from __future__ import annotations

import copy

from .schema import validate_plan


def availability(context, taxonomy):
    """kind -> available count from context (None if the context does not say)."""
    if not context:
        return None
    if context.get("resources"):
        out = {}
        for r in context["resources"]:
            k = taxonomy.kind(r.get("kind"))
            if k and r.get("available", True) and r.get("state", "available") == "available":
                out[k] = out.get(k, 0) + 1
            elif k and r.get("assigned_to"):
                out[k] = out.get(k, 0) + 1   # committed units can be retained
        return out
    if context.get("resource_summary"):
        return {taxonomy.kind(k): int(v.get("available", 0)) for k, v in context["resource_summary"].items() if taxonomy.kind(k)}
    return None


def check_plan(plan, context, taxonomy, p, belief=None):
    """-> (repaired_plan | None, violations list)."""
    v = []
    if not isinstance(plan, dict):
        return None, ["plan is not an object"]
    plan = copy.deepcopy(plan)
    kinds, caps = set(taxonomy.kinds), set(taxonomy.capabilities) | {c for k in taxonomy.kinds.values() for c in k["capabilities"]}
    reqs = []
    for r in plan.get("agency_requests") or []:
        k = taxonomy.kind(r.get("kind")) if isinstance(r, dict) else None
        if k is None:
            v.append(f"dropped request for unknown kind {r.get('kind') if isinstance(r, dict) else r!r}")
            continue
        r["kind"] = k
        if r.get("capability") not in taxonomy.kinds[k]["capabilities"]:
            best = max(taxonomy.kinds[k]["capabilities"], key=taxonomy.kinds[k]["capabilities"].get, default=None)
            v.append(f"{k}: capability {r.get('capability')} not provided by this kind; set to {best}")
            r["capability"] = best
        if belief is not None and k == "boat":
            depth = belief.access["road_water_m"] if belief.access["road_water_m"] is not None else belief.hazard("water") * p["water_depth_full_m"]
            if depth < p["boat_min_water_m"]:
                v.append("dropped boat: no water at the scene")
                continue
        reqs.append(r)
    avail = availability(context, taxonomy)
    if avail is not None:
        for r in reqs:
            have = avail.get(r["kind"], 0)
            if r.get("qty", 0) > have:
                v.append(f"{r['kind']}: qty {r.get('qty')} > {have} available; clamped")
                r["qty"] = have
        reqs = [r for r in reqs if r.get("qty", 0) >= 1]
    plan["agency_requests"] = reqs
    # Drones: per-incident cap, available aircraft, payload stock.
    ctx = context or {}
    caps_ctx = ctx.get("drone_caps") or {}
    cap = caps_ctx.get("per_incident")
    stock = {}
    for d in ctx.get("drones") or []:
        if d.get("payload") and d.get("stock", 1) > 0:
            stock[d["payload"]] = stock.get(d["payload"], 0) + 1
    if ctx.get("drones") is not None:
        n_avail = len(ctx["drones"])
        cap = n_avail if cap is None else min(cap, n_avail)
    used, drones = 0, []
    for d in plan.get("drones") or []:
        if not isinstance(d, dict):
            continue
        if d.get("payload") not in (None, "none") and ctx.get("drones") is not None:
            have = stock.get(d["payload"], 0)
            if d.get("count", 0) > have:
                v.append(f"{d['payload']} drops: {d.get('count')} > {have} stocked aircraft; clamped")
                d["count"] = have
            stock[d["payload"]] = have - d.get("count", 0)
        if cap is not None and used + d.get("count", 0) > cap:
            v.append(f"drone cap {cap}: trimmed {d.get('role')}")
            d["count"] = max(0, cap - used)
        used += d.get("count", 0)
        if d.get("count", 0) >= 1:
            if d.get("drone_ids"):
                d["drone_ids"] = d["drone_ids"][:d["count"]]
            drones.append(d)
    plan["drones"] = drones
    if isinstance(plan.get("confidence"), (int, float)):
        plan["confidence"] = min(1.0, max(0.0, float(plan["confidence"])))
    if isinstance(plan.get("reassess_after_s"), (int, float)):
        plan["reassess_after_s"] = min(3600.0, max(p["reassess_min_s"], float(plan["reassess_after_s"])))
    errs = validate_plan(plan, kinds, caps)
    if errs:
        return None, v + errs
    life = belief is not None and (belief.mean("critical") >= 0.5 or belief.mean("immobile") >= 0.5)
    if (life or plan["assessment"]["severity"] >= 4) and not plan["agency_requests"] and not plan["drones"]:
        if avail is None or any(avail.values()):
            return None, v + ["unsafe: life threat with no response"]
    return plan, v
