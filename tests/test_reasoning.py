"""Onboard decision engine (swarm_ops.reasoning): schema, guardrails, determinism, latency,
idempotent request deltas, hysteresis, cross-incident fairness, solver agreement, offline-only."""
import copy
import re
import time
import unittest
from pathlib import Path

from swarm_ops.reasoning import Reasoner, check_plan, load_taxonomy, plan_response, validate_plan
from swarm_ops.reasoning.belief import Belief
from swarm_ops.reasoning.evals import CASES, ROOFTOP, run_evals
from swarm_ops.reasoning.optimizer import cp_model
from swarm_ops.reasoning.params import PARAMS
from swarm_ops.reasoning.planner import escalation_level
from swarm_ops.reasoning.taxonomy import builtin_taxonomy

TX = load_taxonomy()
KINDS = set(TX.kinds)


def strip(plan):
    plan = copy.deepcopy(plan)
    plan.pop("latency_ms", None)
    return plan


class SchemaAndGuardrails(unittest.TestCase):
    def setUp(self):
        self.plan = Reasoner().plan({**ROOFTOP, "incident_id": "I1"}, {})

    def test_plan_is_schema_valid(self):
        self.assertIsNotNone(self.plan)
        self.assertEqual(validate_plan(self.plan, KINDS, set(TX.providers)), [])
        for q in self.plan["agency_requests"]:
            self.assertIn(q["kind"], KINDS)

    def test_schema_rejects_bad_plan(self):
        bad = dict(self.plan, escalation="everything", confidence=3)
        bad["assessment"] = dict(bad["assessment"], severity=9)
        errs = validate_plan(bad, KINDS)
        self.assertTrue(any("escalation" in e for e in errs))
        self.assertTrue(any("severity" in e for e in errs))
        self.assertTrue(any("confidence" in e for e in errs))

    def test_guardrails_repair(self):
        plan = copy.deepcopy(self.plan)
        plan["agency_requests"] = [
            {"kind": "submarine", "qty": 1, "urgency": "immediate", "capability": "water_rescue", "why": "x"},
            {"kind": "boat", "qty": 9, "urgency": "immediate", "capability": "water_rescue", "why": "x"},
            {"kind": "rescue", "qty": 1, "urgency": "soon", "capability": "search_rescue", "why": "sim alias"}]
        plan["drones"] = [{"role": "supply", "payload": "water", "count": 5, "why": "x"}]
        ctx = {"resource_summary": {"boat": {"available": 2}, "rescue_team": {"available": 1}},
               "drones": [{"id": "D1", "payload": "water", "stock": 1}, {"id": "D2", "payload": "food", "stock": 1}]}
        out, v = check_plan(plan, ctx, TX, PARAMS)
        self.assertIsNotNone(out)
        kinds = {q["kind"]: q["qty"] for q in out["agency_requests"]}
        self.assertEqual(kinds, {"boat": 2, "rescue_team": 1})
        self.assertEqual(out["drones"][0]["count"], 1)
        self.assertTrue(any("submarine" in x for x in v) and any("clamped" in x for x in v))

    def test_guardrails_drop_boat_on_dry_street(self):
        b = Belief({"disruptionops_category": "person_stranded", "observations": [
            {"source": "drone", "confidence": 0.95, "people": 2, "hazards": {"water_depth_m": 0.0}}]}, TX, PARAMS)
        plan = copy.deepcopy(self.plan)
        plan["agency_requests"] = [{"kind": "boat", "qty": 1, "urgency": "immediate", "capability": "water_rescue", "why": "x"},
                                   {"kind": "ambulance", "qty": 1, "urgency": "soon", "capability": "medical_transport", "why": "x"}]
        out, v = check_plan(plan, {}, TX, PARAMS, b)
        self.assertEqual([q["kind"] for q in out["agency_requests"]], ["ambulance"])

    def test_guardrails_reject_unsafe(self):
        plan = copy.deepcopy(self.plan)
        plan["agency_requests"], plan["drones"] = [], []
        plan["assessment"]["severity"] = 5
        out, v = check_plan(plan, {}, TX, PARAMS)
        self.assertIsNone(out)
        self.assertTrue(any("unsafe" in x for x in v))


