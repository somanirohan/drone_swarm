"""Per-incident belief: evidence fusion over the hidden state of a scene.

Every message about an incident (the orchestrator's report, free text, each
drone briefing, a ground-crew report) is treated as a noisy measurement with a
source confidence. Counts (people, injured, critical, vulnerable, immobile) and
hazard intensities are fused with scalar Kalman updates (Gaussian, inverse-
variance weighting) and drift between observations (random-walk process noise),
so stale evidence loses weight. The overall threat level is tracked by a
two-state (level, rate) Kalman filter whose rate gives the trend and its
probability. Quantities that no evidence has touched keep their prior and a
wide variance; that variance is what makes the planner send an assessing drone
(value of information) and hold margin units on standby.

Pure Python, deterministic for the same inputs, no I/O.
"""
from __future__ import annotations

import math
import re

HAZARDS = ("water", "fire", "gas", "structural", "electrical", "crowding", "debris", "heat")
COUNTS = ("people", "injured", "critical", "vulnerable", "immobile")
SHORTAGES = ("water", "food", "medical")

# Lexicon: words -> hazard. Used for free text and for hazard strings; also run over
# the taxonomy's own category names/keywords to derive category hazard priors.
HAZARD_WORDS = {
    "water": ("flood", "water", "submerge", "inundat", "rising", "river", "rooftop", "terrace", "waterlog", "boat"),
    "fire": ("fire", "smoke", "burn", "flame", "blaze"),
    "gas": ("gas", "chemical", "fume", "toxic", "leak", "ammonia", "chlorine", "lpg", "cylinder", "hazmat"),
    "structural": ("collapse", "wall", "crack", "rubble", "earthquake", "building damage", "structural", "landslide", "debris"),
    "electrical": ("live wire", "power line", "electric", "sparking", "cable", "wire", "transformer", "electrocut"),
    "crowding": ("crowd", "stampede", "overcrowd", "shelter full", "full shelter", "at capacity"),
    "debris": ("tree", "blocked", "fallen", "obstruct", "uproot", "branch"),
    "heat": ("heat", "sunstroke", "dehydrat"),
}
SHORTAGE_WORDS = {"water": ("no water", "out of water", "drinking water", "thirst", "water shortage", "no drinking"),
                  "food": ("no food", "out of food", "hungry", "food shortage", "starving", "ration"),
                  "medical": ("medicine", "insulin", "dialysis", "oxygen")}
VULNERABLE_WORDS = ("elderly", "old ", "aged", "grandmother", "grandfather", "senior", "child", "children", "infant", "baby",
                    "toddler", "pregnant", "disabled", "wheelchair", "bedridden", "cannot walk", "can't walk", "blind")
INJURY_WORDS = ("injur", "hurt", "bleed", "fracture", "broken", "burn", "wound", "casualt", "fainted", "chest pain")
CRITICAL_WORDS = ("unconscious", "not breathing", "severe bleeding", "critical", "cardiac", "unresponsive", "heavy bleeding",
                  "crushed", "pinned", "trapped under")
IMMOBILE_WORDS = ("trapped", "stranded", "stuck", "pinned", "cannot get out", "can't get out", "cut off", "on the roof", "rooftop")
TREND_WORDS = {"worsening": ("rising", "worsening", "spreading", "getting worse", "increasing", "growing", "escalat"),
               "improving": ("receding", "improving", "under control", "contained", "stabilis", "stabiliz", "subsiding")}
# Structured hazard tokens used by the PCMC scenario / command center (hazards_reported).
HAZARD_TOKENS = {
    "rising_water": {"water": 0.7}, "deep_water": {"water": 0.9}, "standing_water": {"water": 0.35},
    "flood": {"water": 0.6}, "fire": {"fire": 0.7}, "smoke": {"fire": 0.3, "gas": 0.4}, "gas": {"gas": 0.7},
    "chemical": {"gas": 0.8}, "live_wire": {"electrical": 0.85}, "road_blocked": {"debris": 0.7},
    "collapse": {"structural": 0.75}, "debris": {"debris": 0.4, "structural": 0.3}, "crowding": {"crowding": 0.6},
    "heat": {"heat": 0.7},
}
TOKEN_TREND = {"rising_water": "worsening", "spreading": "worsening", "receding": "improving"}
TOKEN_SHORTAGE = {"no_drinking_water": "water", "no_water": "water", "no_food": "food", "no_medicine": "medical"}
INJURY_TOKENS = {"trapped": {"immobile_min": 1}, "respiratory": {"injured_min": 1}, "burns": {"injured_min": 1},
                 "unconscious": {"injured_min": 1, "critical_min": 1}, "bleeding": {"injured_min": 1},
                 "fracture": {"injured_min": 1}, "injured": {"injured_min": 1}}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "a dozen": 12, "dozen": 12, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100}
