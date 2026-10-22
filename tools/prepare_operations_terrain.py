"""Cache a small, attributed SRTM elevation grid; never invent measured terrain."""
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repository root (tools/..)
ANCHOR = (18.6526632, 73.7624378)


def main():
    cache = ROOT / '.cache' / 'operations-terrain.json'
    size = 9
    coordinates = []
    for row in range(size):
        for col in range(size):
            x, y = -600 + col * 150, -600 + row * 150
            coordinates.append((ANCHOR[0] + y / 111320,
                                ANCHOR[1] + x / (111320 * math.cos(math.radians(ANCHOR[0])))))
    if cache.exists():
        data = json.loads(cache.read_text(encoding='utf8'))
    else:
        query = urllib.parse.urlencode({'locations': '|'.join(f'{lat:.7f},{lon:.7f}' for lat, lon in coordinates), 'interpolation': 'bilinear'})
        request = urllib.request.Request('https://api.opentopodata.org/v1/srtm90m?' + query,
                                         headers={'User-Agent': 'Disaster-Swarm-Simulator/1.0'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if data.get('status') != 'OK' or len(data.get('results', [])) != size * size:
            raise ValueError('Terrain request failed: ' + str(data.get('error')))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding='utf8')
    heights = [r['elevation'] for r in data['results']]
    if any(h is None or not math.isfinite(h) for h in heights):
        raise ValueError('Terrain coverage incomplete; retaining existing map.')
    reference = min(heights)
    terrain = {'size': size, 'bounds': [-600, -600, 600, 600],
               'elevations': [round(h - reference, 2) for h in heights],
               'reference_elevation_m': reference, 'sample_spacing_m': 150,
               'source': 'SRTM 90m via Open Topo Data',
               'source_url': 'https://www.opentopodata.org/datasets/srtm/',
               'vertical_datum': 'EGM96', 'resolution_m': 90,
               'note': 'Coarse radar terrain; buildings and small terrain features are not surveyed.'}
    output = ROOT / 'assets' / 'operations' / 'terrain.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(terrain, indent=2), encoding='utf8')
    print(f'Terrain cached: {size}x{size}, relative range 0–{max(heights)-reference:.1f} m')


if __name__ == '__main__':
    main()
