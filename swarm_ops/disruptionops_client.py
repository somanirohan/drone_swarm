"""Reference data for the swarm: live from DisruptionOps when configured, otherwise from the shipped assets.

``load_reference_data()`` is the single loader the engine should call. It always returns the same shape:

    {
      "mode": "live" | "assets" | "assets-fallback",
      "taxonomy": {...},   # same shape as assets/operations/disruptionops-taxonomy.json
      "catalog":  {...},   # same shape as assets/operations/agency-catalog.json (PCMC units/lifelines/wards)
      "incidents": [...],  # open DisruptionOps incidents, local metres added (live mode only, else [])
      "fetched_at": float, "source": str, "errors": [str, ...]
    }

Live mode is on when ``DISRUPTIONOPS_URL`` is set. Endpoints used (DisruptionOps ``backend/app``; router prefix
``/api/v1`` at main.py:156):

    GET /api/v1/taxonomy?city_id=pune   public        api/v1/runs.py:47
    GET /api/v1/wards                   public        api/v1/geography.py:13
    GET /api/v1/lifelines               public        api/v1/geography.py:18
    GET /api/v1/incidents               any caller    api/v1/operations.py:30  (anonymous allowed)
    GET /api/v1/resources               STAFF bearer  api/v1/operations.py:35

Auth is ``Authorization: Bearer <DISRUPTIONOPS_TOKEN>`` (a staff-role Supabase JWT, core/security.py:111-132). Without
a token the staff-only ``/resources`` call is skipped and units come from the asset catalog. The taxonomy endpoint
does not expose ``effectiveness`` per (kind, capability) (schemas/domain.py:422-426 gives capability ids only), so the
effectiveness values are merged in from the shipped taxonomy asset, defaulting to 1.0 for kinds it does not know.

Every failure falls back to the assets and is logged; nothing here raises to the caller. Only the standard library
is used. Never point tests at a real deployment: they use a local stub server.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("swarm_ops.disruptionops")

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets" / "operations"
TAXONOMY_PATH = ASSETS / "disruptionops-taxonomy.json"
CATALOG_PATH = ASSETS / "agency-catalog.json"

ANCHOR = (18.6526632, 73.7624378)   # lat, lon (same as swarm_ops.engine.ANCHOR)
KM_LAT, KM_LNG = 110.57, 105.6      # same projection as tools/build_agency_catalog.py
HALF_EXTENT_M = 600
#: Only the PCMC / PCCOE zone (migration 029) is simulated; everything else DisruptionOps knows is dropped.
PCMC_PREFIXES = ("w-pc-", "lf-pc-", "PC-")
CACHE_TTL_S = 300.0
TIMEOUT_S = 6.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


# ------------------------------------------------------------------ assets --
def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf8"))


def load_assets() -> dict:
    taxonomy = _read_json(TAXONOMY_PATH) if TAXONOMY_PATH.exists() else {}
    catalog = _read_json(CATALOG_PATH) if CATALOG_PATH.exists() else {}
    return {"mode": "assets", "taxonomy": taxonomy, "catalog": catalog, "incidents": [],
            "fetched_at": time.time(), "source": "assets/operations", "errors": []}


# --------------------------------------------------------------------- http --
class LiveError(RuntimeError):
    pass


def _get(base: str, path: str, token: str = "", timeout: float = TIMEOUT_S) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "swarm-ops/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(Request(base + path, headers=headers), timeout=timeout) as response:  # noqa: S310 (operator-set URL)
            return json.loads(response.read().decode("utf8"))
    except HTTPError as exc:
        raise LiveError(f"GET {path} -> HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise LiveError(f"GET {path} failed: {type(exc).__name__}") from exc


def local_xy(lng: float, lat: float) -> list[float]:
    return [round((lng - ANCHOR[1]) * KM_LNG * 1000, 1), round((lat - ANCHOR[0]) * KM_LAT * 1000, 1)]


# ------------------------------------------------------------- conversions --
def convert_taxonomy(api: dict, asset: dict) -> dict:
    """DisruptionOps GET /taxonomy (camelCase) -> the asset shape (snake_case, effectiveness, keyword list)."""
    eff = {k["id"]: k.get("capabilities", {}) for k in asset.get("resource_kinds", [])}
    kinds = []
    for k in api.get("resourceKinds", []):
        caps = {c: float(eff.get(k["id"], {}).get(c, 1.0)) for c in k.get("capabilities", [])}
        kinds.append({"id": k["id"], "display_name": k.get("displayName", k["id"]),
                      "default_capacity": k.get("defaultCapacity", 1), "capabilities": caps})
    categories = []
    for c in api.get("incidentCategories", []):
        categories.append({
            "id": c["id"], "display_name": c.get("displayName", c["id"]), "hazard_id": c.get("hazardId"),
            "dedup_radius_m": c.get("dedupRadiusM", 250), "dedup_window_min": c.get("dedupWindowMin", 45),
            "base_severity": c.get("baseSeverity", 3), "life_safety": bool(c.get("lifeSafety", False)),
            "needs": dict(c.get("needs", {})),
            "keywords": [{"phrase": p, "weight": float(w)} for p, w in (c.get("keywords") or {}).items()]})
    kinds_providing: dict[str, dict[str, float]] = {}
    for k in kinds:
        for cap, e in k["capabilities"].items():
            kinds_providing.setdefault(cap, {})[k["id"]] = e
    lifeline_assets = {k["id"]: k for k in asset.get("lifeline_kinds", [])}
    out = {
        "source": {"repo": "live", "commit": "live", "note": "GET /api/v1/taxonomy"},
        "hazard_types": [{"id": h["id"], "display_name": h.get("displayName", h["id"]), "sort_order": h.get("sortOrder", 100)}
                         for h in api.get("hazards", [])],
        "capabilities": [{"id": c["id"], "label": c.get("label", c["id"]), "description": c.get("description", "")}
                         for c in api.get("capabilities", [])],
        "resource_kinds": kinds,
        "incident_categories": categories,
        "lifeline_kinds": [{"id": k["id"], "display_name": k.get("displayName", k["id"]),
                            "shelters_people": bool(k.get("sheltersPeople", False)),
                            "serves_public": lifeline_assets.get(k["id"], {}).get("serves_public", True)}
                           for k in api.get("lifelineKinds", [])],
        "agencies": [{"id": a["id"], "name": a.get("name", a["id"]), "short_name": a.get("shortName"),
                      "kind": a.get("kind", "government"), "jurisdiction": a.get("jurisdiction", ""),
                      "city_id": a.get("cityId", "pune"), "capabilities": sorted(a.get("capabilities", []))}
                     for a in api.get("agencies", [])],
        "kinds_providing": kinds_providing,
    }
    out["counts"] = {"hazard_types": len(out["hazard_types"]), "capabilities": len(out["capabilities"]),
                     "resource_kinds": len(kinds), "incident_categories": len(categories),
                     "lifeline_kinds": len(out["lifeline_kinds"]), "agencies": len(out["agencies"]),
                     "keywords": sum(len(c["keywords"]) for c in categories)}
    return out


def _decorate(item: dict) -> dict:
    x, y = item["local_xy"]
    item["on_map"] = abs(x) <= HALF_EXTENT_M and abs(y) <= HALF_EXTENT_M
    item["distance_from_anchor_m"] = round(math.hypot(x, y))
    return item


def convert_catalog(wards: list, lifelines: list, resources: list | None, asset_catalog: dict, agencies: list) -> dict:
    """Live wards / lifelines / resources (camelCase) -> agency-catalog.json shape, PCMC zone only."""
    by_agency_name = {a["name"]: a["id"] for a in agencies}
    asset_agency = {r["source_id"]: r.get("agency_id") for r in asset_catalog.get("resources", [])}
    out_wards = []
    for w in wards:
        if not str(w.get("id", "")).startswith(PCMC_PREFIXES):
            continue
        lng, lat = w["centroid"]
        out_wards.append({"source_id": w["id"], "number": w.get("number"), "name": w.get("name"), "lng": lng, "lat": lat,
                          "population": w.get("population"), "local_xy": local_xy(lng, lat)})
    out_life = []
    for l in lifelines:
        if not str(l.get("id", "")).startswith(PCMC_PREFIXES):
            continue
        lng, lat = l["location"]
        out_life.append(_decorate({"source_id": l["id"], "kind": l["kind"], "name": l["name"], "capacity": l.get("capacity"),
                                   "lng": lng, "lat": lat, "local_xy": local_xy(lng, lat)}))
    if resources is None:   # no staff token: keep the shipped unit list
        out_res = asset_catalog.get("resources", [])
    else:
        out_res = []
        for r in resources:
            if not str(r.get("id", "")).startswith(PCMC_PREFIXES):
                continue
            lng, lat = r["baseLocation"]
            out_res.append(_decorate({
                "source_id": r["id"], "kind": r["kind"], "name": r["label"], "operator": r.get("operator", ""),
                "agency_id": asset_agency.get(r["id"]) or by_agency_name.get(r.get("operator", "")),
                "capacity": r.get("capacity", 1), "lng": lng, "lat": lat, "local_xy": local_xy(lng, lat),
                "status": r.get("status")}))
    return {"source": "live GET /api/v1/{wards,lifelines,resources}", "anchor": asset_catalog.get("anchor"),
            "map_half_extent_m": HALF_EXTENT_M, "note": asset_catalog.get("note", ""),
            "agencies": [a for a in asset_catalog.get("agencies", [])] or [{"id": a["id"], "name": a["name"]} for a in agencies if a["id"].startswith("pc")],
            "wards": out_wards, "resources": out_res, "lifelines": out_life}


def convert_incidents(api: list) -> list[dict]:
    out = []
    for i in api:
        if i.get("status") == "resolved":
            continue
        lng, lat = i["location"]
        out.append({"id": i["id"], "title": i.get("title"), "category": i.get("category"), "hazard": i.get("hazard"),
                    "ward_id": i.get("wardId"), "severity": i.get("severity"), "status": i.get("status"),
                    "report_count": i.get("reportCount"), "lng": lng, "lat": lat, "local_xy": local_xy(lng, lat)})
    return out


# ------------------------------------------------------------------- loader --
def fetch_live(url: str, token: str = "", city_id: str = "pune", timeout: float = TIMEOUT_S) -> dict:
    base = url.rstrip("/") + "/api/v1"
    assets = load_assets()
    errors: list[str] = []
    taxonomy_api = _get(base, f"/taxonomy?city_id={city_id}", token, timeout)   # required: failure => fallback
    taxonomy = convert_taxonomy(taxonomy_api, assets["taxonomy"])
    wards = _get(base, "/wards", token, timeout)
    lifelines = _get(base, "/lifelines", token, timeout)
    resources = None
    if token:
        try:
            resources = _get(base, "/resources", token, timeout)
        except LiveError as exc:
            errors.append(f"{exc}; using asset units")
    else:
        errors.append("no DISRUPTIONOPS_TOKEN: /resources is staff-only, using asset units")
    incidents: list[dict] = []
    try:
        incidents = convert_incidents(_get(base, "/incidents", token, timeout))
    except LiveError as exc:
        errors.append(str(exc))
    catalog = convert_catalog(wards, lifelines, resources, assets["catalog"], taxonomy["agencies"])
    return {"mode": "live", "taxonomy": taxonomy, "catalog": catalog, "incidents": incidents,
            "fetched_at": time.time(), "source": url, "errors": errors}


def load_reference_data(settings=None, *, force: bool = False, ttl: float = CACHE_TTL_S) -> dict:
    """The single loader. ``settings`` is a swarm_ops.config.Settings (default: read the environment)."""
    if settings is None:
        from swarm_ops.config import Settings
        settings = Settings.from_env()
    url, token = settings.disruptionops_url, settings.disruptionops_token
    if not url:
        return load_assets()
    key = (url, bool(token))
    with _lock:
        if not force and _cache["key"] == key and _cache["value"] and time.time() - _cache["at"] < ttl:
            return _cache["value"]
    try:
        value = fetch_live(url, token)
        log.info("reference data loaded live from %s (%s)", url, value["taxonomy"]["counts"])
    except LiveError as exc:
        log.warning("DisruptionOps unreachable or refused (%s); using shipped assets", exc)
        value = load_assets()
        value.update(mode="assets-fallback", errors=[str(exc)], source=f"{url} (unreachable)")
        with _lock:    # cache failures briefly so a dead upstream is not hammered
            _cache.update(key=key, at=time.time() - ttl + 30, value=value)
        return value
    with _lock:
        _cache.update(key=key, at=time.time(), value=value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.update(key=None, at=0.0, value=None)
