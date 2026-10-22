"""Build assets/operations/pcmc-region.json: the PCMC zone round PCCOE as a simulator world.

Read-only inputs from a DisruptionOps checkout (never modified):
  backend/migrations/029_pcmc_pccoe_zone.sql   wards, 32 PC-* units, 16 lifelines, the envelope
  backend/migrations/030_hazard_sector_agent_log.sql   sectors (hazard tags) per ward
  backend/migrations/032_unit_tracking_and_teams.sql   4 PC-POL police units
  data/raw/osm/{roads,water,context}.json      Overpass extracts for the same envelope

Output: one JSON world in local metres east/north of the PCCOE anchor (same projection as
tools/build_agency_catalog.py), with the pune-world.json keys plus world.kind='region',
world.id='pcmc-region', wards, lifelines, units, stations, dock_sites, hotspots and a
pre-split road network (every junction is a polyline end point, so no segment
intersection pass is needed).

    python tools/prepare_pcmc_region.py --source D:/drone_research_reference/DisruptionOps-latest
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "assets" / "operations" / "pcmc-region.json"
_spec = importlib.util.spec_from_file_location("build_agency_catalog", Path(__file__).with_name("build_agency_catalog.py"))
_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_catalog)
ANCHOR, local_xy = _catalog.ANCHOR, _catalog.local_xy

ENVELOPE = (73.715, 18.585, 73.845, 18.712)  # W S E N, migration 029
MAJOR = {"motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary",
         "secondary_link", "tertiary", "tertiary_link", "unclassified"}
MINOR = {"residential", "living_street"}
SERVICE = {"service"}
WIDTH = {"motorway": 16, "trunk": 14, "primary": 12, "secondary": 10, "tertiary": 8, "unclassified": 6,
         "residential": 6, "living_street": 5, "service": 4}
RIVER_WIDTH = {"Pavana River": 50, "Mula": 80, "Indrayani river": 60, "Kasarsai river": 25}
WATERWAY_WIDTH = {"river": 40, "canal": 10, "stream": 6, "drain": 4, "ditch": 3}

# Real places the PCMC scenario uses (lat, lng from OSM/migration 029; see docs/implementation/PCMC-REGION.md).
# ward = the DisruptionOps ward named in scenarios.py; snap: road | river | point.
HOTSPOTS = [
    ("ravet-bridge", "Pawana bank by Ravet bridge (Ravet bandhara)", 18.6418, 73.7466, "w-pc-03", "river"),
    ("morya-ghat", "Morya Gosavi ghat lanes, Chinchwad Gaon", 18.62632, 73.77844, "w-pc-05", "river"),
    ("akurdi-underpass", "Akurdi railway station underpass", 18.64899, 73.76535, "w-pc-01", "road"),
    ("pimpri-camp", "Pimpri camp market lanes by the Pawana", 18.6240, 73.7985, "w-pc-07", "road"),
    ("kudalwadi-chemical", "Chemical unit, Chikhli-Kudalwadi industrial pocket", 18.6745, 73.8220, "w-pc-11", "road"),
    ("sane-chowk", "Sane chowk, Chikhli", 18.6660, 73.8075, "w-pc-11", "road"),
    ("nigdi-wall", "Housing society compound wall, Nigdi Pradhikaran", 18.6625, 73.7800, "w-pc-02", "road"),
    ("pccoe-lane", "Waterlogged lane south of PCCOE gate 1", 18.6512, 73.7632, "w-pc-01", "road"),
    ("birla-marg", "Aditya Birla Hospital Marg, Thergaon side", 18.6180, 73.7740, "w-pc-08", "road"),
    ("sb-patil-shelter", "S B Patil Public School shelter, Ravet", 18.65596, 73.74602, "w-pc-03", "point"),
    ("ravet-relief-camp", "Ravet relief camp (D Y Patil Dnyanshanti School)", 18.64756, 73.75948, "w-pc-03", "point"),
]
# Drone docks at real places: PCCOE campus, the PCMC fire stations, two hospitals.
DOCK_SITES = [
    ("DOCK-PCCOE", "PCCOE campus, Akurdi", 18.65267, 73.76238),
    ("DOCK-NIGDI-FS", "Pradhikaran fire station, Nigdi", 18.65925, 73.77174),
    ("DOCK-PIMPRI-FS", "Main PCMC fire station, Pimpri", 18.62276, 73.81805),
    ("DOCK-THERGAON-FS", "Thergaon fire station", 18.61219, 73.77881),
    ("DOCK-RAHATANI-FS", "Rahatani fire station", 18.59624, 73.78172),
    ("DOCK-CHIKHALI-FS", "Chikhali fire station (MIDC)", 18.66441, 73.81780),
    ("DOCK-YCM", "YCM Hospital, Pimpri", 18.62203, 73.82116),
    ("DOCK-RAVET-GH", "Global Hospital, Ravet", 18.64646, 73.75716),
]
# Places the Pawana is expected over its banks (migration 029 hazard setting): reach (m along the river
# either side of the hotspot). Each reach becomes circles of FLOOD_BANK_M every FLOOD_STEP_M on the centreline.
FLOOD_ZONES = [("flood-ravet", "Pawana at Ravet", "ravet-bridge", 700), ("flood-chinchwad", "Pawana at Chinchwad Gaon", "morya-ghat", 600),
               ("flood-pimpri", "Pawana at Pimpri camp", "pimpri-camp", 500)]
FLOOD_BANK_M, FLOOD_STEP_M = 100, 120
# One-way drone reach from a dock (engine energy model: 110 Wh * 0.9 >= round trip * 0.018 Wh/m + 29 Wh -> ~1.9 km).
DOCK_REACH_M, MAX_DOCKS = 1800, 24


# ----------------------------------------------------------------- geometry ---
def xy(lat, lng):
    return local_xy(lng, lat)


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def inside(p, poly):
    result, j = False, len(poly) - 1
    for i, q in enumerate(poly):
        r = poly[j]
        if (q[1] > p[1]) != (r[1] > p[1]) and p[0] < (r[0] - q[0]) * (p[1] - q[1]) / (r[1] - q[1]) + q[0]:
            result = not result
        j = i
    return result


def clip_halfplane(poly, keep):
    """Sutherland-Hodgman against one half plane; keep(p) -> signed value >= 0 inside."""
    out = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        fc, fp = keep(cur), keep(prev)
        if fc >= 0:
            if fp < 0:
                t = fp / (fp - fc)
                out.append([prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])])
            out.append(list(cur))
        elif fp >= 0:
            t = fp / (fp - fc)
            out.append([prev[0] + t * (cur[0] - prev[0]), prev[1] + t * (cur[1] - prev[1])])
    return out


def clip_rect(poly, b):
    x0, y0, x1, y1 = b
    for keep in (lambda p: p[0] - x0, lambda p: x1 - p[0], lambda p: p[1] - y0, lambda p: y1 - p[1]):
        if not poly:
            break
        poly = clip_halfplane(poly, keep)
    return poly


def clip_polyline(points, b):
    """Split a polyline into runs inside rectangle b (Liang-Barsky per segment)."""
    runs, run = [], []
    for a, c in zip(points, points[1:]):
        t0, t1, dx, dy = 0.0, 1.0, c[0] - a[0], c[1] - a[1]
        ok = True
        for p, q in ((-dx, a[0] - b[0]), (dx, b[2] - a[0]), (-dy, a[1] - b[1]), (dy, b[3] - a[1])):
            if p == 0:
                if q < 0:
                    ok = False
                    break
            else:
                t = q / p
                if p < 0:
                    t0 = max(t0, t)
                else:
                    t1 = min(t1, t)
        if not ok or t0 > t1:
            if len(run) > 1:
                runs.append(run)
            run = []
            continue
        s = [a[0] + t0 * dx, a[1] + t0 * dy]
        e = [a[0] + t1 * dx, a[1] + t1 * dy]
        if not run or dist(run[-1], s) > 1e-6:
            if len(run) > 1:
                runs.append(run)
            run = [s]
        run.append(e)
        if t1 < 1:
            runs.append(run)
            run = []
    if len(run) > 1:
        runs.append(run)
    return runs


def simplify(points, tol):
    """Douglas-Peucker keeping both ends."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = points[i], points[j]
        best, idx = -1.0, None
        for k in range(i + 1, j):
            d = seg_dist(points[k], a, b)[0]
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(points, keep) if k]


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
    q = [a[0] + t * dx, a[1] + t * dy]
    return dist(p, q), q