PEOPLE_NOUNS = r"(?:people|persons|residents|villagers|students|workers|families|occupants|passengers|individuals|members|men|women|kids|children|evacuees)"


def _num(token):
    token = token.strip().lower()
    return int(token) if token.isdigit() else NUMBER_WORDS.get(token)


def _hits(text, words):
    return sum(1 for w in words if w in text)


def parse_text(text):
    """Free text -> partial observation (counts, hazards, shortages, trend). Low-confidence evidence."""
    t = " " + (text or "").lower() + " "
    if not t.strip():
        return {}
    num = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"
    obs = {}
    m = re.search(r"family of " + num, t) or re.search(num + r"\s+(?:\w+\s){0,2}?" + PEOPLE_NOUNS, t)
    if m:
        obs["people"] = _num(m.group(1))
    elif re.search(r"\bcouple\b", t):
        obs["people"] = 2
    elif re.search(r"\bfamil(y|ies)\b", t):
        obs["people"] = 4 if "families" not in t else 12      # household size prior (text evidence, low confidence)
    m = re.search(num + r"\s+(?:\w+\s)?(?:casualt|injur|hurt|wounded)", t)
    if m:
        obs["injured"] = _num(m.group(1))
    elif _hits(t, INJURY_WORDS):
        obs["injured_min"] = 1
    m = re.search(num + r"\s+(?:\w+\s)?(?:trapped|stranded|stuck)", t)
    if m:
        obs["immobile"] = _num(m.group(1))
    elif _hits(t, IMMOBILE_WORDS):
        obs["immobile_min"] = 1
    if re.search(r"(terrace|rooftop|on the roof|on roof|cut off|marooned)", t) and (_hits(t, HAZARD_WORDS["water"]) or "terrace" in t):
        obs["immobile_all"] = True
    if _hits(t, CRITICAL_WORDS):
        obs["critical_min"] = 1
    v = _hits(t, VULNERABLE_WORDS)
    if v:
        obs["vulnerable_min"] = v
    hz = {h: min(1.0, 0.45 + 0.15 * _hits(t, w)) for h, w in HAZARD_WORDS.items() if _hits(t, w)}
    if re.search(r"(dry|no water|not flooded|no flooding)", t) and "water" in hz and not re.search(r"(flood|rising|submerge)", t):
        hz.pop("water")
    if hz:
        obs["hazards"] = hz
    sh = [s for s, w in SHORTAGE_WORDS.items() if _hits(t, w)]
    if "water" in sh and "water" in hz and not re.search(r"(flood|rising|submerge|inundat|waterlog|river)", t):
        hz.pop("water")                     # "out of drinking water" is a shortage, not a flood
        if not hz:
            obs.pop("hazards", None)
    if sh:
        obs["shortages"] = sh
    for label, words in TREND_WORDS.items():
        if _hits(t, words):
            obs["trend"] = label
    if re.search(r"(road|lane|route|access)\s+(?:is\s+)?(blocked|closed|cut)", t) or "blocking" in t:
        obs.setdefault("access", {})["road_blocked"] = True
    if re.search(r"ambulance|emergency route|hospital road", t) and ("block" in t or "fallen" in t):
        obs.setdefault("access", {})["blocks_emergency_route"] = True
    return obs


class Gauss:
    """Scalar Gaussian belief with Kalman update and random-walk drift."""
    __slots__ = ("mean", "var", "n", "t")

    def __init__(self, mean, sd, t=0.0):
        self.mean, self.var, self.n, self.t = float(mean), float(sd) ** 2, 0, float(t)

    def drift(self, t, q_per_s):
        if t > self.t:
            self.var += q_per_s * (t - self.t)
            self.t = t

    def update(self, z, sd):
        r = max(float(sd) ** 2, 1e-9)
        k = self.var / (self.var + r)
        self.mean += k * (float(z) - self.mean)
        self.var *= (1 - k)
        self.n += 1

    @property
    def sd(self):
        return math.sqrt(max(self.var, 0.0))


