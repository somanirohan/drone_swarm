"""Build assets/operations/agency-catalog.json from a DisruptionOps migration.

Reads the PCMC/PCCOE zone migration (DisruptionOps ``backend/migrations/029_pcmc_pccoe_zone.sql``,
reference commit bd21179) read-only and converts its resources, lifelines and wards into
simulator-local metres around the map anchor.

    python tools/build_agency_catalog.py --source path/to/029_pcmc_pccoe_zone.sql
    python tools/build_agency_catalog.py --source ... --output some/other.json --quiet
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repository root (tools/..)
DEFAULT_OUTPUT = ROOT / "assets" / "operations" / "agency-catalog.json"
ANCHOR = (18.6526632, 73.7624378)  # lat, lon (same as swarm_ops.engine.ANCHOR)
KM_LAT, KM_LNG = 110.57, 105.6
HALF_EXTENT_M = 600


def local_xy(lng: float, lat: float) -> list[float]:
    """Simulator metres: x east, y north of the anchor."""
    return [round((lng - ANCHOR[1]) * KM_LNG * 1000, 1), round((lat - ANCHOR[0]) * KM_LAT * 1000, 1)]


def _block(sql: str, name: str) -> str:
    m = re.search(r"insert into %s values\n(.*?);\n" % name, sql, re.S)
    if not m:
        raise ValueError(f"insert block {name!r} not found in the migration")
    return m.group(1)


def build(sql: str) -> dict:
    sql = sql.replace("\r\n", "\n")
    resources = []
    for line in _block(sql, "pc_r").splitlines():
        m = re.match(r"\s*\('([^']+)','([^']+)','([^']+)','([^']+)','([^']+)',(\d+),([\d.]+),([\d.]+)\)", line)
        if not m:
            continue
        rid, kind, label, operator, agency, cap, lng, lat = m.groups()
        resources.append({"source_id": rid, "kind": kind, "name": label, "operator": operator, "agency_id": agency,
                          "capacity": int(cap), "lng": float(lng), "lat": float(lat),
                          "local_xy": local_xy(float(lng), float(lat))})
    lifelines = []
    for line in _block(sql, "pc_l").splitlines():
        m = re.match(r"\s*\('([^']+)','([^']+)','((?:[^']|'')+)',([\d.]+),([\d.]+),(null|\d+),", line)
        if not m:
            continue
        lid, kind, name, lng, lat, cap = m.groups()
        lifelines.append({"source_id": lid, "kind": kind, "name": name.replace("''", "'"),
                          "capacity": None if cap == "null" else int(cap), "lng": float(lng), "lat": float(lat),
                          "local_xy": local_xy(float(lng), float(lat))})
    wards = []
    for m in re.finditer(r"\('(w-pc-\d+)','(P\d+)','([^']+)',\s*([\d.]+),\s*([\d.]+),\s*(\d+),", sql):
        wid, num, name, lng, lat, pop = m.groups()
        wards.append({"source_id": wid, "number": num, "name": name, "lng": float(lng), "lat": float(lat),
                      "population": int(pop), "local_xy": local_xy(float(lng), float(lat))})
    agencies = [{"id": "pcmc", "name": "Pimpri Chinchwad Municipal Corporation"},
                {"id": "pcmcfire", "name": "PCMC Fire Brigade"}]
    for group in (resources, lifelines):
        for item in group:
            x, y = item["local_xy"]
            item["on_map"] = abs(x) <= HALF_EXTENT_M and abs(y) <= HALF_EXTENT_M
            item["distance_from_anchor_m"] = round(math.hypot(x, y))
    return {
        "source": "DisruptionOps backend/migrations/029_pcmc_pccoe_zone.sql @ GitHub officialayush23/DisruptionOps main bd21179 (read-only copy; DisruptionOps not modified or contacted)",
        "anchor": {"lat": ANCHOR[0], "lng": ANCHOR[1], "note": "PCCOE, Akurdi-Nigdi; same point as the simulator map anchor"},
        "map_half_extent_m": HALF_EXTENT_M,
        "note": "local_xy = metres east/north of the anchor. Units with on_map=false start outside the 1.2 km simulated district: the engine must bring them in from the map edge with the extra real travel time (distance_from_anchor_m), not teleport them onto a road.",
        "agencies": agencies, "wards": wards, "resources": resources, "lifelines": lifelines,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True,
                        help="path to DisruptionOps backend/migrations/029_pcmc_pccoe_zone.sql")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"default: {DEFAULT_OUTPUT}")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    out = build(args.source.read_text(encoding="utf8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf8")
    if not args.quiet:
        res, life = out["resources"], out["lifelines"]
        print(len(res), "resources", sum(r["on_map"] for r in res), "on map;", len(life), "lifelines",
              sum(item["on_map"] for item in life), "on map;", len(out["wards"]), "wards ->", args.output)
        for r in res:
            if r["on_map"]:
                print(" on map:", r["source_id"], r["kind"], r["local_xy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