def seg_intersection(a, b, c, d):
    rx, ry, sx, sy = b[0] - a[0], b[1] - a[1], d[0] - c[0], d[1] - c[1]
    cross = rx * sy - ry * sx
    if abs(cross) < 1e-9:
        return None
    t = ((c[0] - a[0]) * sy - (c[1] - a[1]) * sx) / cross
    u = ((c[0] - a[0]) * ry - (c[1] - a[1]) * rx) / cross
    return [a[0] + t * rx, a[1] + t * ry] if 0 <= t <= 1 and 0 <= u <= 1 else None


def area(poly):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(poly, poly[1:] + poly[:1]))) / 2


def r1(p):
    return [round(p[0], 1), round(p[1], 1)]


# ------------------------------------------------------------------- inputs ---
def load_osm(path):
    data = json.loads(path.read_text(encoding="utf8"))
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node" and "lat" in e}
    return data["elements"], nodes


def parse_wards(sql):
    wards = []
    for m in re.finditer(r"\('(w-pc-\d+)','(P\d+)','([^']+)',\s*([\d.]+),\s*([\d.]+),\s*(\d+),\s*([\d.]+),\s*([\d.]+)\)", sql):
        wid, num, name, lng, lat, pop, eld, elev = m.groups()
        wards.append({"id": wid, "number": num, "name": name, "lng": float(lng), "lat": float(lat), "population": int(pop),
                      "elderly_share": float(eld), "elevation_m": float(elev)})
    return wards