class LogCount:
    """Count belief as a Gaussian over log(1 + n): scale-free, so '40 people' from a fair source
    outweighs a prior of 3 without a hand-tuned absolute variance. mean/sd are in people."""
    __slots__ = ("mu", "s2", "n", "t")

    def __init__(self, mean, sd_log, t=0.0):
        self.mu, self.s2, self.n, self.t = math.log1p(max(float(mean), 0.0)), float(sd_log) ** 2, 0, float(t)

    def drift(self, t, q_per_s):
        if t > self.t:
            self.s2 += q_per_s * (t - self.t)
            self.t = t

    def update(self, z, sd_log):
        r = max(float(sd_log) ** 2, 1e-9)
        k = self.s2 / (self.s2 + r)
        self.mu += k * (math.log1p(max(float(z), 0.0)) - self.mu)
        self.s2 *= (1 - k)
        self.n += 1

    @property
    def mean(self):
        return math.expm1(self.mu)

    @mean.setter
    def mean(self, v):
        self.mu = math.log1p(max(float(v), 0.0))

    @property
    def sd(self):
        return (self.mean + 1.0) * math.sqrt(max(math.exp(self.s2) - 1.0, 0.0))


class TrendFilter:
    """Two-state (level, rate) Kalman filter over the scene's overall threat level in [0, 1]."""

    def __init__(self, level, t, p):
        self.x = [level, 0.0]
        self.P = [[0.09, 0.0], [0.0, (2 * p["trend_word_rate"]) ** 2]]
        self.t = t
        self.p = p
        self.n = 0

    def predict(self, t):
        dt = max(0.0, t - self.t)
        if dt == 0:
            return
        (a, b), (c, d) = self.P
        self.x = [self.x[0] + self.x[1] * dt, self.x[1]]
        # F P F^T with F = [[1, dt], [0, 1]], plus Q.
        a2 = a + dt * (b + c) + dt * dt * d + self.p["hazard_drift_per_s"] * dt
        b2 = b + dt * d
        c2 = c + dt * d
        d2 = d + self.p["rate_drift_per_s"] * dt
        self.P = [[a2, b2], [c2, d2]]
        self.t = t

    def _update(self, h, z, r):
        # h selects the observed component (0 level, 1 rate).
        P = self.P
        s = P[h][h] + r
        k = [P[0][h] / s, P[1][h] / s]
        y = z - self.x[h]
        self.x = [self.x[0] + k[0] * y, self.x[1] + k[1] * y]
        self.P = [[P[0][0] - k[0] * P[h][0], P[0][1] - k[0] * P[h][1]],
                  [P[1][0] - k[1] * P[h][0], P[1][1] - k[1] * P[h][1]]]
        self.n += 1

    def observe_level(self, z, sd):
        self._update(0, z, sd * sd)

    def observe_rate(self, z, sd):
        self._update(1, z, sd * sd)

    def trend(self):
        p = self.p
        rate, sd = self.x[1], math.sqrt(max(self.P[1][1], 1e-18))
        change, change_sd = rate * p["trend_horizon_s"], sd * p["trend_horizon_s"]
        p_worse = 0.5 * (1 + math.erf(rate / (sd * math.sqrt(2))))
        if change > p["trend_min_change"] and change > p["trend_z"] * change_sd * 0.5:
            label = "worsening"
        elif change < -p["trend_min_change"] and -change > p["trend_z"] * change_sd * 0.5:
            label = "improving"
        else:
            label = "stable"
        return label, rate, p_worse


def effective_hazard(h, v):
    """Hazard intensity as it affects mobility: shallow water (< ~0.5 m) does not strand people."""
    v = max(0.0, min(1.0, v))
    return max(0.0, min(1.0, (v - 0.3) / 0.4)) if h == "water" else v


def _conf(obs, p):
    c = obs.get("confidence")
    if isinstance(c, (int, float)) and 0 < c <= 1:
        return float(c)
    return p["source_confidence"].get(obs.get("source", "report"), 0.5)


def _hazard_value(name, value, p):
    if isinstance(value, bool):
        return 0.7 if value else 0.0
    if name == "water_depth_m":
        return max(0.0, min(1.0, float(value) / p["water_depth_full_m"]))
    return max(0.0, min(1.0, float(value)))


