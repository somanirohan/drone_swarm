"""Fetch bounded Google Open Buildings 2023 COG windows and propose height overlay.

No Earth Engine login, full regional tile, Google Maps imagery, or world edits.
Dependencies: rasterio, pyproj, s2sphere, shapely. Google data: CC BY-4.0.
"""
from __future__ import annotations

import argparse
import json
import math
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pyproj
import rasterio
import rasterio.features
import rasterio.windows
import s2sphere
from rasterio.transform import Affine
from shapely.geometry import Polygon, box, mapping
from shapely.ops import transform as geometry_transform

ROOT = Path(__file__).resolve().parents[1]  # repository root (tools/..)
ANCHOR = (18.6526632, 73.7624378)
BUCKET = "open-buildings-temporal-data"
DATA_SOURCE = "https://sites.research.google/gr/open-buildings/temporal/"
MANIFEST_REFERENCE = "https://github.com/google-research/google-research/blob/master/building_detection/open_buildings_temporal_download_region_geotiffs.ipynb"


def axis_aligned_window(bounds, affine):
    """Avoid NumPy linalg (broken MKL delay-load DLL in this Windows environment)."""
    if affine.b or affine.d:
        raise ValueError("Expected axis-aligned raster")
    left,bottom,right,top = bounds
    cols = sorted(((left-affine.c)/affine.a, (right-affine.c)/affine.a))
    rows = sorted(((top-affine.f)/affine.e, (bottom-affine.f)/affine.e))
    col0,row0 = math.floor(cols[0]),math.floor(rows[0])
    return rasterio.windows.Window(col0,row0,math.ceil(cols[1])-col0,math.ceil(rows[1])-row0)


def fetch_json(url, limit=10_000_000):
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("Metadata exceeds bounded download")
    return json.loads(data)


def discover_tiles(cache, year=2023, radius=620):
    token = s2sphere.CellId.from_lat_lng(s2sphere.LatLng.from_degrees(*ANCHOR)).parent(2).to_token()
    # Anchor is well inside UTM43; select only relevant CRS manifest.
    projection = "EPSG:32643"
    manifest_name = f"v1/manifests/{token}_EPSG_32643_{year}_06_30.json"
    cache_manifest = cache / Path(manifest_name).name
    if cache_manifest.exists():
        manifest = json.loads(cache_manifest.read_text())
    else:
        manifest = fetch_json(f"https://storage.googleapis.com/{BUCKET}/{manifest_name}")
        cache_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    transformer = pyproj.Transformer.from_crs("EPSG:4326", projection, always_xy=True)
    cx, cy = transformer.transform(ANCHOR[1], ANCHOR[0])
    region = box(cx-radius, cy-radius, cx+radius, cy+radius)
    urls = []
    for tileset in manifest["tilesets"]:
        for source in tileset["sources"]:
            affine = source["affineTransform"]
            transform = Affine(affine["scaleX"], 0, affine["translateX"],
                               0, affine["scaleY"], affine["translateY"])
            width, height = source["dimensions"]["width"], source["dimensions"]["height"]
            polygon = Polygon([transform*p for p in ((0,0),(width,0),(width,height),(0,height))])
            if polygon.intersects(region):
                url = (manifest["uriPrefix"] + source["uris"][0]).replace("gs://", "https://storage.googleapis.com/")
                urls.append(url)
    if not 1 <= len(urls) <= 4:
        raise RuntimeError("Unexpected number of intersecting tiles")
    band_indexes = {b["id"]: b.get("tilesetBandIndex", 0) + 1 for b in manifest["bands"]}
    return urls, region, projection, band_indexes, manifest_name


