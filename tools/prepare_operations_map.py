"""Fetch a small OSM extract and prepare a attributed, local-metre 3D reference.

No Google imagery is scraped. River, flood and emergency facilities are fictional.
Building heights without OSM measurements are explicitly estimated.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repository root (tools/..)
ANCHOR = (18.6526632, 73.7624378)


def local(lon, lat):
    return [round((lon - ANCHOR[1]) * 111320 * math.cos(math.radians(ANCHOR[0])), 2),
            round((lat - ANCHOR[0]) * 111320, 2)]


def clip_segment(a, b, bounds=(-600, -600, 600, 600)):
    """Liang–Barsky clipping avoids source ways floating beyond the terrain."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0]-bounds[0]), (dx, bounds[2]-a[0]),
                 (-dy, a[1]-bounds[1]), (dy, bounds[3]-a[1])):
        if abs(p) < 1e-12:
            if q < 0:
                return None
        elif p < 0:
            lo = max(lo, q/p)
        else:
            hi = min(hi, q/p)
        if lo > hi:
            return None
    return [[round(a[0]+t*dx, 2), round(a[1]+t*dy, 2)] for t in (lo, hi)]


def clip_polyline(points):
    paths, current = [], []
    for a, b in zip(points, points[1:]):
        segment = clip_segment(a, b)
        if not segment or segment[0] == segment[1]:
            if len(current) > 1:
                paths.append(current)
            current = []
            continue
        if current and current[-1] != segment[0]:
            paths.append(current)
            current = []
        if not current:
            current.append(segment[0])
        current.append(segment[1])
    if len(current) > 1:
        paths.append(current)
    return paths