def voronoi_wards(wards, bounds):
    """Ward cells exactly as migration 029: Voronoi in lng/lat degrees, clipped to the envelope, then projected
    (the projection is affine, so the cells map exactly)."""
    env = [[ENVELOPE[0], ENVELOPE[1]], [ENVELOPE[2], ENVELOPE[1]], [ENVELOPE[2], ENVELOPE[3]], [ENVELOPE[0], ENVELOPE[3]]]
    for w in wards:
        cell = [list(p) for p in env]
        a = (w["lng"], w["lat"])
        for o in wards:
            if o is w:
                continue
            b = (o["lng"], o["lat"])
            mx, my, nx, ny = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, b[0] - a[0], b[1] - a[1]
            cell = clip_halfplane(cell, lambda p, mx=mx, my=my, nx=nx, ny=ny: -((p[0] - mx) * nx + (p[1] - my) * ny))
        poly = [r1(local_xy(p[0], p[1])) for p in cell]
        w["polygon"] = poly
        w["centroid"] = local_xy(w["lng"], w["lat"])
        w["area_sq_km"] = round(area(poly) / 1e6, 2)
    return wards


def ward_of(p, wards):
    for w in wards:
        if inside(p, w["polygon"]):
            return w["id"]
    return min(wards, key=lambda w: dist(w["centroid"], p))["id"]


def parse_sectors(sql):
    sectors = {m.group(1): {"id": m.group(1), "name": m.group(2), "faces": m.group(3).split(",")}
               for m in re.finditer(r"\('(pcmc-[a-z-]+)', '([^']+)', '\{([^}]*)\}'\)", sql)}
    ward_sector = dict(re.findall(r"\('(w-pc-\d+)','(pcmc-[a-z-]+)'\)", sql))
    return sectors, ward_sector


def parse_police(sql):
    return [{"source_id": i, "kind": "police", "name": n, "operator": "Pimpri Chinchwad Police", "agency_id": "pcpolice",
             "capacity": 4, "lng": float(lng), "lat": float(lat), "local_xy": local_xy(float(lng), float(lat)),
             "source_migration": "032_unit_tracking_and_teams.sql"}
            for i, n, lng, lat in re.findall(r"\('(PC-POL-\d+)', '([^']+)',\s*([\d.]+), ([\d.]+)\)", sql)]


