"""ResponsePlan: the contract between the onboard planner, guardrails and the engine.

A strict JSON schema (for docs/clients) plus a dependency-free validator.
"""
from __future__ import annotations

ROLES = ("assess", "relay", "overwatch", "supply")
PAYLOADS = ("none", "food", "water", "first_aid", "medical")
URGENCY = ("immediate", "soon", "standby")
ESCALATION = ("single_unit", "multi_unit", "full_team")
TRENDS = ("improving", "stable", "worsening")


def plan_schema(kinds=None, capabilities=None):
    kind = {"type": "string", **({"enum": sorted(kinds)} if kinds else {})}
    cap = {"type": "string", **({"enum": sorted(capabilities)} if capabilities else {})}
    obj = lambda props, req=None: {"type": "object", "properties": props, "required": req or list(props), "additionalProperties": False}
    return obj({
        "assessment": obj({"severity": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                           "trend": {"type": "string", "enum": list(TRENDS)},
                           "key_risks": {"type": "array", "items": {"type": "string"}},
                           "unknowns": {"type": "array", "items": {"type": "string"}}}),
        "drones": {"type": "array", "items": obj({"role": {"type": "string", "enum": list(ROLES)},
                                                  "payload": {"type": "string", "enum": list(PAYLOADS)},
                                                  "count": {"type": "integer"}, "why": {"type": "string"},
                                                  "drone_ids": {"type": "array", "items": {"type": "string"}}},
                                                 ["role", "payload", "count", "why"])},
        "agency_requests": {"type": "array", "items": obj({"kind": kind, "qty": {"type": "integer"},
                                                           "urgency": {"type": "string", "enum": list(URGENCY)},
                                                           "capability": cap, "why": {"type": "string"},
                                                           "unit_ids": {"type": "array", "items": {"type": "string"}},
                                                           "eta_s": {"type": ["number", "null"]}},
                                                          ["kind", "qty", "urgency", "capability", "why"])},
        "public_actions": {"type": "array", "items": {"type": "string"}},
        "escalation": {"type": "string", "enum": list(ESCALATION)},
        "reassess_after_s": {"type": "number"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    }, ["assessment", "drones", "agency_requests", "public_actions", "escalation", "reassess_after_s", "confidence", "rationale"])


def validate_plan(plan, kinds=None, capabilities=None):
    """List of schema errors (empty = valid). Extra top-level keys (explanation, belief, ...) are allowed."""
    errs = []
    if not isinstance(plan, dict):
        return ["plan is not an object"]
    a = plan.get("assessment")
    if not isinstance(a, dict):
        errs.append("assessment missing")
    else:
        if a.get("severity") not in (1, 2, 3, 4, 5):
            errs.append("assessment.severity must be 1-5")
        if a.get("trend") not in TRENDS:
            errs.append("assessment.trend invalid")
        for k in ("key_risks", "unknowns"):
            if not isinstance(a.get(k), list):
                errs.append(f"assessment.{k} must be a list")
    for n, d in enumerate(plan.get("drones") if isinstance(plan.get("drones"), list) else [None]):
        if not isinstance(d, dict):
            errs.append("drones must be a list of objects")
            break
        if d.get("role") not in ROLES:
            errs.append(f"drones[{n}].role invalid")
        if d.get("payload") not in PAYLOADS:
            errs.append(f"drones[{n}].payload invalid")
        if not isinstance(d.get("count"), int) or isinstance(d.get("count"), bool) or d["count"] < 1:
            errs.append(f"drones[{n}].count must be a positive integer")
    for n, r in enumerate(plan.get("agency_requests") if isinstance(plan.get("agency_requests"), list) else [None]):
        if not isinstance(r, dict):
            errs.append("agency_requests must be a list of objects")
            break
        if kinds is not None and r.get("kind") not in kinds:
            errs.append(f"agency_requests[{n}].kind '{r.get('kind')}' is not a DisruptionOps resource kind")
        if not isinstance(r.get("qty"), int) or isinstance(r.get("qty"), bool) or r["qty"] < 1:
            errs.append(f"agency_requests[{n}].qty must be a positive integer")
        if r.get("urgency") not in URGENCY:
            errs.append(f"agency_requests[{n}].urgency invalid")
        if capabilities is not None and r.get("capability") not in capabilities:
            errs.append(f"agency_requests[{n}].capability invalid")
    if not isinstance(plan.get("public_actions"), list):
        errs.append("public_actions must be a list")
    if plan.get("escalation") not in ESCALATION:
        errs.append("escalation invalid")
    for k in ("reassess_after_s", "confidence"):
        if not isinstance(plan.get(k), (int, float)) or isinstance(plan.get(k), bool):
            errs.append(f"{k} must be a number")
    if isinstance(plan.get("confidence"), (int, float)) and not 0 <= plan["confidence"] <= 1:
        errs.append("confidence must be within 0-1")
    if not isinstance(plan.get("rationale"), str):
        errs.append("rationale must be a string")
    return errs