def normalise_hazards(raw, p):
    """dict {name: intensity|bool} (or water_depth_m) or list of strings -> {hazard: intensity}."""
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if v is None:
                continue
            if k == "water_depth_m":
                out["water"] = max(out.get("water", 0.0), _hazard_value(k, v, p))
            elif k in HAZARDS and isinstance(v, (int, float, bool)):
                out[k] = _hazard_value(k, v, p)
            elif isinstance(v, (int, float, bool)):
                for h, words in HAZARD_WORDS.items():
                    if any(w in k.lower() for w in words):
                        out[h] = max(out.get(h, 0.0), _hazard_value(k, v, p))
    elif isinstance(raw, (list, tuple)):
        for s in raw:
            s = str(s).lower()
            if s in HAZARD_TOKENS:
                for h, v in HAZARD_TOKENS[s].items():
                    out[h] = max(out.get(h, 0.0), v)
                continue
            for h, words in HAZARD_WORDS.items():
                if s == h or any(w in s for w in words):
                    out[h] = max(out.get(h, 0.0), 0.6)
    return out


def category_hazard_prior(category, taxonomy):
    """Hazards a category implies, derived from the taxonomy's own hazard id, name and keywords."""
    name = " ".join([category.get("id", "").replace("_", " "), category.get("display_name", "") or ""]).lower()
    kws = category.get("keywords", [])
    total = sum(w for _, w in kws) or 1.0
    prior = {}
    for h, words in HAZARD_WORDS.items():
        share = sum(w for k, w in kws if any(x in k for x in words)) / total
        v = 0.5 * min(1.0, 3.0 * share) + (0.3 if any(x in name for x in words) else 0.0)
        if v >= 0.15:
            prior[h] = min(0.6, v)
    hz = {"flood": "water", "fire": "fire", "seismic": "structural", "air": "gas", "heat": "heat"}.get(category.get("hazard"))
    # The deployment's primary hazard is a weak prior only (DisruptionOps tags most categories 'flood').
    if hz and hz not in prior:
        prior[hz] = 0.15
    return prior


def _vuln_count(v):
    if isinstance(v, dict):
        return sum(int(x) for x in v.values() if isinstance(x, (int, float)) and not isinstance(x, bool))
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    return None


def _first_number(*vals):
    for v in vals:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return 0.0


