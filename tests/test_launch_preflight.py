"""Launcher preflight against a real isolated HTTP/WebSocket server."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from swarm_ops.engine import OperationsSimulation
from swarm_ops.server import OperationsServer

spec = importlib.util.spec_from_file_location('launch_preflight',
    Path(__file__).resolve().parents[1]/'scripts'/'verify-swarm-connection.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.engine = OperationsSimulation(fleet_size=5, coordination='decentralized')
        self.engine.paused = True
        self.server = OperationsServer(self.engine, port=0,
            journal_path=Path(self.folder.name)/'events.sqlite3')
        self.server.integration_token = 'test-only-integration-token-12345'
        await self.server.start()
        self.base = f'http://127.0.0.1:{self.server.port}'

    async def asyncTearDown(self):
        await self.server.stop()
        self.folder.cleanup()

    async def test_real_permissions_snapshot_and_no_incident_dispatch(self):
        count = len(self.engine.incidents)
        result = await preflight.verify(self.base, self.server.integration_token)
        self.assertTrue(result['integration_authenticated'])
        self.assertTrue(result['viewer_read_only'])
        self.assertEqual(result['fleet'],5)
        self.assertEqual(result['coordination'],'decentralized')
        self.assertEqual(len(self.engine.incidents),count)
        self.assertTrue(self.engine.paused)

    async def test_wrong_token_is_not_reported_as_ready(self):
        with self.assertRaisesRegex(ValueError,'authentication'):
            await preflight.verify(self.base,'wrong-test-only-token')

    async def test_external_ground_authority_is_acknowledged(self):
        await preflight.verify(self.base,self.server.integration_token,orchestrator='external')
        self.assertFalse(self.engine.nodes.orchestrator.enabled)
        await preflight.verify(self.base,self.server.integration_token,orchestrator='internal')
        self.assertTrue(self.engine.nodes.orchestrator.enabled)

    async def test_public_verification_rejects_operator_session_exposure(self):
        with self.assertRaisesRegex(ValueError,'Public session'):
            await preflight.verify(self.base,self.server.integration_token,require_public=True)

    async def test_invalid_base_url_rejected(self):
        with self.assertRaisesRegex(ValueError,'HTTP'):
            await preflight.verify('file:///private',self.server.integration_token)
