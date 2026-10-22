"""Optional authenticated SIMULATION adapter, disabled by default.

Mount with ENABLE_SWARM_OPERATIONS=1 on a separate DEVELOPMENT database only.
The existing intake + CP-SAT replan remain ground allocation authority.
Nothing in this module sends a flight command or accepts physical dispatch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.agents import replan
from app.core.config import settings
from app.core.security import StaffPrincipal
from app.db import session as db
from app.incidents import intake
from app.mesh.service import ward_at
from app.taxonomy import cache as taxonomy
from app.world.clock import WALL

router = APIRouter(tags=["drone-operations-demo"])


def _enabled():
    if (os.getenv("ENABLE_SWARM_OPERATIONS") != "1" or
            os.getenv("SWARM_OPERATIONS_DEMO_DATABASE") != "1" or
            settings.indradhanu_env != "development"):
        raise HTTPException(403, "Swarm adapter requires an explicitly isolated development database")


def _authenticate(token):
    _enabled()
    configured = os.getenv("SWARM_OPERATIONS_TOKEN", "")
    if not configured or not token or not hmac.compare_digest(token, configured):
        raise HTTPException(403, "Missing or incorrect swarm operations token")


class Observation(BaseModel):
    city_id: Literal["pune"] = "pune"
    local_incident_id: str = Field(min_length=1, max_length=80)
    category: str = Field(default="person_stranded", max_length=40)
    location: list[float] = Field(min_length=2, max_length=2)
    title: str = Field(max_length=200)
    people: int = Field(default=0, ge=0, le=10000)
    needs: Any = Field(default_factory=list)
    briefing: Any = Field(default_factory=dict)
    briefing_revision: int = Field(default=1, ge=1)
    drone_id: str | None = Field(default=None, max_length=80)
    severity: int = Field(default=3, ge=1, le=5)

    @field_validator("location")
    @classmethod
    def coordinates(cls, value):
        if not all(math.isfinite(v) for v in value) or not (-180 <= value[0] <= 180 and -90 <= value[1] <= 90):
            raise ValueError("Expected finite [longitude, latitude]")
        return value


class Event(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    run_id: str = Field(min_length=1, max_length=80)
    type: Literal["incident.observed", "drone.briefing", "drone.payload_delivered"]
    payload: Observation


@router.post("/drone/operations/events")
async def receive(body: Event, x_swarm_operations_token: str | None = Header(default=None)):
    _authenticate(x_swarm_operations_token)
    payload = body.payload
    encoded = json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded) > 128000:
        raise HTTPException(413, "Observation over 128 KB")
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    # Session-level locks do not work behind transaction poolers. A transaction
    # advisory lock held by this connection serializes duplicate requests while
    # intake uses its existing independent transaction. At least 2 pool slots.
    async with db.transaction() as conn:
        await conn.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", "swarm:" + body.id)
        old = await conn.fetchrow("select * from swarm_operations_events where event_id=$1", body.id)
        if old:
            if old["body_hash"] != digest:
                raise HTTPException(409, "Event ID reused with different content")
            if old["response"]:
                return old["response"]
        if payload.category not in taxonomy.categories:
            raise HTTPException(422, "Category is absent from this installation's taxonomy")
        ward = await ward_at(*payload.location, payload.city_id)
        if ward is None:
            raise HTTPException(422, "Observation is outside configured wards")
        # Recover if process died after intake committed but before ACK persisted.
        # One fixed device ID per event, never count retries as corroboration.
        device = "swarm-ops:" + hashlib.sha256(body.id.encode()).hexdigest()[:48]
        prior = await conn.fetchrow("""select id::text, incident_id::text
            from citizen_reports where device_id=$1 and city_id=$2
            order by created_at limit 1""", device, payload.city_id)
        incident_id = prior["incident_id"] if prior else None
        if not prior and body.type == "incident.observed":
            note = (f"SIMULATED drone observation: {payload.title}. "
                    f"People observed: {payload.people}; reported needs: {json.dumps(payload.needs)}; "
                    f"briefing: {json.dumps(payload.briefing)}")[:500]
            result = await intake.receive(
                ward_id=ward, category=payload.category, location=tuple(payload.location),
                note=note, source="sim", device_id=device,
                reporter_key="device:sensor:swarm-ops:" + str(payload.drone_id or "unknown"),
                reporter_name="Simulated drone fleet", city_id=payload.city_id, clock=WALL)
            incident_id = result.incident_id
        elif body.type != "incident.observed":
            mapped = await conn.fetchrow("""select incident_id::text from swarm_operations_events
                where run_id=$1 and city_id=$2 and local_incident_id=$3 and incident_id is not null
                order by created_at limit 1""", body.run_id, payload.city_id, payload.local_incident_id)
            if not mapped:
                raise HTTPException(409, "Incident must be observed before briefing or supply update")
            incident_id = mapped["incident_id"]
        response = {"accepted": True, "event_id": body.id, "run_id": body.run_id,
                    "local_incident_id": payload.local_incident_id,
                    "incident_id": incident_id, "mode": "simulation",
                    "dispatch_confirmed": False}
        await conn.execute("""insert into swarm_operations_events
            (event_id,run_id,city_id,body_hash,local_incident_id,incident_id,response,payload)
            values($1,$2,$3,$4,$5,$6::uuid,$7,$8)
            on conflict(event_id) do update set response=excluded.response""",
            body.id, body.run_id, payload.city_id, digest, payload.local_incident_id,
            incident_id, response, body.model_dump())
    # Commit mapping first, so a slow/failed allocator cannot lose the observation.
    # state() retries the existing allocator even after a dropped POST ACK.
    return response


@router.get("/drone/operations/state")
async def state(run_id: str = Query(min_length=1, max_length=80),
                city_id: Literal["pune"] = "pune",
                x_swarm_operations_token: str | None = Header(default=None)):
    _authenticate(x_swarm_operations_token)
    mappings = await db.fetch("""select distinct local_incident_id,incident_id::text external_id
        from swarm_operations_events where run_id=$1 and city_id=$2 and incident_id is not null""",
        run_id, city_id)
    if mappings:
        # Existing authoritative ground allocator; no custom parallel dispatch.
        await replan.replan(city_id=city_id, clock=WALL, trigger="drone simulation observation")
    ids = [m["external_id"] for m in mappings]
    local = {m["external_id"]: m["local_incident_id"] for m in mappings}
    statuses = await db.fetch("select id::text,status::text from incidents where id::text=any($1::text[])", ids)
    status_by_id = {r["id"]: r["status"] for r in statuses}
    rows = await db.fetch("""select r.id,r.kind,r.operator,r.status::text,
        extensions.ST_X(r.location::extensions.geometry) lon,
        extensions.ST_Y(r.location::extensions.geometry) lat,
        a.id::text assignment_id,a.incident_id::text,a.status::text assignment_status,
        a.eta_minutes,coalesce(a.progress,0)::float progress
        from resources r join assignments a on a.resource_id=r.id
        where r.city_id=$1 and a.sim_run_id is null and a.incident_id::text=any($2::text[])
        and a.status::text in ('proposed','approved','en_route','on_site')""", city_id, ids)
    resources = [{"id": r["id"], "kind": r["kind"], "state": r["assignment_status"],
        "location": [r["lon"], r["lat"]], "incident_id": local[r["incident_id"]],
        "external_incident_id": r["incident_id"], "assignment_id": r["assignment_id"],
        "eta": max(0, float(r["eta_minutes"] or 0) * (1-r["progress"]) * 60),
        "agency": r["operator"], "position_source": "disruptionops_resource_table"} for r in rows]
    handoffs = await db.fetch("""select local_incident_id incident_id,resource_id,
        briefing_revision,accepted_by,accepted_at from swarm_operations_handoffs where run_id=$1""", run_id)
    return {"run_id": run_id, "mode": "simulation", "source": "disruptionops",
            "incidents": [{"local_id": m["local_incident_id"], "external_id": m["external_id"],
                "status": status_by_id.get(m["external_id"], "reported"),
                "resolved": status_by_id.get(m["external_id"]) == "resolved"} for m in mappings],
            "resources": resources, "handoffs": [{**dict(h), "accepted": True,
                "accepted_at": h["accepted_at"].isoformat()} for h in handoffs]}


class Handoff(BaseModel):
    run_id: str = Field(min_length=1, max_length=80)
    local_incident_id: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(min_length=1, max_length=80)
    briefing_revision: int = Field(ge=1)


@router.post("/drone/operations/handoff")
async def acknowledge(body: Handoff, principal: StaffPrincipal):
    """Staff explicitly accepts the drone brief; arrival never implies ACK."""
    _enabled()
    async with db.transaction() as conn:
        assignment = await conn.fetchrow("""select a.id from assignments a
            join swarm_operations_events e on e.incident_id=a.incident_id
            where e.run_id=$1 and e.local_incident_id=$2 and a.resource_id=$3
            and a.sim_run_id is null and a.status::text='on_site' limit 1""",
            body.run_id, body.local_incident_id, body.resource_id)
        latest = await conn.fetchval("""select max((payload->'payload'->>'briefing_revision')::int)
            from swarm_operations_events where run_id=$1 and local_incident_id=$2""",
            body.run_id, body.local_incident_id)
        if not assignment or latest != body.briefing_revision:
            raise HTTPException(409, "Requires on-site assignment and current briefing revision")
        await conn.execute("""insert into swarm_operations_handoffs
            (run_id,local_incident_id,resource_id,briefing_revision,accepted_by)
            values($1,$2,$3,$4,$5) on conflict do nothing""",
            body.run_id, body.local_incident_id, body.resource_id, body.briefing_revision,
            principal.full_name or str(principal.user_id or principal.role))
    return {"accepted": True, "mode": "simulation"}