def observations_from(situation, p):
    """All evidence in the situation, oldest first, as normalised observation dicts."""
    t_now = float(situation.get("t_s", 0.0) or 0.0)
    onset = _first_number(situation.get("onset_s"), situation.get("reported_at"), t_now)
    out = []
    text = situation.get("text") or situation.get("report_text")
    if text:
        o = parse_text(text)
        if o:
            o.update(source="text", t_s=onset)
            out.append(o)
    rep = dict(situation.get("report") or {})
    for key in ("injured", "critical", "immobile", "shortages", "access", "trend"):
        if key in situation and key not in rep:
            rep[key] = situation[key]
    if "trapped" in situation and "immobile" not in rep:
        rep["immobile"] = situation["trapped"]
    # People: an explicitly reported count is evidence; `people`/`crowd_size` from a command center may be a
    # template default, so it is weaker; `reported_people: None` means "not stated" (no evidence).
    if isinstance(situation.get("reported_people"), (int, float)):
        rep.setdefault("people", situation["reported_people"])
    elif "people" in situation and "reported_people" not in situation and "people" not in rep:
        rep["people"] = situation["people"]
        rep.setdefault("_people_weak", True)
    if isinstance(situation.get("crowd_size"), (int, float)) and "people" not in rep and "reported_people" not in situation:
        rep["people"] = situation["crowd_size"]
        rep.setdefault("_people_weak", True)
    vul = situation.get("vulnerable")
    if isinstance(vul, (list, tuple)):
        if vul:
            rep["vulnerable_min"] = max(len(vul), 1)
    elif vul is not None:
        rep.setdefault("vulnerable", vul)
    for tok in situation.get("injuries") or []:
        for k, v in INJURY_TOKENS.get(str(tok).lower(), {"injured_min": 1}).items():
            rep[k] = max(rep.get(k, 0), v)
    if situation.get("injured_reported") is True:
        rep["injured_min"] = max(rep.get("injured_min", 0), 1)
    tokens = list(situation.get("hazards_reported") or [])
    hz = situation.get("hazards")
    if isinstance(hz, dict):
        rep.setdefault("hazards", hz)
    elif isinstance(hz, (list, tuple)):
        tokens += [h for h in hz if h not in tokens]
    if tokens:
        lst = [t for t in tokens if str(t).lower() not in TOKEN_SHORTAGE]
        if lst and "hazards" not in rep:
            rep["hazards"] = lst
        sh = [TOKEN_SHORTAGE[str(t).lower()] for t in tokens if str(t).lower() in TOKEN_SHORTAGE]
        if sh:
            rep["shortages"] = list(rep.get("shortages") or []) + sh
        tr = [TOKEN_TREND[str(t).lower()] for t in tokens if str(t).lower() in TOKEN_TREND]
        if tr and "trend" not in rep:
            rep["trend"] = tr[0]
        if any(str(t).lower() == "road_blocked" for t in tokens):
            rep.setdefault("access", {})["road_blocked"] = True
    if isinstance(situation.get("height_m"), (int, float)) and situation["height_m"] >= 3 and "rising_water" in tokens:
        rep["immobile_min"] = max(rep.get("immobile_min", 0), 1)   # on a terrace above rising water
        rep["immobile_all"] = True
    for need in situation.get("needs") or []:
        if need == "rescue":
            rep["immobile_min"] = max(rep.get("immobile_min", 0), 1)
        elif need in ("food", "water") and need not in (rep.get("shortages") or []):
            rep.setdefault("_weak_shortages", []).append(need)
    if isinstance(situation.get("severity"), (int, float)) and not isinstance(situation.get("severity"), bool):
        rep["_severity"] = max(1, min(5, int(situation["severity"])))
    callers = situation.get("callers", situation.get("reporters"))
    if isinstance(callers, (int, float)) and callers >= 1 and "confidence" not in rep:
        base = p["source_confidence"]["orchestrator"]
        rep["confidence"] = round(1 - (1 - base) ** (1 + 0.5 * (int(callers) - 1)), 3)   # independent callers corroborate
        rep["people_min"] = 1
    if rep:
        rep.setdefault("source", "orchestrator")
        rep.setdefault("t_s", onset)
        if "confidence" not in rep and isinstance(situation.get("report_confidence"), (int, float)):
            rep["confidence"] = situation["report_confidence"]
        out.append(rep)
    b = situation.get("briefing")
    if isinstance(b, dict):
        bo = {"source": "drone" if str(b.get("source", "")).upper().startswith("D") else b.get("source", "drone"),
              "t_s": b.get("observed_at", t_now), "confidence": b.get("confidence"), "people": b.get("people"),
              "hazards": b.get("hazards")}
        if b.get("summary"):
            extra = parse_text(b["summary"])
            for k in ("injured", "critical_min", "injured_min", "vulnerable_min", "trend"):
                if k in extra:
                    bo.setdefault(k, extra[k])
        out.append({k: v for k, v in bo.items() if v is not None})
    for o in situation.get("observations") or []:
        o = dict(o)
        if "trapped" in o and "immobile" not in o:
            o["immobile"] = o["trapped"]
        if o.get("text"):
            for k, v in parse_text(o["text"]).items():
                o.setdefault(k, v)
        o.setdefault("t_s", t_now)
        o.setdefault("source", "drone")
        out.append(o)
    out.sort(key=lambda o: float(o.get("t_s", 0.0) or 0.0))
    return out


