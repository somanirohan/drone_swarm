"""Build assets/operations/disruptionops-taxonomy.json from DisruptionOps migrations.

Reads ``backend/migrations/*.sql`` (all of them, in numeric order) of a DisruptionOps checkout
read-only and replays the taxonomy ``insert`` statements, honouring ``on conflict ... do update``
(later value wins) and ``do nothing`` (first value wins), plus the one taxonomy ``update``
(migration 007, ``lifeline_kinds.serves_public``).

    python tools/build_taxonomy.py --source D:/drone_research_reference/DisruptionOps-latest
    python tools/build_taxonomy.py --source <checkout> --check     # exit 1 if the JSON is stale
    python tools/build_taxonomy.py --source <checkout> --catalog   # also refresh agency-catalog.json
                                                                   # (only if migration 029 changed)

Only the Python standard library is used. DisruptionOps is never modified or contacted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "assets" / "operations" / "disruptionops-taxonomy.json"
UPSTREAM = "https://github.com/officialayush23/DisruptionOps"

# table -> (columns forming the primary key)
TABLE_KEYS = {
    "hazard_types": ("id",),
    "capabilities": ("id",),
    "resource_kinds": ("id",),
    "resource_kind_capabilities": ("kind_id", "capability_id"),
    "lifeline_kinds": ("id",),
    "incident_categories": ("id",),
    "incident_category_needs": ("category_id", "capability_id"),
    "incident_category_keywords": ("category_id", "phrase"),
    "agencies": ("id",),
    "agency_capabilities": ("agency_id", "capability_id"),
}


def strip_comments(sql: str) -> str:
    """Remove -- line comments, string-aware (a quote inside a comment must not open a string)."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif c == "-" and sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def parse_tuple(s: str, i: int):
    """Parse '(' value, ... ')' starting at s[i]=='('; returns (list, next_index)."""
    assert s[i] == "("
    i += 1
    values = []
    while True:
        i = _skip_ws(s, i)
        c = s[i]
        if c == "'":
            j, buf = i + 1, []
            while True:
                if s[j] == "'":
                    if j + 1 < len(s) and s[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(s[j])
                j += 1
            values.append("".join(buf))
            i = j + 1
        else:
            m = re.compile(r"[^,)\s]+").match(s, i)
            tok = m.group(0)
            low = tok.lower()
            if low == "null":
                val = None
            elif low in ("true", "false"):
                val = low == "true"
            else:
                try:
                    val = int(tok)
                except ValueError:
                    try:
                        val = float(tok)
                    except ValueError:
                        raise ValueError(f"unsupported SQL literal {tok!r}") from None
            values.append(val)
            i = m.end()
        i = _skip_ws(s, i)
        # tolerate a type cast such as 'x'::text
        if s.startswith("::", i):
            m = re.compile(r"::[a-z_ ]+").match(s, i)
            i = m.end()
            i = _skip_ws(s, i)
        if s[i] == ",":
            i += 1
            continue
        if s[i] == ")":
            return values, i + 1
        raise ValueError(f"unexpected {s[i:i + 30]!r}")


INSERT_RE = re.compile(
    r"insert\s+into\s+(?:public\.)?(\w+)\s*\(([^)]*)\)\s*values\s*", re.I)


def replay(files: list[tuple[str, str]]) -> dict[str, dict[tuple, dict]]:
    """Return {table: {primary_key_tuple: row}} after replaying every migration in order."""
    state: dict[str, dict[tuple, dict]] = {t: {} for t in TABLE_KEYS}
    for name, raw in files:
        sql = strip_comments(raw.replace("\r\n", "\n"))
        for m in INSERT_RE.finditer(sql):
            table = m.group(1).lower()
            if table not in TABLE_KEYS:
                continue
            cols = [c.strip().lower() for c in m.group(2).split(",")]
            pos, rows = m.end(), []
            while True:
                pos = _skip_ws(sql, pos)
                if sql[pos] != "(":
                    break
                row, pos = parse_tuple(sql, pos)
                if len(row) != len(cols):
                    raise ValueError(f"{name}: {table} row has {len(row)} values for {len(cols)} columns")
                rows.append(dict(zip(cols, row)))
                pos = _skip_ws(sql, pos)
                if sql[pos] == ",":
                    pos += 1
                    continue
                break
            tail = sql[pos:pos + 400]
            conflict = re.match(r"\s*on\s+conflict\s*(\([^)]*\))?\s*do\s+(nothing|update\s+set\s+([^;]*))", tail, re.I)
            upsert_cols: list[str] = []
            if conflict and conflict.group(2).lower().startswith("update"):
                upsert_cols = re.findall(r"(\w+)\s*=\s*excluded\.\w+", conflict.group(3) or "")
            for row in rows:
                key = tuple(row[k] for k in TABLE_KEYS[table])
                existing = state[table].get(key)
                if existing is None:
                    state[table][key] = row
                    row["_migration"] = name
                elif upsert_cols:
                    for c in upsert_cols:
                        existing[c] = row.get(c)
                    existing["_migration_updated"] = name
        # The single taxonomy UPDATE in the series (007): lifeline_kinds.serves_public.
        for um in re.finditer(r"update\s+(?:public\.)?lifeline_kinds\s+set\s+serves_public\s*=\s*(true|false)\s+where\s+id\s+in\s*\(([^)]*)\)", sql, re.I):
            value = um.group(1).lower() == "true"
            for kid in re.findall(r"'([^']+)'", um.group(2)):
                row = state["lifeline_kinds"].get((kid,))
                if row:
                    row["serves_public"] = value
    return state


def build(migrations_dir: Path, commit: str) -> dict:
    paths = sorted(p for p in migrations_dir.glob("*.sql") if re.match(r"\d{3}_", p.name))
    if not paths:
        raise SystemExit(f"no migrations found in {migrations_dir}")
    files = [(p.name, p.read_text(encoding="utf8")) for p in paths]
    digest = hashlib.sha256()
    for name, text in files:
        digest.update(name.encode() + b"\0" + text.replace("\r\n", "\n").encode())
    st = replay(files)
    rows = lambda t: [r for r in st[t].values()]  # noqa: E731

    caps_of_kind: dict[str, dict[str, float]] = {}
    for r in rows("resource_kind_capabilities"):
        caps_of_kind.setdefault(r["kind_id"], {})[r["capability_id"]] = float(r["effectiveness"])
    needs_of: dict[str, dict[str, int]] = {}
    for r in rows("incident_category_needs"):
        needs_of.setdefault(r["category_id"], {})[r["capability_id"]] = int(r["qty_per_incident"])
    kw_of: dict[str, list] = {}
    for r in rows("incident_category_keywords"):
        kw_of.setdefault(r["category_id"], []).append({"phrase": r["phrase"], "weight": float(r["weight"])})
    agcaps: dict[str, list] = {}
    for r in rows("agency_capabilities"):
        agcaps.setdefault(r["agency_id"], []).append(r["capability_id"])

    hazards = sorted(({"id": r["id"], "display_name": r["display_name"], "sort_order": r.get("sort_order", 100)}
                      for r in rows("hazard_types")), key=lambda h: (h["sort_order"], h["id"]))
    capabilities = [{"id": r["id"], "label": r["label"], "description": r.get("description", "")}
                    for r in rows("capabilities")]
    kinds = [{"id": r["id"], "display_name": r["display_name"], "default_capacity": r.get("default_capacity", 1),
              "capabilities": caps_of_kind.get(r["id"], {})} for r in rows("resource_kinds")]
    categories = [{
        "id": r["id"], "display_name": r["display_name"], "hazard_id": r.get("hazard_id"),
        "dedup_radius_m": r.get("dedup_radius_m", 250), "dedup_window_min": r.get("dedup_window_min", 45),
        "base_severity": r.get("base_severity", 3), "life_safety": bool(r.get("life_safety", False)),
        "needs": needs_of.get(r["id"], {}), "keywords": kw_of.get(r["id"], []),
    } for r in rows("incident_categories")]
    lifeline_kinds = [{"id": r["id"], "display_name": r["display_name"],
                       "shelters_people": bool(r.get("shelters_people", False)),
                       "serves_public": bool(r.get("serves_public", True))} for r in rows("lifeline_kinds")]
    agencies = [{"id": r["id"], "name": r["name"], "short_name": r.get("short_name"), "kind": r.get("kind", "government"),
                 "jurisdiction": r.get("jurisdiction", ""), "city_id": r.get("city_id", "pune"),
                 "capabilities": sorted(agcaps.get(r["id"], []))} for r in rows("agencies")]
    # Reverse index the swarm engine wants: which resource kinds can serve a capability.
    kinds_providing: dict[str, dict[str, float]] = {}
    for k in kinds:
        for cap, eff in k["capabilities"].items():
            kinds_providing.setdefault(cap, {})[k["id"]] = eff
    return {
        "source": {"repo": UPSTREAM, "commit": commit, "migrations": [p.name for p in paths][0] + ".." + paths[-1].name,
                   "migration_count": len(paths), "migrations_sha256": digest.hexdigest(),
                   "note": "Derived read-only from DisruptionOps backend/migrations; regenerate with tools/build_taxonomy.py."},
        "hazard_types": hazards,
        "capabilities": capabilities,
        "resource_kinds": kinds,
        "incident_categories": categories,
        "lifeline_kinds": lifeline_kinds,
        "agencies": agencies,
        "kinds_providing": kinds_providing,
        "counts": {"hazard_types": len(hazards), "capabilities": len(capabilities), "resource_kinds": len(kinds),
                   "incident_categories": len(categories), "lifeline_kinds": len(lifeline_kinds),
                   "agencies": len(agencies), "keywords": sum(len(c["keywords"]) for c in categories)},
    }


def git_commit(source: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, required=True, help="DisruptionOps checkout (contains backend/migrations)")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--commit", default=None, help="source commit (default: git rev-parse HEAD of --source)")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if output differs")
    ap.add_argument("--catalog", action="store_true", help="also rebuild agency-catalog.json from migration 029")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    mdir = args.source / "backend" / "migrations"
    data = build(mdir, args.commit or git_commit(args.source))
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf8") if args.output.exists() else ""
        return 0 if current == text else 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf8")
    if args.catalog:
        sys.path.insert(0, str(Path(__file__).parent))
        import build_agency_catalog
        sql = (mdir / "029_pcmc_pccoe_zone.sql").read_text(encoding="utf8")
        catalog = build_agency_catalog.build(sql)
        catalog["source"] = f"DisruptionOps backend/migrations/029_pcmc_pccoe_zone.sql @ {data['source']['commit']} (read-only; DisruptionOps not modified or contacted)"
        target = build_agency_catalog.DEFAULT_OUTPUT
        target.write_text(json.dumps(catalog, indent=1, ensure_ascii=False) + "\n", encoding="utf8")
    if not args.quiet:
        print(json.dumps(data["counts"]), "->", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