# -------------------------------------------------------------------- roads ---
def build_roads(elements, nodes, bounds, hotspots, keep_points, minor_radius=550, service_radius=140):
    """Major classes everywhere; residential near hotspots; service roads only right at stations/lifelines.
    Ways are split at shared OSM nodes (true junctions), simplified, and reduced to the largest component."""
    proj = {}

    def P(nid):
        if nid not in proj:
            lat, lng = nodes[nid]
            proj[nid] = local_xy(lng, lat)
        return proj[nid]

    def near(nids, centres, radius):
        for nid in nids[:: max(1, len(nids) // 12)] + [nids[-1]]:
            if nid in nodes:
                p = P(nid)
                for c in centres:
                    if abs(p[0] - c[0]) < radius and abs(p[1] - c[1]) < radius and dist(p, c) < radius:
                        return True
        return False

    ways = []
    for e in elements:
        if e["type"] != "way":
            continue
        tags = e.get("tags") or {}
        hw = tags.get("highway")
        if hw in MAJOR:
            pass
        elif hw in MINOR:
            if not near(e["nodes"], hotspots, minor_radius):
                continue
        elif hw in SERVICE:
            if not near(e["nodes"], keep_points, service_radius):
                continue
        else:
            continue
        # Keep only the parts inside the envelope (split at nodes outside it).
        run = []
        for nid in e["nodes"] + [None]:
            p = P(nid) if nid in nodes else None
            if p is not None and bounds[0] <= p[0] <= bounds[2] and bounds[1] <= p[1] <= bounds[3]:
                run.append(nid)
                continue
            if len(run) > 1:
                ways.append((e["id"], hw.replace("_link", ""), tags.get("name:en") or tags.get("name", ""), tags.get("bridge") == "yes", run))
            run = []
    uses = Counter()
    for _, _, _, _, nids in ways:
        uses.update(set(nids))
        uses[nids[0]] += 1
        uses[nids[-1]] += 1
    edges = []
    for wid, kind, name, bridge, nids in ways:
        start = 0
        for i in range(1, len(nids)):
            if uses[nids[i]] > 1 or i == len(nids) - 1:
                if nids[i] != nids[start]:
                    edges.append({"way": wid, "kind": kind, "name": name, "bridge": bridge, "nodes": nids[start:i + 1]})
                start = i
    # Largest connected component over junction nodes.
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for ed in edges:
        ra, rb = find(ed["nodes"][0]), find(ed["nodes"][-1])
        if ra != rb:
            parent[ra] = rb
    sizes = Counter(find(ed["nodes"][0]) for ed in edges)
    main = sizes.most_common(1)[0][0]
    dropped = len(edges)
    edges = [ed for ed in edges if find(ed["nodes"][0]) == main]
    dropped -= len(edges)
    # Merge chains through degree-2 junctions when kind and name agree.
    degree = Counter()
    for ed in edges:
        degree[ed["nodes"][0]] += 1
        degree[ed["nodes"][-1]] += 1
    by_end = defaultdict(list)
    for i, ed in enumerate(edges):
        by_end[ed["nodes"][0]].append(i)
        by_end[ed["nodes"][-1]].append(i)
    alive = [True] * len(edges)
    for n, d in degree.items():
        if d != 2:
            continue
        pair = [i for i in by_end[n] if alive[i]]
        if len(pair) != 2 or pair[0] == pair[1]:
            continue
        a, b = edges[pair[0]], edges[pair[1]]
        if (a["kind"], a["name"], a["bridge"]) != (b["kind"], b["name"], b["bridge"]):
            continue
        an = a["nodes"] if a["nodes"][-1] == n else a["nodes"][::-1]
        bn = b["nodes"] if b["nodes"][0] == n else b["nodes"][::-1]
        if an[0] == bn[-1]:
            continue  # would close a loop
        a["nodes"] = an + bn[1:]
        alive[pair[1]] = False
        for end in (bn[-1],):
            by_end[end] = [pair[0] if i == pair[1] else i for i in by_end[end]]
    edges = [ed for ed, ok in zip(edges, alive) if ok]
    roads, junctions = [], set()
    for i, ed in enumerate(edges):
        pts = simplify([P(nid) for nid in ed["nodes"]], 3.0)
        pts = [r1(p) for p in pts]
        junctions.add(tuple(pts[0]))
        junctions.add(tuple(pts[-1]))
        road = {"id": f"osm-road-{ed['way']}-{i}", "points": pts, "kind": ed["kind"], "name": ed["name"], "blocked": False,
                "width": WIDTH.get(ed["kind"], 6)}
        if ed["bridge"]:
            road["bridge"] = True
        roads.append(road)
    return roads, {"components_dropped_edges": dropped, "junctions": len(junctions)}


def nearest_on_roads(p, roads, ward_poly=None):
    best = None
    for road in roads:
        pts = road["points"]
        for a, b in zip(pts, pts[1:]):
            if min(a[0], b[0]) - 400 > p[0] or max(a[0], b[0]) + 400 < p[0] or min(a[1], b[1]) - 400 > p[1] or max(a[1], b[1]) + 400 < p[1]:
                continue
            d, q = seg_dist(p, a, b)
            if ward_poly is not None and not inside(q, ward_poly):
                continue
            if best is None or d < best[0]:
                best = (d, q, road["id"])
    return best


# -------------------------------------------------------------------- water ---
def build_water(elements, nodes, bounds):
    ways = {e["id"]: e for e in elements if e["type"] == "way"}
    waterways, areas, pawana_ways = [], [], []
    for e in ways.values():
        tags = e.get("tags") or {}
        if not tags:
            continue
        pts = [local_xy(nodes[n][1], nodes[n][0]) for n in e["nodes"] if n in nodes]
        if len(pts) < 2:
            continue
        name = tags.get("name", "")
        if "waterway" in tags and tags["waterway"] != "riverbank":
            if name == "Pavana River" or name == "Pavana":
                pawana_ways.append(e)
                continue
            width = RIVER_WIDTH.get(name, WATERWAY_WIDTH.get(tags["waterway"], 5))
            for k, run in enumerate(clip_polyline(pts, bounds)):
                run = simplify(run, 4.0)
                waterways.append({"id": f"osm-waterway-{e['id']}-{k}", "name": name, "kind": tags["waterway"], "width": width,
                                  "points": [r1(p) for p in run]})
        elif tags.get("natural") == "water" and e["nodes"][0] == e["nodes"][-1]:
            poly = clip_rect(pts[:-1], bounds)
            if len(poly) >= 3 and area(poly) > 150:
                areas.append({"id": f"osm-water-{e['id']}", "name": name, "kind": tags.get("water", "water"),
                              "polygon": [r1(p) for p in simplify(poly + [poly[0]], 2.0)[:-1]]})
    for e in elements:  # multipolygon outer rings (river banks)
        if e["type"] == "relation" and (e.get("tags") or {}).get("natural") == "water":
            for m in e.get("members", []):
                w = ways.get(m.get("ref"))
                if m.get("role") == "outer" and w and w["nodes"][0] == w["nodes"][-1] and not w.get("tags"):
                    pts = [local_xy(nodes[n][1], nodes[n][0]) for n in w["nodes"] if n in nodes]
                    poly = clip_rect(pts[:-1], bounds)
                    if len(poly) >= 3:
                        areas.append({"id": f"osm-water-r{e['id']}-{w['id']}", "name": "", "kind": e["tags"].get("water", "water"),
                                      "polygon": [r1(p) for p in simplify(poly + [poly[0]], 2.0)[:-1]]})
    # Chain the Pawana ways end to end (upstream Ravet -> downstream Kasarwadi).
    chain = []
    remaining = list(pawana_ways)
    starts = {w["nodes"][0] for w in remaining}
    ends = {w["nodes"][-1] for w in remaining}
    head = next(w for w in remaining if w["nodes"][0] not in ends)
    while head:
        remaining.remove(head)
        chain += head["nodes"] if not chain else head["nodes"][1:]
        head = next((w for w in remaining if w["nodes"][0] == chain[-1]), None)
    assert not remaining, "Pawana ways did not chain"
    del starts
    pts = [local_xy(nodes[n][1], nodes[n][0]) for n in chain if n in nodes]
    runs = clip_polyline(pts, bounds)
    full = max(runs, key=len)
    pawana_full = [r1(p) for p in simplify(full, 4.0)]
    waterways.insert(0, {"id": "osm-waterway-pawana", "name": "Pawana (Pavana) river", "kind": "river", "width": RIVER_WIDTH["Pavana River"],
                         "points": pawana_full})
    return waterways, areas, pawana_full


# --------------------------------------------------------------------- main ---
def build(source: Path) -> dict:
    mig = source / "backend" / "migrations"
    sql29 = (mig / "029_pcmc_pccoe_zone.sql").read_text(encoding="utf8")
    sql30 = (mig / "030_hazard_sector_agent_log.sql").read_text(encoding="utf8")
    sql32 = (mig / "032_unit_tracking_and_teams.sql").read_text(encoding="utf8")
    osm = source / "data" / "raw" / "osm"
    manifest = json.loads((osm / "manifest.json").read_text(encoding="utf8"))
    cat = _catalog.build(sql29)
    x0, y0 = local_xy(ENVELOPE[0], ENVELOPE[1])
    x1, y1 = local_xy(ENVELOPE[2], ENVELOPE[3])
    bounds = [x0, y0, x1, y1]

    wards = voronoi_wards(parse_wards(sql29), bounds)
    sectors, ward_sector = parse_sectors(sql30)
    for w in wards:
        w["sector_id"] = ward_sector.get(w["id"])
        w["hazards"] = sectors.get(w["sector_id"], {}).get("faces", [])
    units = cat["resources"] + parse_police(sql32)
    for u in units:
        u.setdefault("source_migration", "029_pcmc_pccoe_zone.sql")
    lifelines = cat["lifelines"]
    for m in re.finditer(r"\('(lf-pc-\w+)'.*?,(true|false),'\{([^}]*)\}',(null|\d+)\)", sql29):
        lf = next(l for l in lifelines if l["source_id"] == m.group(1))
        lf["accepts_casualties"] = m.group(2) == "true"
        lf["specialities"] = [s for s in m.group(3).split(",") if s]
        lf["people_served_per_hour"] = None if m.group(4) == "null" else int(m.group(4))

    hotspots = [{"id": i, "name": n, "lat": lat, "lng": lng, "named_ward": w, "snap": s, "local_xy": xy(lat, lng)} for i, n, lat, lng, w, s in HOTSPOTS]
    docks = [{"id": i, "name": n, "lat": lat, "lng": lng, "local_xy": xy(lat, lng)} for i, n, lat, lng in DOCK_SITES]
    keep_points = [u["local_xy"] for u in units] + [l["local_xy"] for l in lifelines] + [d["local_xy"] for d in docks]
    road_elements, road_nodes = load_osm(osm / "roads.json")
    roads, road_stats = build_roads(road_elements, road_nodes, bounds, [h["local_xy"] for h in hotspots] + keep_points, keep_points)
    del road_elements, road_nodes

    water_elements, water_nodes = load_osm(osm / "water.json")
    waterways, water_areas, pawana = build_water(water_elements, water_nodes, bounds)
    river = [r1(p) for p in simplify(pawana, 15.0)]

    ctx_elements, ctx_nodes = load_osm(osm / "context.json")
    no_fly, osm_stations, osm_schools = [], [], []
    for e in ctx_elements:
        tags = e.get("tags") or {}
        if e["type"] == "way" and tags.get("landuse") == "military" and e["nodes"][0] == e["nodes"][-1]:
            pts = [local_xy(ctx_nodes[n][1], ctx_nodes[n][0]) for n in e["nodes"] if n in ctx_nodes]
            poly = clip_rect(pts[:-1], bounds)
            if len(poly) >= 3 and area(poly) > 5000:
                poly = simplify(poly + [poly[0]], 25.0)[:-1]
                no_fly.append({"id": f"osm-military-{e['id']}", "name": tags.get("name") or "Military area (Dehu Road cantonment)",
                               "reason": "military airspace (migration 030 sector pcmc-dehu-talawade faces 'airspace')",
                               "polygon": [r1(p) for p in poly], "height": 150})
        if tags.get("amenity") in ("fire_station", "police", "hospital", "school") and (e["type"] == "node" or "center" in e):
            lat, lng = (e["lat"], e["lon"]) if e["type"] == "node" else (e["center"]["lat"], e["center"]["lon"])
            entry = {"osm_id": f"{e['type']}/{e['id']}", "kind": tags["amenity"], "name": tags.get("name", ""), "lat": lat, "lng": lng,
                     "local_xy": xy(lat, lng)}
            if tags["amenity"] == "school":
                if entry["name"]:
                    osm_schools.append(entry)
            elif tags["amenity"] != "hospital" or entry["name"]:
                osm_stations.append(entry)
    del ctx_elements, ctx_nodes

    # Snap everything that moves on roads, and record how far the real point is from the kept network.
    ward_poly = {w["id"]: w["polygon"] for w in wards}
    for item in units + lifelines + docks:
        item["ward_id"] = ward_of(item["local_xy"], wards)
        if item.get("kind") == "boat":
            d, q = min((seg_dist(item["local_xy"], a, b) for a, b in zip(pawana, pawana[1:])), key=lambda t: t[0])
            item["river_xy"], item["river_distance_m"] = r1(q), round(d, 1)
            continue
        hit = nearest_on_roads(item["local_xy"], roads)
        item["road_xy"], item["road_distance_m"] = (r1(hit[1]), round(hit[0], 1)) if hit else (None, None)
    for h in hotspots:
        h["ward_id"] = ward_of(h["local_xy"], wards)
        if h["snap"] == "river":
            d, q = min((seg_dist(h["local_xy"], a, b) for a, b in zip(pawana, pawana[1:])), key=lambda t: t[0])
            # Stand on the bank side of the anchor, 28 m off the centreline (inside the flooded corridor).
            dx, dy = h["local_xy"][0] - q[0], h["local_xy"][1] - q[1]
            n = math.hypot(dx, dy) or 1.0
            site = [q[0] + dx / n * 28, q[1] + dy / n * 28]
            if not inside(site, ward_poly[h["named_ward"]]):
                site = h["local_xy"]
            h["site_xy"] = r1(site)
            h["river_distance_m"] = round(dist(site, q), 1)
        elif h["snap"] == "road":
            hit = nearest_on_roads(h["local_xy"], roads, ward_poly[h["named_ward"]])
            h["site_xy"], h["snap_distance_m"] = r1(hit[1]), round(hit[0], 1)
        else:
            h["site_xy"] = r1(h["local_xy"])
        h["site_ward_id"] = ward_of(h["site_xy"], wards)
        if h["site_ward_id"] != h["named_ward"]:
            h["ward_note"] = (f"The real place lies in {h['site_ward_id']} by the migration's Voronoi cells; "
                              f"DisruptionOps scenarios.py files this report under {h['named_ward']}.")
        hit = nearest_on_roads(h["site_xy"], roads)
        h["road_xy"], h["road_distance_m"] = r1(hit[1]), round(hit[0], 1)
    crossings = []
    for road in roads:
        for a, b in zip(road["points"], road["points"][1:]):
            for c, d in zip(river, river[1:]):
                hit = seg_intersection(a, b, c, d)
                if hit and not any(dist(hit, k["position"]) < 60 for k in crossings):
                    crossings.append({"position": r1(hit), "road_id": road["id"], "name": road["name"], "kind": road["kind"],
                                      "bridge": bool(road.get("bridge"))})
    flood_zones = []
    for fid, name, hid, reach in FLOOD_ZONES:
        # The overflowed bank as a chain of circles along the Pawana: +-reach metres of river around the
        # hotspot, FLOOD_BANK_M either side of the centreline (streets set back from the river stay dry).
        h = next(x for x in hotspots if x["id"] == hid)
        along, total, best = [], 0.0, None
        for a, b in zip(pawana, pawana[1:]):
            d, q = seg_dist(h["site_xy"], a, b)
            if best is None or d < best[0]:
                best = (d, total + dist(a, q))
            along.append((total, a, b))
            total += dist(a, b)
        for k, s in enumerate(range(int(best[1] - reach), int(best[1] + reach) + 1, FLOOD_STEP_M)):
            for s0, a, b in along:
                L = dist(a, b)
                if s0 <= s <= s0 + L and L > 0:
                    t = (s - s0) / L
                    c = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]
                    flood_zones.append({"id": f"{fid}-{k:02d}", "reach": fid, "name": name, "center": r1(c), "radius": FLOOD_BANK_M,
                                        "ward_id": ward_of(c, wards)})
                    break
    stations = {}
    for u in units:
        label = _catalog_label(u["name"])
        st = stations.setdefault(label, {"id": "st-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-"), "name": label,
                                         "local_xy": u["local_xy"], "ward_id": u["ward_id"], "unit_ids": []})
        st["unit_ids"].append(u["source_id"])

    # Coverage docks (pcmc_random): drones reach ~1.9 km from a dock on one battery, so add real places
    # (OSM fire/police stations, named hospitals and schools) greedily until the road network is covered.
    for d in docks:
        d["role"] = "core"
    junction_pts = list({tuple(r["points"][0]) for r in roads} | {tuple(r["points"][-1]) for r in roads})[::3]

    def covered(p):
        return any(dist(p, d["local_xy"]) <= DOCK_REACH_M for d in docks)
    open_pts = [p for p in junction_pts if not covered(p)]
    wet = lambda p: any(dist(p, z["center"]) < z["radius"] for z in flood_zones)
    candidates = [s for s in osm_stations + osm_schools if bounds[0] < s["local_xy"][0] < bounds[2] and bounds[1] < s["local_xy"][1] < bounds[3]
                  and not wet(s["local_xy"]) and not any(inside(s["local_xy"], z["polygon"]) for z in no_fly)
                  and not re.search(r"dental|clinic|eye|skin|ivf", s["name"], re.I)]
    while open_pts and len(docks) < MAX_DOCKS:
        best = max(candidates, key=lambda s: (sum(dist(p, s["local_xy"]) <= DOCK_REACH_M for p in open_pts), s["osm_id"]))
        gain = [p for p in open_pts if dist(p, best["local_xy"]) <= DOCK_REACH_M]
        if len(gain) < 5:
            break
        candidates.remove(best)
        docks.append({"id": "DOCK-" + re.sub(r"[^A-Z0-9]+", "-", (best["name"] or best["kind"]).upper()).strip("-")[:24],
                      "name": f"{best['name'] or best['kind']} ({best['kind'].replace('_', ' ')})", "lat": best["lat"], "lng": best["lng"],
                      "local_xy": best["local_xy"], "role": "coverage", "osm_id": best["osm_id"]})
        open_pts = [p for p in open_pts if p not in gain]
    for d in docks:
        d["ward_id"] = ward_of(d["local_xy"], wards)
    dock_coverage = round(1 - len(open_pts) / len(junction_pts), 3)

    base_hit = nearest_on_roads(docks[0]["local_xy"], roads)
    shelters = [{"id": l["source_id"], "kind": l["kind"], "name": l["name"], "position": l["local_xy"] + [0], "capacity": l.get("capacity")}
                for l in lifelines if l["kind"] in ("shelter", "relief_centre")]
    world = {
        "id": "pcmc-region", "kind": "region", "name": "PCMC zone round PCCOE (Akurdi-Nigdi, Pimpri-Chinchwad)",
        "anchor": [ANCHOR[0], ANCHOR[1]], "envelope_wsen": list(ENVELOPE),
        "bounds": [round(v, 1) for v in bounds],
        "terrain": {"size": 2, "bounds": [round(v, 1) for v in bounds], "elevations": [0.0, 0.0, 0.0, 0.0], "reference_elevation_m": 575.0,
                    "source": "flat", "note": "Flat; per-ward elevation_m (DisruptionOps planning estimates) is in wards."},
        "buildings": [],
        "roads": roads,
        "road_network": {"prebuilt": True, "note": "Every junction is a polyline end point (OSM shared nodes); interior points are not junctions. "
                         "Flyovers that do not share an OSM node stay unconnected. Largest connected component only.", **road_stats},
        "river": river, "river_width": RIVER_WIDTH["Pavana River"], "river_name": "Pawana (Pavana)",
        "waterways": waterways, "water_areas": water_areas, "flood_zones": flood_zones, "river_crossings": crossings,
        "bases": [{"id": "pccoe-ops", "kind": "drone_base", "name": "PCCOE operations base", "position": r1(base_hit[1]) + [0]}],
        "shelters": shelters, "no_fly": no_fly,
        "wards": [{k: w[k] for k in ("id", "number", "name", "population", "elderly_share", "elevation_m", "area_sq_km", "sector_id", "hazards")}
                  | {"centroid": r1(w["centroid"]), "polygon": w["polygon"]} for w in wards],
        "sectors": list(sectors.values()),
        "lifelines": [_pick(l, ("source_id", "kind", "name", "capacity", "accepts_casualties", "specialities", "people_served_per_hour",
                                "lat", "lng", "local_xy", "ward_id", "road_xy", "road_distance_m")) for l in lifelines],
        "units": [_pick(u, ("source_id", "kind", "name", "operator", "agency_id", "capacity", "lat", "lng", "local_xy", "ward_id",
                            "road_xy", "road_distance_m", "river_xy", "river_distance_m", "source_migration")) for u in units],
        "stations": list(stations.values()),
        "osm_stations": osm_stations,
        "dock_sites": docks, "dock_coverage": {"reach_m": DOCK_REACH_M, "road_junctions_covered": dock_coverage},
        "hotspots": hotspots,
        "metadata": {
            "source": "OpenStreetMap (Overpass extract shipped with DisruptionOps data/raw/osm) + DisruptionOps migrations 029/030/032",
            "attribution": "(c) OpenStreetMap contributors; ward/unit/lifeline data from DisruptionOps (read-only)",
            "license": "ODbL 1.0 geometry", "source_url": "https://www.openstreetmap.org/copyright",
            "osm_base": manifest["files"]["roads"]["osm_base"], "osm_fetched_at": manifest["fetched_at"],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "projection": "local metres: x = (lng - 73.7624378) * 105600, y = (lat - 18.6526632) * 110570 (tools/build_agency_catalog.py)",
            "road_selection": "motorway..tertiary, links and unclassified everywhere; residential/living_street within 550 m of a hotspot, unit, "
                              "lifeline or dock; service roads within 140 m of a unit, lifeline or dock; Douglas-Peucker 3 m",
            "approximate": ["ward populations/elevations are DisruptionOps planning estimates", "ward cells are Voronoi of centroids (as migration 029)",
                            "incident points are plausible real places, not reported coordinates", "river width is a constant per river",
                            "world.river is the Pawana centreline simplified to 15 m; waterways keeps a 4 m version", "no building footprints (context.json has none)"],
            "synthetic_features": ["flood_zones", "dock_sites"],
        },
    }
    return world


