"""Validate the unmounted future adapter against stubbed repository interfaces."""
import importlib.util
import os
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError


class ContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.rows = {}
        self.prior = None
        self.intake = AsyncMock(return_value=SimpleNamespace(incident_id="db-incident-1"))
        parent = self

        class Conn:
            async def execute(self, query, *args):
                if "insert into swarm_operations_events" in query:
                    parent.rows[args[0]] = {"body_hash": args[3], "response": args[6]}

            async def fetchrow(self, query, *args):
                if "from swarm_operations_events where event_id" in query:
                    return parent.rows.get(args[0])
                if "from citizen_reports" in query:
                    return parent.prior
                if "from swarm_operations_events" in query:
                    return {"incident_id": "db-incident-1"}
                return None

            async def fetchval(self, query, *args):
                return 1

        @asynccontextmanager
        async def transaction():
            yield Conn()

        def module(**attrs):
            m = ModuleType("test_stub")
            m.__dict__.update(attrs)
            return m

        settings = SimpleNamespace(indradhanu_env="development")
        modules = {
            "app": module(), "app.agents": module(replan=SimpleNamespace(replan=AsyncMock())),
            "app.core": module(), "app.core.config": module(settings=settings),
            "app.core.security": module(StaffPrincipal=str),
            "app.db": module(session=SimpleNamespace(transaction=transaction)),
            "app.incidents": module(intake=SimpleNamespace(receive=self.intake)),
            "app.mesh": module(), "app.mesh.service": module(ward_at=AsyncMock(return_value="ward-1")),
            "app.taxonomy": module(cache=SimpleNamespace(categories={"person_stranded": object()})),
            "app.world": module(), "app.world.clock": module(WALL=object())}
        self.patchmods = patch.dict(sys.modules, modules)
        self.patchmods.start()
        self.env = patch.dict(os.environ, {"ENABLE_SWARM_OPERATIONS":"1", "SWARM_OPERATIONS_DEMO_DATABASE":"1",
                                         "SWARM_OPERATIONS_TOKEN":"local-test-only"})
        self.env.start()
        path = Path(__file__).resolve().parents[1] / "swarm_ops/integrations/disruptionops_patch/backend/app/drone/operations.py"
        spec = importlib.util.spec_from_file_location("test_swarm_operations_adapter", path)
        self.adapter = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.adapter
        spec.loader.exec_module(self.adapter)

    def tearDown(self):
        self.env.stop()
        self.patchmods.stop()

    def event(self):
        return self.adapter.Event(id="stable-id", run_id="runA", type="incident.observed", payload={
            "local_incident_id":"I1", "location":[73.7624378,18.6526632],
            "title":"Rooftop rescue", "people":4, "briefing_revision":1})

    async def test_duplicate_ack_does_not_intake_again(self):
        first = await self.adapter.receive(self.event(), "local-test-only")
        second = await self.adapter.receive(self.event(), "local-test-only")
        self.assertEqual(first, second)
        self.intake.assert_awaited_once()
        self.assertFalse(first["dispatch_confirmed"])

    async def test_post_intake_crash_recovery_uses_existing_report(self):
        self.prior = {"id":"report-1", "incident_id":"db-incident-1"}
        receipt = await self.adapter.receive(self.event(), "local-test-only")
        self.assertEqual(receipt["incident_id"], "db-incident-1")
        self.intake.assert_not_awaited()

    async def test_content_change_on_same_id_conflicts(self):
        await self.adapter.receive(self.event(), "local-test-only")
        changed = self.event()
        changed.payload.people = 5
        with self.assertRaises(HTTPException) as err:
            await self.adapter.receive(changed, "local-test-only")
        self.assertEqual(err.exception.status_code, 409)

    async def test_auth_and_production_guard(self):
        with self.assertRaises(HTTPException):
            self.adapter._authenticate("wrong")
        self.adapter.settings.indradhanu_env = "production"
        with self.assertRaises(HTTPException):
            self.adapter._authenticate("local-test-only")

    async def test_invalid_or_nonfinite_geo_rejected(self):
        for location in ([float("nan"),18], [73,91], [181,18]):
            with self.assertRaises(ValidationError):
                self.adapter.Observation(local_incident_id="I1", title="test", location=location)

    async def test_missing_taxonomy_is_visible(self):
        event = self.event()
        event.payload.category = "missing_medical_category"
        with self.assertRaises(HTTPException) as err:
            await self.adapter.receive(event, "local-test-only")
        self.assertEqual(err.exception.status_code, 422)
        self.intake.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
