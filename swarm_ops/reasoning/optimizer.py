"""Response optimisation: which ground units go to which incident.

Model (identical for both solvers), over every concurrent incident at once:

    x_{u,i} in {0,1}           unit u is sent to incident i (each unit at most one incident)
    f_{u,d}, g_{u,d} >= 0      part of unit u's cover that serves demand d's expected amount (f) or its
                               uncertainty margin (g);  f + g <= cover(u,d) * x_{u,i(d)}
    sum_u f_{u,d} <= A_d,  sum_u g_{u,d} <= M_d

    maximise  sum_{u,d} (w_d / A_d) * exp(-eta_{u,i} / tau_d) * (f_{u,d} + m * g_{u,d})  -  sum cost_{u,i} x_{u,i}

    cover(u,d) = seats/patients (people capabilities) or 1 (work) x taxonomy effectiveness x access factor
    cost_{u,i} = unit_cost x escalation factor(i) + scarcity_cost / units of that kind available,
                 x (1 - retention_bonus) if u was already committed to i (hysteresis)

So value is expected harm avoided, discounted by each unit's own ETA (faster units are
worth more), capped at the expected demand plus a partly-valued uncertainty margin.
Scarce units go to the incident with the highest marginal value per cost: fairness
across concurrent incidents emerges from the shared, concave objective.

Solvers: OR-Tools CP-SAT on the integer-scaled model (single worker, fixed seed,
deterministic-time budget, warm-started) when installed; otherwise a lazy greedy on
marginal gain followed by a removal pass. For a fixed assignment the optimal f/g is
"fastest units fill the expected demand first", which is how objective() evaluates it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

try:  # optional exact solver
    from ortools.sat.python import cp_model  # type: ignore
except Exception:  # pragma: no cover
    cp_model = None


@dataclass
class Unit:
    id: str
    kind: str
    capacity: int
    eta: dict                    # incident_id -> seconds
    virtual: bool = False        # placeholder for "one unit of this kind"; the orchestrator picks which
    previous: str | None = None  # incident it was committed to in the last plan
    fixed: str | None = None     # already on scene there (counts as cover, never requested)
    label: str = ""


@dataclass
class Allocation:
    assignment: dict = field(default_factory=dict)          # unit_id -> incident_id
    value: float = 0.0
    cost: float = 0.0
    solver: str = "greedy"
    contributions: dict = field(default_factory=dict)       # unit_id -> [(capability, amount, "mean"|"margin")]
    problem: object = None


def unit_cover(unit, demand, kind_spec, p, depth_m):
    eff = kind_spec["capabilities"].get(demand.capability, 0.0)
    if eff <= 0:
        return 0.0
    if unit.kind == "boat":
        access = 1.0 if depth_m >= p["boat_min_water_m"] else 0.0
    elif unit.kind == "helicopter":
        access = 1.0
    else:
        access = 1.0 if depth_m <= p["road_max_water_m"] else 0.15
    if access == 0.0:
        return 0.0
    if demand.measure != "units" and demand.capability in ("water_supply", "supply_delivery"):
        if unit.kind == "water_tanker":
            return unit.capacity / p["litres_per_person"] * eff * access
        return unit.capacity * p["ration_people_per_capacity"] * eff * access
    if demand.measure == "people":
        size = p["care_per_unit"] if demand.capability == "medical_care" else p["people_capacity"].get(unit.kind, unit.capacity)
        return size * eff * access
    return eff * access


class Problem:
    def __init__(self, demands, units, taxonomy, p, depth, cost_factor):
        self.p, self.m = p, p["margin_value"]
        self.units = sorted(units, key=lambda u: u.id)
        self.by_id = {u.id: u for u in self.units}
        self.demands = {}
        for d in demands:
            self.demands.setdefault(d.incident_id, []).append(d)
        self.incidents = sorted(self.demands)
        avail = {}
        for u in self.units:
            if not u.fixed:
                avail[u.kind] = avail.get(u.kind, 0) + 1
        self.cover = {}   # (unit, incident) -> [(j, cover, discount)]
        for u in self.units:
            spec = taxonomy.kinds.get(u.kind)
            if spec is None:
                continue
            for i in self.incidents:
                if u.fixed and u.fixed != i:
                    continue
                eta = 0.0 if u.fixed else float(u.eta.get(i, p["default_unit_eta_s"]))
                parts = []
                for j, d in enumerate(self.demands[i]):
                    c = unit_cover(u, d, spec, p, depth.get(i, 0.0))
                    if c > 0:
                        parts.append((j, c, math.exp(-eta / max(d.tau_s, 1.0))))
                if parts:
                    self.cover[(u.id, i)] = parts
        self.cost = {}
        for (uid, i) in self.cover:
            u = self.by_id[uid]
            if u.fixed:
                continue
            c = p["unit_cost"] * cost_factor.get(i, 1.0) + p["scarcity_cost"] / max(avail.get(u.kind, 1), 1)
            if u.previous == i:
                c *= 1 - p["retention_bonus"]
            self.cost[(uid, i)] = c
        self.fixed = [(u.id, u.fixed) for u in self.units if u.fixed and (u.id, u.fixed) in self.cover]

    def incident_value(self, i, uids, detail=False):
        """Optimal fill for a fixed set of units at incident i: fastest (highest discount) first."""
        total, parts = 0.0, {}
        for j, d in enumerate(self.demands[i]):
            A, M = d.amount, d.margin
            rate = d.weight / max(A, 1e-6)
            offers = sorted(((disc, c, uid) for uid in uids for jj, c, disc in self.cover.get((uid, i), ()) if jj == j),
                            key=lambda o: (-o[0], o[2]))
            left_a, left_m = A, M
            for disc, c, uid in offers:
                fa = min(c, left_a)
                fm = min(c - fa, left_m)
                left_a -= fa
                left_m -= fm
                total += rate * disc * (fa + self.m * fm)
                if detail:
                    if fa > 1e-9:
                        parts.setdefault(uid, []).append((d.capability, fa, "mean"))
                    elif fm > 1e-9:
                        parts.setdefault(uid, []).append((d.capability, fm, "margin"))
        return (total, parts) if detail else total

    def members(self, assignment, i):
        return [u for u, ii in assignment.items() if ii == i] + [u for u, ii in self.fixed if ii == i]

    def objective(self, assignment):
        value = sum(self.incident_value(i, self.members(assignment, i)) for i in self.incidents)
        cost = sum(self.cost[(u, i)] for u, i in assignment.items())
        return value - cost, value, cost


def solve_greedy(prob):
    """Lazy greedy on marginal gain; deterministic tie-break (unit id, incident id); then a removal pass."""
    assignment = {}
    cur = {i: prob.incident_value(i, prob.members(assignment, i)) for i in prob.incidents}

    def gain(uid, i):
        return prob.incident_value(i, prob.members(assignment, i) + [uid]) - cur[i] - prob.cost[(uid, i)]

    gains = {k: gain(*k) for k in prob.cost}
    while gains:
        (uid, i), g = min(gains.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
        if g <= 1e-9:
            break
        assignment[uid] = i
        cur[i] = prob.incident_value(i, prob.members(assignment, i))
        gains = {k: (gain(*k) if k[1] == i else v) for k, v in gains.items() if k[0] != uid}
    best = prob.objective(assignment)[0]
    for uid in sorted(assignment):
        trial = {k: v for k, v in assignment.items() if k != uid}
        val = prob.objective(trial)[0]
        if val > best + 1e-9:
            assignment, best = trial, val
    return assignment


def solve_cpsat(prob, hint, det_time):
    if cp_model is None or not prob.cost:
        return None
    S = 1000                                   # amounts are scaled to integers
    model = cp_model.CpModel()
    x = {k: model.NewBoolVar(f"x{n}") for n, k in enumerate(sorted(prob.cost))}
    for u in prob.units:
        ks = [v for k, v in x.items() if k[0] == u.id]
        if len(ks) > 1:
            model.AddAtMostOne(ks)
    obj = []
    for i in prob.incidents:
        uids = [uid for (uid, ii) in prob.cover if ii == i]
        for j, d in enumerate(prob.demands[i]):
            rate = d.weight / max(d.amount, 1e-6)
            fs, gs = [], []
            for uid in uids:
                for jj, c, disc in prob.cover[(uid, i)]:
                    if jj != j:
                        continue
                    cap = int(round(c * S))
                    f = model.NewIntVar(0, cap, "")
                    g = model.NewIntVar(0, cap, "")
                    if (uid, i) in x:
                        model.Add(f + g <= cap).OnlyEnforceIf(x[(uid, i)])
                        model.Add(f + g == 0).OnlyEnforceIf(x[(uid, i)].Not())
                    else:   # fixed on-scene unit
                        model.Add(f + g <= cap)
                    fs.append(f)
                    gs.append(g)
                    obj.append(int(round(rate * disc * 1e3)) * f)
                    obj.append(int(round(rate * disc * prob.m * 1e3)) * g)
            if fs:
                model.Add(sum(fs) <= int(round(d.amount * S)))
                model.Add(sum(gs) <= int(round(d.margin * S)))
    for k, v in x.items():
        obj.append(-int(round(prob.cost[k] * S * 1e3)) * v)
    model.Maximize(sum(obj))
    for k, v in x.items():
        model.AddHint(v, 1 if hint.get(k[0]) == k[1] else 0)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_deterministic_time = det_time
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {k[0]: k[1] for k, v in x.items() if solver.Value(v)}, status == cp_model.OPTIMAL


def allocate(demands, units, taxonomy, p, depth, cost_factor, solver="auto"):
    prob = Problem(demands, units, taxonomy, p, depth, cost_factor)
    assignment = solve_greedy(prob)
    used = "greedy"
    use_cp = solver == "cpsat"
    if use_cp and cp_model is not None:
        res = solve_cpsat(prob, assignment, p.get("cpsat_det_time", 2.0))
        if res is not None:
            cand, optimal = res
            if prob.objective(cand)[0] >= prob.objective(assignment)[0] - 1e-9:
                assignment, used = cand, "cp-sat" if optimal else "cp-sat (feasible)"
            else:
                used = "greedy (better than cp-sat incumbent)"
    total, value, cost = prob.objective(assignment)
    out = Allocation(assignment=assignment, value=value, cost=cost, solver=used, problem=prob)
    for i in prob.incidents:
        _, parts = prob.incident_value(i, prob.members(assignment, i), detail=True)
        for uid, ps in parts.items():
            out.contributions.setdefault(uid, []).extend(ps)
    return out