class BoundedRangeProxy:
    """Only HTTP ranges allowed; hard cap total GCS bytes across GDAL requests."""
    def __init__(self, urls, cap=64*1024*1024):
        self.urls, self.cap, self.bytes = urls, cap, 0
        self.lock = threading.Lock()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _index(self):
                return int(urllib.parse.urlparse(self.path).path.split("/")[-1].replace(".tif", ""))

            def do_HEAD(self):
                try:
                    url = parent.urls[self._index()]
                    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=20) as response:
                        length = response.headers["Content-Length"]
                    self.send_response(200)
                    self.send_header("Content-Length", length)
                    self.send_header("Accept-Ranges", "bytes")
                    self.end_headers()
                except Exception:
                    self.send_error(502)

            def do_GET(self):
                requested = self.headers.get("Range", "")
                try:
                    if not requested.startswith("bytes=") or "," in requested:
                        raise RuntimeError("Full tile requests are forbidden")
                    start, end = map(int, requested.removeprefix("bytes=").split("-"))
                    count = end - start + 1
                    with parent.lock:
                        if count <= 0 or count > 16*1024*1024 or parent.bytes + count > parent.cap:
                            raise RuntimeError("Download budget exceeded")
                        parent.bytes += count
                    request = urllib.request.Request(parent.urls[self._index()], headers={"Range": requested})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        if response.status != 206:
                            raise RuntimeError("Source ignored range")
                        data = response.read(count+1)
                        content_range = response.headers["Content-Range"]
                    if len(data) != count:
                        raise RuntimeError("Unexpected range size")
                    self.send_response(206)
                    self.send_header("Content-Type", "image/tiff")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Content-Range", content_range)
                    self.end_headers()
                    self.wfile.write(data)
                except Exception:
                    self.send_error(502)

        self.server = ThreadingHTTPServer(("127.0.0.1",0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def url(self, index):
        return f"http://127.0.0.1:{self.server.server_port}/{index}.tif"


def fetch_crop(cache, urls, region, indexes, budget_mb=64):
    crops = []
    with BoundedRangeProxy(urls, cap=budget_mb*1024*1024) as proxy:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                          GDAL_HTTP_TIMEOUT="45", GDAL_HTTP_MAX_RETRY="0", GDAL_CACHEMAX=64):
            for i, source_url in enumerate(urls):
                destination = cache / f"google-open-buildings-2023-crop-{i}.tif"
                if not destination.exists():
                    with rasterio.open(proxy.url(i)) as dataset:
                        intersect = box(*dataset.bounds).intersection(region)
                        window = axis_aligned_window(intersect.bounds, dataset.transform)
                        window = window.intersection(rasterio.windows.Window(0,0,dataset.width,dataset.height))
                        data = dataset.read([indexes["building_height"], indexes["building_presence"]], window=window)
                        transform = dataset.window_transform(window)
                        with rasterio.open(destination, "w", driver="GTiff", width=data.shape[2], height=data.shape[1],
                            count=2, dtype=data.dtype, crs=dataset.crs, transform=transform,
                            compress="deflate", nodata=-99) as output:
                            output.write(data)
                            output.set_band_description(1, "building_height")
                            output.set_band_description(2, "building_presence")
                            output.update_tags(source=source_url, year="2023", license="CC BY-4.0")
                    print(f"Cropped tile {i+1}/{len(urls)}; range bytes so far: {proxy.bytes:,}", flush=True)
                crops.append(destination)
        return crops, proxy.bytes


def height_overlay(world_path, crops, presence_threshold=0.5):
    world = json.loads(world_path.read_text(encoding="utf-8"))
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    lat0, lon0 = ANCHOR
    def point(east, north):
        return to_utm.transform(lon0 + east/(111320*math.cos(math.radians(lat0))), lat0+north/111320)
    result = {}
    with_raster = []
    for crop in crops:
        dataset = rasterio.open(crop)
        with_raster.append((dataset, dataset.read()))
    try:
        for building in world["buildings"]:
            if building.get("height_source") == "osm_height":
                continue
            polygon = Polygon([point(*p[:2]) for p in building["footprint"]])
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if polygon.is_empty:
                continue
            values, total, covered = [], 0, 0
            for dataset, data in with_raster:
                if not polygon.intersects(box(*dataset.bounds)):
                    continue
                window = axis_aligned_window(polygon.bounds, dataset.transform)
                window = window.intersection(rasterio.windows.Window(0,0,dataset.width,dataset.height))
                if window.width < 1 or window.height < 1:
                    continue
                slices = window.toslices()
                sample = data[:, slices[0], slices[1]]
                mask = rasterio.features.geometry_mask([mapping(polygon)], out_shape=sample.shape[1:],
                    transform=dataset.window_transform(window), invert=True)
                valid = mask & np.isfinite(sample[0]) & (sample[0]>0) & (sample[0]<=100) & (sample[1]>=presence_threshold)
                total += int(mask.sum())
                covered += int(valid.sum())
                values.extend(sample[0][valid].tolist())
            # Avoid assigning height from a single shifted edge or another roof.
            if len(values) < 16 or not total or covered/total < 0.25:
                continue
            predicted = float(np.median(values))
            result[building["id"]] = {"height": round(predicted,2), "height_source":"google_open_buildings_2023_predicted",
                "height_year":2023, "height_method":"median inside OSM footprint; building presence >= threshold",
                "valid_pixel_fraction":round(covered/total,3), "sample_count":len(values),
                "height_p25":round(float(np.percentile(values,25)),2), "height_p75":round(float(np.percentile(values,75)),2)}
    finally:
        for dataset, _ in with_raster:
            dataset.close()
    return result, len(world["buildings"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=ROOT/"assets/operations/pune-world.json")
    parser.add_argument("--output", type=Path, default=ROOT/"assets/operations/pune-height-overlay.json")
    parser.add_argument("--budget-mb", type=int, default=64)
    parser.add_argument("--presence-threshold", type=float, default=0.5)
    args = parser.parse_args()
    if not 8 <= args.budget_mb <= 128 or not 0 < args.presence_threshold <= 1:
        parser.error("Budget must be 8..128MB; presence threshold 0..1")
    cache = ROOT/".cache/operations/open-buildings-height"
    cache.mkdir(parents=True, exist_ok=True)
    urls, region, projection, indexes, manifest = discover_tiles(cache)
    crops, downloaded = fetch_crop(cache, urls, region, indexes, args.budget_mb)
    heights, total = height_overlay(args.world, crops, args.presence_threshold)
    result = {"metadata": {"source":"Google Open Buildings 2.5D Temporal Dataset", "year":2023,
        "source_url":DATA_SOURCE, "download_reference":MANIFEST_REFERENCE,
        "license":"CC BY-4.0", "attribution":"Google Research, Open Buildings 2.5D Temporal; derived from Copernicus Sentinel-2",
        "anchor":list(ANCHOR), "crs":projection, "manifest":manifest, "source_tiles":urls,
        "downloaded_range_bytes_this_run":downloaded, "presence_threshold":args.presence_threshold,
        "building_total":total, "buildings_with_predicted_height":len(heights),
        "generated_at":datetime.now(timezone.utc).isoformat(),
        "limitations":["2023 model predictions, not measured roof heights", "effective resolution around 4m despite 0.5m pixel grid",
            "possible roof offset; OSM footprint and Sentinel-2 rasters may be misaligned", "Global South height accuracy not quantitatively validated",
            "presence scores uncalibrated; threshold is a heuristic", "not safe flight clearance or engineering survey data"]},
        "heights":heights}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,separators=(",", ":")),encoding="utf-8")
    print(json.dumps({"output":str(args.output),"predicted_buildings":len(heights),"total_buildings":total,
                      "downloaded_range_bytes":downloaded}), flush=True)


if __name__ == "__main__":
    main()
