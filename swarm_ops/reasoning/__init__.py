"""Onboard decision engine for the swarm agency (no LLM, no network, deterministic).

    from swarm_ops.reasoning import plan_response
    plan = plan_response(situation, context)      # dict (ResponsePlan) or None

Pipeline: belief.py (evidence fusion) -> needs.py (taxonomy-driven demands) ->
optimizer.py (CP-SAT / greedy allocation) -> planner.py (drones, escalation,
explanations) -> guardrails.py (invariants). See docs/REASONING.md.
"""
from __future__ import annotations

import logging

from .guardrails import check_plan
from .planner import Reasoner
from .schema import plan_schema, validate_plan
from .taxonomy import DO_TO_SIM, SIM_TO_DO, load_taxonomy

log = logging.getLogger(__name__)
_DEFAULT = None


def default_reasoner():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Reasoner()
    return _DEFAULT


def plan_response(situation, context=None, reasoner=None):
    """Plan the response to one incident (re-optimised jointly with context['concurrent'] situations).

    Synchronous and fast (typically a few ms; < 50 ms). Returns None only if the plan
    fails the guardrails or the inputs are unusable; the caller then uses its labelled
    deterministic fallback.
    """
    try:
        return (reasoner or default_reasoner()).plan(situation or {}, context or {})
    except Exception:  # never take the simulation down
        log.exception("reasoning: plan_response failed; caller should use its fallback")
        return None


def plan_all(situations, context=None, reasoner=None):
    """Joint plan for several concurrent incidents (list aligned with input; None entries on rejection)."""
    try:
        return (reasoner or default_reasoner()).plan_all(list(situations), context or {})
    except Exception:
        log.exception("reasoning: plan_all failed")
        return [None] * len(list(situations))


__all__ = ["plan_response", "plan_all", "Reasoner", "default_reasoner", "check_plan", "validate_plan",
           "plan_schema", "load_taxonomy", "DO_TO_SIM", "SIM_TO_DO"]