class Belief:
    """Fused estimate of one incident's hidden state."""

    def __init__(self, situation, taxonomy, p):
        self.p = p
        self.category = taxonomy.category(situation.get("disruptionops_category") or situation.get("category") or "unknown_report")
        self.t = float(situation.get("t_s", 0.0) or 0.0)
        self.onset = _first_number(situation.get("onset_s"), situation.get("reported_at"), self.t)
        pm, psd = p["people_prior"]
        self.counts = {"people": LogCount(pm, psd, self.onset)}
        self.mins = {}
        hz_prior = category_hazard_prior(self.category, taxonomy)
        self.hazards = {h: Gauss(hz_prior.get(h, 0.0), 0.25 if h in hz_prior else 0.12, self.onset) for h in HAZARDS}
        self.hazard_observed = set()
        self.shortages = {s: Gauss(0.5 if self.category["id"] == "supply_shortage" else 0.05, 0.3, self.onset) for s in SHORTAGES}
        level0 = max(hz_prior.values(), default=0.2)
        self.trend = TrendFilter(level0, self.onset, p)
        self.access = {"road_blocked": False, "blocks_emergency_route": False, "road_water_m": None}
        self.link = 1.0
        self.responders = set()
        self.treated = 0
        self.evidence = 0
        self.sources = []
        self.declared_trend = None
        self.immobile_all = False
        for o in observations_from(situation, p):
            self._fuse(o)
        # Drift everything to "now": stale evidence is less certain.
        for g in self.counts.values():
            g.drift(self.t, p["people_drift_per_s"])
        for g in self.hazards.values():
            g.drift(self.t, p["hazard_drift_per_s"])
        self.trend.predict(self.t)
        self._finalise()

    # ------------------------------------------------------------ fusion ---
    def _count(self, name, prior_mean, t):
        g = self.counts.get(name)
        if g is None:
            g = LogCount(prior_mean, self.p["derived_count_sd_log"], t)
            self.counts[name] = g
        return g

    def _prior_count(self, name):
        p, people = self.p, max(self.counts["people"].mean, 0.0)
        ls = "life_safety" if self.category["life_safety"] else "other"
        if name == "injured":
            return people * p["injured_share_prior"][ls]
        if name == "critical":
            return self._prior_count("injured") * p["critical_share_of_injured"] if "injured" not in self.counts \
                else self.counts["injured"].mean * p["critical_share_of_injured"]
        if name == "vulnerable":
            return people * p["vulnerable_share_prior"]
        if name == "immobile":
            share = sum(p["immobile_per_hazard"].get(h, 0.0) * effective_hazard(h, g.mean) for h, g in self.hazards.items())
            return people * min(1.0, share)
        return 0.0

    def _fuse(self, o):
        p = self.p
        t = _first_number(o.get("t_s"), self.t)
        c = _conf(o, p)
        src = o.get("source", "report")
        self.evidence += 1
        self.sources.append(src)
        for name in COUNTS:
            val = o.get(name)
            if name == "vulnerable":
                val = _vuln_count(val)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            g = self._count(name, self._prior_count(name), t)
            g.drift(t, p["people_drift_per_s"])
            cc = c * 0.5 if (name == "people" and o.get("_people_weak")) else c
            g.update(max(0.0, float(val)), (1 - cc) * p["count_noise_k"] + p["count_noise_floor"])
        if o.get("immobile_all"):   # "family on the terrace": everyone present is immobile (soft, in time order)
            self.immobile_all = True
            g = self._count("immobile", self._prior_count("immobile"), t)
            n = self.counts["people"].mean
            if g.mean < n:
                g.update(n, 0.3 + 0.5 * (1 - c))
        if isinstance(o.get("_severity"), int):
            self.trend.predict(t)
            self.trend.observe_level((o["_severity"] - 1) / 4.0, 0.35)
        for s in o.get("_weak_shortages") or []:
            if s in self.shortages:
                self.shortages[s].update(1.0, 0.9)
        # "at least k" evidence (a word like 'trapped', a vulnerable label) is a soft measurement in time order,
        # so a later confident observation ("nobody trapped any more") can overrule it.
        for key in ("injured_min", "critical_min", "vulnerable_min", "immobile_min"):
            if isinstance(o.get(key), (int, float)) and not isinstance(o.get(key), bool):
                g = self._count(key[:-4], self._prior_count(key[:-4]), t)
                if g.mean < o[key]:
                    g.update(float(o[key]), (1 - c) * p["count_noise_k"] + p["count_noise_floor"])
        if isinstance(o.get("people_min"), (int, float)):
            self.mins["people"] = max(self.mins.get("people", 0), int(o["people_min"]))
        hz = normalise_hazards(o.get("hazards"), p)
        if isinstance(o.get("access"), dict) and isinstance(o["access"].get("road_water_m"), (int, float)):
            self.access["road_water_m"] = float(o["access"]["road_water_m"])
        for h, v in hz.items():
            g = self.hazards[h]
            g.drift(t, p["hazard_drift_per_s"])
            g.update(v, (1 - c) * p["hazard_noise_k"] + p["hazard_noise_floor"])
            self.hazard_observed.add(h)
        # An explicit numeric hazard dict that sets a hazard to 0 is evidence of absence (e.g. dry street).
        if hz:
            level = max(hz.values())
            self.trend.predict(t)
            self.trend.observe_level(level, (1 - c) * p["hazard_noise_k"] + p["hazard_noise_floor"])
        for s in o.get("shortages") or []:
            if s in self.shortages:
                self.shortages[s].update(1.0, (1 - c) * 0.6 + 0.05)
        for s in o.get("supplied") or []:
            if s in self.shortages:
                self.shortages[s].update(0.0, (1 - c) * 0.6 + 0.05)
        tr = o.get("trend")
        if tr in ("worsening", "improving", "stable"):
            self.declared_trend = tr
            self.trend.predict(t)
            z = {"worsening": 1, "improving": -1, "stable": 0}[tr] * p["trend_word_rate"]
            self.trend.observe_rate(z, p["trend_word_rate"] * (1.6 - c))
        acc = o.get("access")
        if isinstance(acc, dict):
            for k in ("road_blocked", "blocks_emergency_route"):
                if k in acc:
                    self.access[k] = bool(acc[k])
        if isinstance(o.get("link_quality"), (int, float)):
            self.link = float(o["link_quality"])
        for r in o.get("responders_on_scene") or []:
            self.responders.add(str(r))
        if isinstance(o.get("treated"), (int, float)):
            self.treated = max(self.treated, int(o["treated"]))

    def _finalise(self):
        p = self.p
        for name in COUNTS:
            if name not in self.counts:
                m = self._prior_count(name)
                self.counts[name] = LogCount(m, p["derived_count_sd_log"], self.t)
        people = self.counts["people"]
        people.mean = max(people.mean, 0.0)
        for name, lo in self.mins.items():
            g = self.counts[name]
            if g.mean < lo:
                g.mean = float(lo)
        if people.mean < self.counts["immobile"].mean:
            people.mean = self.counts["immobile"].mean
        if people.mean < self.counts["injured"].mean:
            people.mean = self.counts["injured"].mean
        for name in ("injured", "vulnerable", "immobile"):
            g = self.counts[name]
            g.mean = min(max(g.mean, 0.0), people.mean)
        crit = self.counts["critical"]
        crit.mean = min(max(crit.mean, 0.0), self.counts["injured"].mean)
        for g in list(self.hazards.values()) + list(self.shortages.values()):
            g.mean = min(1.0, max(0.0, g.mean))
        self.trend_label, self.trend_rate, self.p_worsening = self.trend.trend()

    # ----------------------------------------------------------- outputs ---
    def mean(self, name):
        return self.counts[name].mean

    def sd(self, name):
        return self.counts[name].sd

    def hazard(self, name):
        return self.hazards[name].mean

    def observed(self, name):
        return self.counts[name].n > 0 or name in self.mins

    def uncertainty(self):
        """0 (well known) .. 1 (nothing known): mean normalised spread of the decision-relevant state."""
        n = self.mean("people")
        terms = [min(1.0, self.sd("people") / (n + 1.5))]
        for name in ("injured", "critical", "immobile"):   # spread relative to the scene's size
            terms.append(min(1.0, 2.0 * self.sd(name) / (n + 1.5)))
        main = max(self.hazards.values(), key=lambda g: g.mean)
        terms.append(min(1.0, main.sd / 0.3))
        return round(sum(terms) / len(terms), 4)

    def vulnerable_share(self):
        n = self.mean("people")
        return 0.0 if n <= 0 else min(1.0, self.mean("vulnerable") / n)

    def summary(self):
        r = lambda x: round(x, 2)
        return {
            "category": self.category["id"],
            "people": [r(self.mean("people")), r(self.sd("people"))],
            "injured": [r(self.mean("injured")), r(self.sd("injured"))],
            "critical": [r(self.mean("critical")), r(self.sd("critical"))],
            "vulnerable": [r(self.mean("vulnerable")), r(self.sd("vulnerable"))],
            "immobile": [r(self.mean("immobile")), r(self.sd("immobile"))],
            "hazards": {h: r(g.mean) for h, g in self.hazards.items() if g.mean >= 0.05},
            "shortages": {s: r(g.mean) for s, g in self.shortages.items() if g.mean >= 0.2},
            "trend": self.trend_label, "p_worsening": r(self.p_worsening),
            "uncertainty": self.uncertainty(), "evidence": self.evidence,
        }
