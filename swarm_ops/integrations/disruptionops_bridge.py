"""Durable client for DisruptionOps' optional /drone/operations demo adapter.

This transports simulation observations, never flight or physical dispatch commands.
No remote writes happen unless DISRUPTIONOPS_BRIDGE=1 and a token is configured.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

# Exact mapping for the three implemented demo scenarios. Existing upstream
# taxonomy has no generic medical_emergency/unsafe_crossing category: preserve
# those as unclassified reports rather than guess heat casualty/flooded road.
SCENE_CATEGORY_MAP = {"flood": "person_stranded", "medical": "unknown_report",
                      "blocked_route": "unknown_report"}


def geo_position(position, anchor=(18.6526632, 73.7624378)):
    """Local scene [east, north, up], in metres, to [longitude, latitude]."""
    if isinstance(position, dict):
        x, north = float(position.get("x", 0)), float(position.get("y", 0))
    else:
        x, north = float(position[0]), float(position[1])
    lat, lon = anchor
    return [lon + x / (111320 * math.cos(math.radians(lat))), lat + north / 111320]


def local_position(location, anchor=(18.6526632, 73.7624378)):
    lon, lat = map(float, location)
    lat0, lon0 = anchor
    return [(lon - lon0) * 111320 * math.cos(math.radians(lat0)),
            (lat - lat0) * 111320, 0]


class BridgeError(RuntimeError):
    pass


def validate_external_state(state: dict, run_id: str, anchor=(18.6526632, 73.7624378)) -> dict:
    """Validate the whole import before mutating simulator state or invoking callback."""
    def identifier(value):
        if not isinstance(value, str) or not 1 <= len(value) <= 100 or any(ord(c) < 32 for c in value):
            raise BridgeError("Invalid external identifier")
        return value

    def finite(value, low, high):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise BridgeError("Invalid external number")
        return float(value)

    if not isinstance(state, dict) or state.get("run_id") != run_id or state.get("mode") != "simulation":
        raise BridgeError("Incorrect scope or non-simulation response")
    result = {"run_id": run_id, "mode": "simulation", "source": "disruptionops",
              "incidents": [], "resources": [], "handoffs": []}
    for name, limit in (("incidents",500), ("resources",500), ("handoffs",500)):
        if not isinstance(state.get(name, []), list) or len(state.get(name, [])) > limit:
            raise BridgeError("External collection exceeds contract bounds")
        if not all(isinstance(row, dict) for row in state.get(name, [])):
            raise BridgeError("Invalid external row")
    local_ids = set()
    for row in state.get("incidents", []):
        local_id = identifier(row.get("local_id"))
        if local_id in local_ids:
            raise BridgeError("Duplicate external incident mapping")
        local_ids.add(local_id)
        status = row.get("status", "reported")
        if status not in {"reported", "confirmed", "dispatched", "in_progress", "responding", "resolved"}:
            raise BridgeError("Unknown incident status")
        resolved = row.get("resolved", status == "resolved")
        if not isinstance(resolved, bool) or resolved != (status == "resolved"):
            raise BridgeError("Inconsistent incident resolution")
        result["incidents"].append({"local_id": local_id, "external_id": identifier(row.get("external_id")),
                                     "status": status, "resolved": resolved})
    resources = {}
    for row in state.get("resources", []):
        resource_id = identifier(row.get("id"))
        if resource_id in resources:
            raise BridgeError("Duplicate external resource")
        status = row.get("state")
        if status not in {"proposed", "approved", "assigned", "en_route", "on_site", "available", "offline", "complete"}:
            raise BridgeError("Unknown resource status")
        incident_id = row.get("incident_id")
        if incident_id is not None:
            identifier(incident_id)
        location = row.get("location")
        if location is not None:
            if not isinstance(location, list) or len(location) != 2:
                raise BridgeError("Expected external [longitude, latitude]")
            location = [finite(location[0], -180, 180), finite(location[1], -90, 90)]
            position = local_position(location, anchor)
        else:
            position = row.get("position")
        if not isinstance(position, list) or len(position) != 3:
            raise BridgeError("Expected external [east, north, up]")
        position = [finite(v, -50000, 50000) for v in position]
        clean = {"id": resource_id, "kind": identifier(row.get("kind")), "state": status,
                 "position": position, "incident_id": incident_id, "eta": finite(row.get("eta",0), 0, 86400),
                 "agency": identifier(row.get("agency", "unknown")),
                 "position_source": "disruptionops_resource_table"}
        if location is not None:
            clean["location"] = location
        if row.get("assignment_id") is not None:
            clean["assignment_id"] = identifier(row["assignment_id"])
        resources[resource_id] = clean
        result["resources"].append(clean)
    for row in state.get("handoffs", []):
        incident_id, resource_id = identifier(row.get("incident_id")), identifier(row.get("resource_id"))
        revision = row.get("briefing_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or not 1 <= revision <= 1000000:
            raise BridgeError("Invalid handoff revision")
        accepted = row.get("accepted")
        if not isinstance(accepted, bool):
            raise BridgeError("Handoff acceptance must be explicit boolean")
        # Ignore historical acknowledgments whose assignment has already ended;
        # they must not free a drone currently on a different response.
        current = resources.get(resource_id)
        if accepted and (not current or current["state"] != "on_site" or current["incident_id"] != incident_id):
            continue
        result["handoffs"].append({"incident_id": incident_id, "resource_id": resource_id,
                                    "briefing_revision": revision, "accepted": accepted})
    return result


class DisruptionOpsBridge:
    def __init__(self, *, url: str, token: str, db_path: str | Path,
                 on_update: Callable | None = None, enabled=False,
                 city_id="pune", anchor=(18.6526632, 73.7624378),
                 request_fn: Callable | None = None):
        self.enabled = bool(enabled)
        self.url = url.rstrip("/")
        parsed = urlparse(self.url)
        if self.enabled and (not token or parsed.scheme not in ("http", "https") or not parsed.netloc):
            raise ValueError("Enabled bridge requires an HTTP(S) API base URL and token")
        if self.enabled and parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote bridge requires HTTPS")
        self.token, self.city_id, self.anchor = token, city_id, anchor
        self.on_update = on_update
        self._request_fn = request_fn or self._http
        self._stop = asyncio.Event()
        self.last_error = ""
        self.last_sync = 0.0
        self.active_run_id: str | None = None
        self._conn = None
        if self.enabled:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, body TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending', response TEXT, error TEXT)""")
            self._conn.commit()

    @classmethod
    def from_env(cls, *, on_update=None):
        return cls(enabled=os.getenv("DISRUPTIONOPS_BRIDGE", "0") == "1",
                   url=os.getenv("OPS_DISRUPTIONOPS_URL", os.getenv("DISRUPTIONOPS_API_URL", "")),
                   token=os.getenv("OPS_DISRUPTIONOPS_TOKEN", os.getenv("DISRUPTIONOPS_BRIDGE_TOKEN", "")),
                   city_id=os.getenv("DISRUPTIONOPS_CITY", "pune"),
                   db_path=os.getenv("DISRUPTIONOPS_OUTBOX", ".runtime/disruptionops-outbox.sqlite"),
                   on_update=on_update)

    def enqueue_event(self, event: dict) -> bool:
        if not self.enabled:
            return False
        if not event.get("id") or not event.get("run_id"):
            raise ValueError("Events require stable id and run_id")
        if len(json.dumps(event)) > 128000:
            raise ValueError("Bridge event exceeds 128 KB")
        body = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        old = self._conn.execute("SELECT body FROM outbox WHERE id=?", (str(event["id"]),)).fetchone()
        if old and old[0] != body:
            raise ValueError("Event ID reused with a different payload")
        result = self._conn.execute("INSERT OR IGNORE INTO outbox(id,run_id,body) VALUES(?,?,?)",
                                    (str(event["id"]), str(event["run_id"]), body))
        self._conn.commit()
        self.active_run_id = str(event["run_id"])
        return bool(result.rowcount)

    def enqueue_snapshot(self, snapshot: dict) -> int:
        """Export only known incidents; never oracle locations of undiscovered people.

        Repeated snapshots have the same stable event IDs and never corroborate
        themselves. Engine may use detected/discovered/observed boolean; hidden
        and unconfirmed candidates are intentionally not exported.
        """
        if not self.enabled:
            return 0
        run_id = snapshot.get("run_id")
        if not run_id:
            raise ValueError("Snapshot requires run_id")
        self.active_run_id = str(run_id)
        count = 0
        for incident in snapshot.get("incidents", []):
            known = (incident.get("detected") or incident.get("discovered") or
                     incident.get("observed") or incident.get("status") in
                     ("reported", "detected", "responding", "observing", "ground_response",
                      "confirmed", "assessed", "handed_over", "resolved"))
            if not known:
                continue
            # First observation always has revision1; briefing changes have their
            # own events so replaying a new snapshot does not change old content.
            event = observation_event(run_id=run_id, incident=incident, city_id=self.city_id)
            existing = self._conn.execute("""SELECT body FROM outbox WHERE run_id=?
                AND json_extract(body,'$.payload.local_incident_id')=?
                AND json_extract(body,'$.type')='incident.observed' LIMIT 1""",
                (str(run_id), str(incident["id"]))).fetchone()
            if existing:
                original = json.loads(existing[0])
                if (original["payload"]["briefing_revision"] == event["payload"]["briefing_revision"] and
                        original["payload"]["briefing"] == event["payload"]["briefing"]):
                    continue
                event["type"] = "drone.briefing"
                event["id"] = hashlib.sha256(f"{run_id}:{incident['id']}:brief:{event['payload']['briefing_revision']}".encode()).hexdigest()[:48]
            old = self._conn.execute("SELECT 1 FROM outbox WHERE id=?", (event["id"],)).fetchone()
            if old:
                continue
            count += self.enqueue_event(event)
        return count

    def _http(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        req = Request(self.url + path, data=data, method=method, headers={
            "X-Swarm-Operations-Token": self.token, "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=12) as response:
                # Bound decoded payloads; exceptions never echo URLs/tokens/body.
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise BridgeError("Response too large")
                return json.loads(raw)
        except HTTPError as exc:
            raise BridgeError(f"HTTP {exc.code}") from None
        except (OSError, ValueError) as exc:
            raise BridgeError(type(exc).__name__) from None

    async def _request(self, *args):
        if inspect.iscoroutinefunction(self._request_fn):
            return await self._request_fn(*args)
        return await asyncio.to_thread(self._request_fn, *args)

    async def flush_once(self, *, now=None):
        if not self.enabled:
            return 0
        now = time.time() if now is None else now
        rows = self._conn.execute("""SELECT id,body,attempts FROM outbox
            WHERE status='pending' AND next_try<=? ORDER BY rowid LIMIT 16""", (now,)).fetchall()
        delivered = 0
        for event_id, body, attempts in rows:
            try:
                response = await self._request("POST", "/drone/operations/events", json.loads(body))
                if response.get("event_id") != event_id or not response.get("accepted"):
                    raise BridgeError("Endpoint did not acknowledge matching event")
                self._conn.execute("UPDATE outbox SET status='delivered',response=?,error=NULL WHERE id=?",
                                   (json.dumps(response), event_id))
                delivered += 1
                self.last_error = ""
            except Exception as exc:
                # Permanent bad input/auth stays visible, not falsely delivered.
                self.last_error = str(exc) if isinstance(exc, BridgeError) else type(exc).__name__
                delay = min(60, 2 ** min(attempts + 1, 6))
                self._conn.execute("UPDATE outbox SET attempts=attempts+1,next_try=?,error=? WHERE id=?",
                                   (now + delay, self.last_error, event_id))
            self._conn.commit()
        return delivered

    async def poll_once(self, run_id=None):
        if not self.enabled or not (run_id or self.active_run_id):
            return None
        run_id = str(run_id or self.active_run_id)
        query = urlencode({"run_id": run_id, "city_id": self.city_id})
        state = await self._request("GET", "/drone/operations/state?" + query)
        state = validate_external_state(state, run_id, self.anchor)
        # Callback is performed before checkpoint: failures are replayed on reconnect.
        if self.on_update:
            result = self.on_update(state)
            if inspect.isawaitable(result):
                await result
        self.last_sync = time.time()
        return state

    async def sync_once(self, snapshot=None):
        if snapshot is not None:
            self.enqueue_snapshot(snapshot)
        await self.flush_once()
        return await self.poll_once()

    async def run(self):
        if not self.enabled:
            return
        while not self._stop.is_set():
            await self.flush_once()
            try:
                await self.poll_once()
            except Exception as exc:
                self.last_error = str(exc) if isinstance(exc, BridgeError) else type(exc).__name__
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    def stop(self):
        self._stop.set()

    def close(self):
        self.stop()
        if self._conn:
            self._conn.close()
            self._conn = None

    def status(self):
        pending = 0 if not self._conn else self._conn.execute(
            "SELECT count(*) FROM outbox WHERE status='pending'").fetchone()[0]
        return {"enabled": self.enabled, "pending": pending, "last_error": self.last_error,
                "last_sync": self.last_sync, "mode": "simulation", "run_id": self.active_run_id}


def observation_event(*, run_id, incident, event_id=None, city_id="pune", kind="incident.observed"):
    """Build endpoint's exact typed payload from a simulator incident."""
    local_id = str(incident["id"])
    briefing = incident.get("briefing") or {}
    revision = int(incident.get("briefing_revision", briefing.get("revision", incident.get("revision", 1))))
    event_id = event_id or hashlib.sha256(f"{run_id}:{local_id}:{kind}:{revision}".encode()).hexdigest()[:48]
    return {"id": event_id, "run_id": str(run_id), "type": kind, "payload": {
        "city_id": city_id, "local_incident_id": local_id,
        "category": SCENE_CATEGORY_MAP.get(incident.get("category"), incident.get("category", "person_stranded")),
        "location": geo_position(incident["position"]),
        "title": incident.get("title", "Drone observation"),
        "people": int(incident.get("people", 0)),
        "needs": incident.get("needs", []), "briefing": incident.get("briefing", {}),
        "briefing_revision": revision, "drone_id": str(incident["drone_id"]) if incident.get("drone_id") is not None else None,
        "severity": int(incident.get("severity", 3))}}