def _catalog_label(name):
    return name[name.find("(") + 1:name.rfind(")")] if "(" in name and name.endswith(")") else name


def _pick(d, keys):
    return {k: d[k] for k in keys if k in d}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, required=True, help="DisruptionOps checkout root (read-only)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    world = build(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(world, separators=(",", ":"), ensure_ascii=False)
    args.output.write_text(text, encoding="utf8")
    if not args.quiet:
        segs = sum(len(r["points"]) - 1 for r in world["roads"])
        print(f"{args.output} {len(text) / 1e6:.2f} MB: {len(world['roads'])} roads ({segs} segments, "
              f"{world['road_network']['junctions']} junctions), {len(world['waterways'])} waterways, {len(world['water_areas'])} water areas, "
              f"river {len(world['river'])} pts, {len(world['wards'])} wards, {len(world['lifelines'])} lifelines, {len(world['units'])} units, "
              f"{len(world['no_fly'])} no-fly, {len(world['hotspots'])} hotspots")
        print("kinds:", dict(Counter(r["kind"] for r in world["roads"])))
        far = [(u["source_id"], u.get("road_distance_m")) for u in world["units"] + world["lifelines"] if (u.get("road_distance_m") or 0) > 60]
        print("units/lifelines > 60 m from a kept road:", far)
        for h in world["hotspots"]:
            print(" ", h["id"], h["named_ward"], h["site_ward_id"], h["site_xy"], h.get("snap_distance_m"), h.get("road_distance_m"), h.get("ward_note", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