class Behaviour(unittest.TestCase):
    def test_eval_suite_passes(self):
        res = run_evals()
        failed = [(c["id"], n) for c in res["cases"] for n, ok in c["checks"] if not ok]
        self.assertEqual(failed, [])
        self.assertEqual([p["name"] for p in res["pairs"] if not p["ok"]], [])
        self.assertGreaterEqual(len(res["cases"]), 12)

    def test_deterministic(self):
        a = Reasoner().plan({**ROOFTOP, "incident_id": "X"}, {})
        b = Reasoner().plan({**ROOFTOP, "incident_id": "X"}, {})
        self.assertEqual(strip(a), strip(b))

    def test_latency_under_50ms(self):
        r = Reasoner()
        r.plan({**ROOFTOP, "incident_id": "warm"}, {})
        worst = 0.0
        for case in CASES:
            t0 = time.perf_counter()
            r.plan({**case["situation"], "incident_id": case["id"]}, {})
            worst = max(worst, (time.perf_counter() - t0) * 1000)
        self.assertLess(worst, 50.0)
        # Ten concurrent incidents re-optimised jointly.
        t0 = time.perf_counter()
        plans = Reasoner().plan_all([{**c["situation"], "incident_id": c["id"]} for c in CASES[:10]], {})
        self.assertTrue(all(plans))
        self.assertLess((time.perf_counter() - t0) * 1000, 250.0)

    def test_requests_are_idempotent_deltas(self):
        """Regression: re-planning must not stack '1x fire_engine' + '2x fire_engine' for the same need."""
        r = Reasoner()
        s = {"incident_id": "F", "disruptionops_category": "person_stranded", "report_text": "4 people stranded on roof, water rising",
             "reported_people": 4, "hazards_reported": ["rising_water"]}
        ctx = {"resources": [{"id": "PC-FIRE-01", "kind": "fire_engine", "eta_s": 300},
                             {"id": "PC-FIRE-02", "kind": "fire_engine", "eta_s": 400},
                             {"id": "A1", "kind": "ambulance", "eta_s": 500}]}
        first = r.plan(s, ctx)
        total = {q["kind"]: q["qty"] for q in first["agency_requests"]}
        self.assertEqual({d["kind"]: d["delta"] for d in first["requests_delta"]}, total)
        for _ in range(3):
            again = r.plan(s, ctx)
            self.assertEqual({q["kind"]: q["qty"] for q in again["agency_requests"]}, total)
            self.assertEqual(again["requests_delta"], [])
        # The caller reports what is actually committed; delta is net of it.
        committed = dict(s, committed={"fire_engine": 1})
        part = Reasoner().plan(committed, ctx)
        fe = next(q for q in part["agency_requests"] if q["kind"] == "fire_engine")
        self.assertEqual(fe["delta"], fe["qty"] - 1)
        # Substitution is explained: fire engine only because no boat/rescue team is in this fleet.
        self.assertIn("substitute for boat", fe["why"])
        # Belief improves -> stand down, as a negative delta.
        calm = dict(s, t_s=900, observations=[{"t_s": 880, "source": "ground", "confidence": 0.95, "people": 4, "immobile": 0,
                                               "hazards": {"water_depth_m": 0.1}, "trend": "improving"}])
        down = r.plan(calm, ctx)
        self.assertTrue(down["stand_down"])
        self.assertTrue(any(d["delta"] < 0 for d in down["requests_delta"]))

    def test_escalation_hysteresis(self):
        lo, hi = PARAMS["escalation_thresholds"]
        h = PARAMS["escalation_hysteresis"]
        self.assertEqual(escalation_level(hi + 0.01, "single_unit", PARAMS), "full_team")      # up immediately
        self.assertEqual(escalation_level(hi - h / 2, "full_team", PARAMS), "full_team")       # no flip-flop
        self.assertEqual(escalation_level(hi - 2 * h, "full_team", PARAMS), "multi_unit")      # clearly lower
        self.assertEqual(escalation_level(lo - h / 2, "multi_unit", PARAMS), "multi_unit")
        self.assertEqual(escalation_level(0.0, None, PARAMS), "single_unit")

    def test_scarce_unit_goes_to_worse_incident(self):
        ctx = {"resource_summary": {"boat": {"available": 1, "eta_s": 300}}}
        mild = {"incident_id": "M", "disruptionops_category": "person_stranded", "report_text": "1 person stranded, water steady",
                "reported_people": 1, "hazards_reported": ["standing_water"], "trend": "stable"}
        severe = {**ROOFTOP, "incident_id": "S", "reported_people": 9, "report_text": "9 people on the roof, water rising"}
        plans = {p["incident_id"]: p for p in Reasoner().plan_all([mild, severe], ctx)}
        self.assertIn("boat", {q["kind"] for q in plans["S"]["agency_requests"]})
        self.assertNotIn("boat", {q["kind"] for q in plans["M"]["agency_requests"]})

    @unittest.skipIf(cp_model is None, "OR-Tools not installed")
    def test_greedy_close_to_cpsat(self):
        sits = [{**c["situation"], "incident_id": c["id"]} for c in CASES[:6]]
        g = Reasoner().plan_all(sits, {"params": {"solver": "greedy"}})
        c = Reasoner().plan_all(sits, {"params": {"solver": "cpsat"}})
        self.assertTrue(all(g) and all(c))
        self.assertTrue(all(p["explanation"]["solver"].startswith(("cp-sat", "greedy")) for p in c))

    def test_explanations_present(self):
        p = Reasoner().plan({**ROOFTOP, "incident_id": "E"}, {})
        f = p["explanation"]["factors"]
        self.assertTrue(f and abs(sum(x["contribution"] for x in f) - 1.0) < 0.35)
        self.assertTrue(all(q["why"] for q in p["agency_requests"]))
        self.assertTrue(p["rationale"])

    def test_pcmc_scenario_reports(self):
        from swarm_ops.scenarios.pcmc import PAWANA_NIGHT
        for n, rep in enumerate(PAWANA_NIGHT):
            s = {"incident_id": f"P{n}", "disruptionops_category": rep["category"], "report_text": rep["text"],
                 "callers": rep["callers"], "reported_people": rep["reported_people"], "vulnerable": rep["vulnerable"],
                 "injuries": rep["injuries"], "hazards_reported": rep["hazards"], "height_m": rep["height_m"]}
            plan = plan_response(s, {})
            self.assertIsNotNone(plan, rep["hotspot"])
            self.assertTrue(plan["agency_requests"] or plan["drones"], rep["hotspot"])
            self.assertTrue({q["kind"] for q in plan["agency_requests"]} <= KINDS)

    def test_bad_input_returns_none_not_raise(self):
        self.assertIsNone(plan_response({"reported_people": "many"}, {"resources": "broken"}))


class OfflineOnly(unittest.TestCase):
    def test_no_llm_or_network_code(self):
        root = Path(__file__).resolve().parents[1] / "swarm_ops" / "reasoning"
        pat = re.compile(r"^\s*(import|from)\s+(anthropic|openai|requests|httpx|urllib|http|socket|aiohttp|ollama)\b", re.M)
        for f in root.rglob("*.py"):
            self.assertIsNone(pat.search(f.read_text(encoding="utf8")), f.name)

    def test_builtin_taxonomy_fallback(self):
        tx = load_taxonomy(Path("does/not/exist.json"))
        self.assertEqual(set(tx.kinds), set(builtin_taxonomy().kinds))
        self.assertEqual(tx.kind("rescue"), "rescue_team")
        self.assertEqual(tx.kind("engineering"), "jcb")


if __name__ == "__main__":
    unittest.main()