def prepare(raw: dict) -> dict:
    rng = random.Random(73)
    buildings, roads = [], []
    for element in raw.get('elements', []):
        tags = element.get('tags', {})
        points = [local(p['lon'], p['lat']) for p in element.get('geometry', [])]
        if not points:
            continue
        if tags.get('building') and len(points) >= 4:
            height_source = 'estimated'
            try:
                height = float(tags.get('height', '').replace(' m', ''))
                height_source = 'osm_height'
            except ValueError:
                try:
                    height = float(tags['building:levels']) * 3.2
                    height_source = 'osm_levels_estimate'
                except (ValueError, KeyError):
                    height = rng.choice([7, 10, 13, 16, 20, 25])
            buildings.append({'id': f"osm-building-{element['id']}", 'footprint': points,
                              'height': min(100, max(4, height)), 'kind': tags.get('building', 'yes'),
                              'height_source': height_source,
                              'name': tags.get('name', '')})
        if tags.get('highway') not in (None, 'footway', 'path', 'steps', 'construction') and len(points) > 1:
            for part, path in enumerate(clip_polyline(points)):
                roads.append({'id': f"osm-road-{element['id']}-{part}", 'points': path,
                              'kind': tags['highway'], 'name': tags.get('name', ''), 'blocked': False,
                              'width': 13 if tags['highway'] in ('primary', 'secondary', 'tertiary') else 7})
    if not buildings:
        # An explicit fallback avoids disguising invented buildings as sourced geometry.
        for row in range(-4, 5):
            for col in range(-4, 5):
                x, y = col * 105 + rng.uniform(-10, 10), row * 105 + rng.uniform(-10, 10)
                w, d = rng.uniform(22, 55), rng.uniform(20, 45)
                buildings.append({'id': f'fictional-{row}-{col}',
                                  'footprint': [[x-w/2, y-d/2], [x+w/2, y-d/2], [x+w/2, y+d/2], [x-w/2, y+d/2], [x-w/2, y-d/2]],
                                  'height': rng.choice([8, 12, 18, 25]), 'kind': 'residential', 'height_source': 'estimated'})
    if not roads:
        for offset in (-460, -230, 0, 230, 460):
            roads.extend([{'id': f'fictional-ew-{offset}', 'points': [[-600, offset], [600, offset]], 'width': 10, 'blocked': False},
                          {'id': f'fictional-ns-{offset}', 'points': [[offset, -600], [offset, 600]], 'width': 10, 'blocked': False}])
    height_path = ROOT / 'assets' / 'operations' / 'pune-height-overlay.json'
    height_metadata = None
    if height_path.exists():
        overlay = json.loads(height_path.read_text(encoding='utf8'))
        height_metadata = overlay['metadata']
        for building in buildings:
            prediction = overlay['heights'].get(building['id'])
            if prediction and building['height_source'] == 'estimated':
                for key in ('height', 'height_source', 'height_year', 'valid_pixel_fraction', 'height_p25', 'height_p75'):
                    building[key] = prediction[key]
    height_counts = {}
    for building in buildings:
        source = building['height_source']
        height_counts[source] = height_counts.get(source, 0) + 1
    # Explicit scenario overlay, never written back to OSM.
    river = [[-310, -600], [-180, -360], [-250, -90], [-130, 210], [-260, 600]]
    terrain_path = ROOT / 'assets' / 'operations' / 'terrain.json'
    terrain = json.loads(terrain_path.read_text(encoding='utf8')) if terrain_path.exists() else None
    return {'anchor': list(ANCHOR), 'bounds': [-600, -600, 600, 600], 'terrain': terrain,
            'buildings': buildings, 'roads': roads, 'river': river, 'river_width': 45,
            'bases': [{'id': 'launch-base', 'kind': 'drone_base', 'position': [330, -390, 0]},
                      {'id': 'district-hospital', 'kind': 'hospital', 'position': [380, -240, 0]},
                      {'id': 'rescue-station', 'kind': 'rescue', 'position': [280, -430, 0]}],
            'shelters': [{'id': 'north-shelter', 'kind': 'shelter', 'position': [260, 330, 0], 'capacity': 100},
                         {'id': 'south-shelter', 'kind': 'shelter', 'position': [-70, -420, 0], 'capacity': 70}],
            'no_fly': [{'id': 'hospital-approach', 'center': [380, -240], 'radius': 55, 'height': 70}],
            'metadata': {'source': 'OpenStreetMap' if raw.get('elements') else 'procedural_fallback',
                         'attribution': '© OpenStreetMap contributors' + (' · Heights: Google Research / Copernicus Sentinel-2' if height_metadata else ''), 'license': 'ODbL 1.0 geometry; CC BY-4.0 height predictions' if height_metadata else 'ODbL 1.0',
                         'source_url': 'https://www.openstreetmap.org/copyright',
                         'fetched_at': datetime.now(timezone.utc).isoformat(),
                         'replica_quality': 'mapped footprints, predicted and estimated heights, coarse SRTM terrain, procedural materials; not a surveyed replica',
                         'height_sources': height_counts, 'height_data': height_metadata,
                         'synthetic_features': ['river', 'flood', 'shelters', 'response facilities', 'no-fly training zone'],
                         'google_maps_reference': '18.6526632,73.7624378'}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    cache = ROOT / '.cache' / 'operations-osm.json'
    cache.parent.mkdir(parents=True, exist_ok=True)
    raw = {}
    if cache.exists():
        raw = json.loads(cache.read_text(encoding='utf8'))
    elif not args.offline:
        dlat, dlon = 600/111320, 600/(111320*math.cos(math.radians(ANCHOR[0])))
        bbox = f'{ANCHOR[0]-dlat},{ANCHOR[1]-dlon},{ANCHOR[0]+dlat},{ANCHOR[1]+dlon}'
        query = f'[out:json][timeout:35];(way[building]({bbox});way[highway]({bbox}););out geom;'
        body = urllib.parse.urlencode({'data': query}).encode()
        for endpoint in ('https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'):
            try:
                request = urllib.request.Request(endpoint, body, {'User-Agent': 'DisruptionOps-Research-Simulator/1.0', 'Content-Type': 'application/x-www-form-urlencoded'})
                with urllib.request.urlopen(request, timeout=45) as response:
                    raw = json.load(response)
                if raw.get('elements'):
                    cache.write_text(json.dumps(raw), encoding='utf8')
                    break
            except Exception as exc:
                print(f'OSM fetch unavailable ({type(exc).__name__}); trying fallback.')
    world = prepare(raw)
    output = ROOT / 'assets' / 'operations' / 'pune-world.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(world, separators=(',', ':')), encoding='utf8')
    print(f"Prepared {len(world['buildings'])} buildings, {len(world['roads'])} road segments; source={world['metadata']['source']}")
    print(output)


if __name__ == '__main__':
    main()
