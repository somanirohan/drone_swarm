"""Local disaster operations server: simulation, static 3D UI and bounded WS feed.

Simulation-only controls. This service does not command physical aircraft or
agencies. Optional DisruptionOps integration is explicitly configured separately.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import gzip
import hashlib
import json
import mimetypes
import os
import secrets
import sqlite3
import time
from collections import OrderedDict, deque
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from swarm_ops.telemetry import Telemetry, setup_logging
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

ROOT = Path(__file__).resolve().parents[1]  # repository root (swarm_ops/..)
PROTOCOL = 'disruptionops.swarm.v2'
COMMANDS = {'random_scenario', 'scenario', 'pause', 'resume', 'speed', 'reset',
            'fault', 'release_resource', 'ack_handoff', 'fleet', 'detect', 'random_incident', 'set_coordination'}
INTEGRATION_COMMANDS = {'external_resource_update', 'agency_request', 'control', 'orchestrate_incident', 'assign', 'unassign',
                        'recall_drone', 'cancel_incident', 'close_incident', 'update_incident',
                        'request_situation', 'node_attach', 'node_detach', 'node_order', 'node_post', 'set_coordination'}
OLD_WEB_PATHS = {'/web/operations.html': '/web/ops/operations.html',
                 '/web/operations.css': '/web/ops/operations.css',
                 '/web/operations.js': '/web/ops/operations.js',
                 '/web/index.html': '/web/legacy/index.html'}


class EventJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (cursor INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(run_id,event_id))')
        self.db.commit()

    def append(self, run_id, events):
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO events(run_id,event_id,payload) VALUES(?,?,?)',
                                [(str(run_id), str(e['id']), json.dumps({'run_id': run_id, **e}, separators=(',', ':'))) for e in events if 'id' in e])

    def replay(self, after=0, limit=100):
        rows = self.db.execute('SELECT cursor,payload FROM events WHERE cursor>? ORDER BY cursor LIMIT ?', (max(0, after), min(500, max(1, limit)))).fetchall()
        return [{'cursor': cursor, **json.loads(payload)} for cursor, payload in rows]

    def close(self):
        self.db.close()


class OperationsServer:
    def __init__(self, engine, *, control_token=None, journal_path=None, host='127.0.0.1', port=8787, frame_hz=10, public_demo=False):
        self.engine = engine
        self.public_demo = public_demo
        self.control_token = control_token or secrets.token_urlsafe(32)
        self.integration_token = os.environ.get('OPS_INTEGRATION_TOKEN', '')
        self.host, self.port = host, port
        self.frame_hz = max(1, min(20, frame_hz))
        self.clients = {}
        self.seq = 0
        self.acks = OrderedDict()
        self.journal = EventJournal(journal_path or ROOT / '.cache' / 'operations' / 'events.sqlite3')
        self._journal_seen = set()
        self._journal_run = None
        self._listener = None
        self._tick_task = None
        self.bridge = None
        self._bridge_task = None
        # Run recorder + audit logs. Tests that pass journal_path keep their data beside it.
        runs_dir = (Path(journal_path).parent / 'runs') if journal_path else ROOT / '.cache' / 'runs'
        self.telemetry = Telemetry(runs_dir, secrets=(self.control_token, self.integration_token))

    def _role(self, request, params=None):
        if self.public_demo:
            return 'operator'
        token = request.headers.get('Authorization', '')[7:] if request.headers.get('Authorization', '').startswith('Bearer ') else (params or {}).get('token', [''])[0]
        if token and secrets.compare_digest(token.encode(), self.control_token.encode()):
            return 'operator'
        if token and self.integration_token and secrets.compare_digest(token.encode(), self.integration_token.encode()):
            return 'integration'
        return 'view'

    def _json(self, status, data):
        return self._response(status, json.dumps(data, separators=(',', ':')).encode(), 'application/json', cache=False)

    @staticmethod
    def _response(status, body, content_type, cache=False, compressed=False):
        headers = Headers([('Content-Type', content_type), ('Content-Length', str(len(body))),
                           ('Cache-Control', 'public, max-age=86400' if cache else 'no-store'),
                           ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer')])
        if compressed:
            headers['Content-Encoding'] = 'gzip'
            headers['Vary'] = 'Accept-Encoding'
        return Response(status.value, status.phrase, headers, body)

    def _local_operator(self, connection, request):
        remote = connection.remote_address
        try:
            hostname = urlsplit('//' + request.headers.get('Host', '')).hostname
        except ValueError:
            return False
        forwarded = any(h in request.headers for h in ('CF-Connecting-IP', 'CF-Ray', 'X-Forwarded-For', 'Forwarded'))
        return bool(remote and remote[0] in ('127.0.0.1', '::1') and hostname in ('localhost', '127.0.0.1', '::1') and not forwarded)

    def http(self, connection, request):
        url = urlsplit(request.path)
        path = unquote(url.path)
        if path == '/ops/runs' or path.startswith('/ops/runs/') or path == '/ops/telemetry':
            query = parse_qs(url.query)
            role = self._role(request, query)
            self.telemetry.connection('http.request', role, connection.remote_address, path=path)
            if role == 'view':
                return self._json(HTTPStatus.FORBIDDEN, {'error': 'Operator or integration token required for run datasets.'})
            if path == '/ops/telemetry':
                return self._json(HTTPStatus.OK, self.telemetry.stats())
            status, body, kind = self.telemetry.handle(path, query)
            if kind == 'json':
                return self._json(HTTPStatus(status), body)
            return self._response(HTTPStatus(status), body, kind)
        if path == '/ops/ws':
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc.lower() != request.headers.get('Host', '').lower():
                return self._json(HTTPStatus.FORBIDDEN, {'error': 'Cross-origin WebSocket connections are not permitted.'})
            return None
        if path == '/health':
            return self._json(HTTPStatus.OK, {'ok': True, 'protocol': PROTOCOL, 'simulation_only': True, 'clients': len(self.clients), 'integration': bool(self.bridge)})
        if path == '/ops/session':
            if self.public_demo:
                return self._json(HTTPStatus.OK, {'can_control': True, 'public_demo': True, 'simulation_only': True})
            if not self._local_operator(connection, request):
                return self._json(HTTPStatus.OK, {'can_control': False, 'simulation_only': True})
            return self._json(HTTPStatus.OK, {'control_token': self.control_token, 'can_control': True, 'simulation_only': True})
        if path == '/ops/state':
            return self._json(HTTPStatus.OK, self.engine.snapshot())
        if path == '/ops/scenarios':
            from swarm_ops.engine import scenario_catalog
            return self._json(HTTPStatus.OK, {'scenarios': scenario_catalog(), 'current': self.engine.scenario.get('id'),
                                              'world_id': getattr(self.engine, 'world_id', None)})
        if path == '/ops/events':
            params = parse_qs(url.query)
            try:
                after, limit = int(params.get('after', ['0'])[0]), int(params.get('limit', ['100'])[0])
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {'error': 'after and limit must be integers'})
            events = self.journal.replay(after, limit)
            return self._json(HTTPStatus.OK, {'events': events, 'next_cursor': events[-1]['cursor'] if events else after})
        if path == '/ops/messages':
            # Command-center <-> agent message log (engine `messages`); paged by message id: ?after=<id>&limit=<n>.
            params = parse_qs(url.query)
            try:
                limit = min(500, max(1, int(params.get('limit', ['100'])[0])))
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {'error': 'limit must be an integer'})
            messages = getattr(self.engine, 'messages', None)
            if messages is None:
                messages = self.engine.snapshot(include_world=False).get('messages', [])
            after = params.get('after', [''])[0]
            if after:
                ids = [m.get('id') for m in messages]
                messages = messages[ids.index(after) + 1:] if after in ids else messages
            page = list(messages[:limit])
            return self._json(HTTPStatus.OK, {'run_id': self.engine.run_id, 'messages': page, 'next_after': page[-1].get('id') if page else after,
                                              'available': hasattr(self.engine, 'messages') or 'messages' in self.engine.snapshot(include_world=False)})
        if path in ('/', '/index.html', '/operations'):
            path = '/web/ops/operations.html'
        if path == '/legacy':
            path = '/web/legacy/index.html'
        # Old URLs from before the web/ops + web/legacy split; already-shared links keep working.
        path = OLD_WEB_PATHS.get(path, path)
        prefix, _, tail = path.lstrip('/').partition('/')
        if prefix not in ('web', 'assets'):
            return self._json(HTTPStatus.NOT_FOUND, {'error': 'not found'})
        base = ROOT / prefix
        target = (base / tail).resolve()
        if base not in target.parents or not target.is_file():
            return self._json(HTTPStatus.NOT_FOUND, {'error': 'not found'})
        content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if target.suffix == '.js':
            content_type = 'text/javascript'
        data = target.read_bytes()
        compress = 'gzip' in request.headers.get('Accept-Encoding', '') and target.suffix in ('.js', '.json', '.css', '.html')
        if compress:
            data = gzip.compress(data, compresslevel=3)
        return self._response(HTTPStatus.OK, data, content_type, cache=('vendor' in target.parts), compressed=compress)

    def _packet(self, state, include_world=False):
        if not include_world:
            state = {k: v for k, v in state.items() if k != 'world'}
        return json.dumps({'type': 'snapshot', 'protocol': PROTOCOL, 'seq': self.seq,
                           'server_time': time.time(), 'state': state}, separators=(',', ':'))

    async def websocket(self, ws):
        params = parse_qs(urlsplit(ws.request.path).query)
        token = params.get('token', [''])[0]
        can_control = self.public_demo or bool(token and secrets.compare_digest(token.encode(), self.control_token.encode()))
        can_integrate = self.public_demo or bool(self.integration_token and token and secrets.compare_digest(token.encode(), self.integration_token.encode()))
        role = 'operator' if can_control else 'integration' if can_integrate else 'view'
        self.telemetry.connection('ws.connect', role, ws.remote_address)
        state = self.engine.snapshot()
        queue = asyncio.Queue(maxsize=2)
        self.clients[ws] = {'queue': queue, 'run_id': state['run_id']}

        async def writer():
            while True:
                packet, delivered_run = await queue.get()
                await ws.send(packet)
                if ws in self.clients:
                    self.clients[ws]['run_id'] = delivered_run

        sender = asyncio.create_task(writer())
        recent_commands = deque()
        try:
            await ws.send(self._packet(state, include_world=True))
            async for raw in ws:
                if not isinstance(raw, str):
                    await ws.close(1003, 'JSON text required')
                    break
                now = time.monotonic()
                while recent_commands and now - recent_commands[0] > 1:
                    recent_commands.popleft()
                if len(recent_commands) >= 10:
                    await ws.close(1008, 'Command rate exceeded')
                    break
                recent_commands.append(now)
                ack = self.apply_command(raw, can_control=can_control, can_integrate=can_integrate)
                await ws.send(json.dumps(ack, separators=(',', ':')))
        except ConnectionClosed:
            pass
        finally:
            self.clients.pop(ws, None)
            self.telemetry.connection('ws.disconnect', role, ws.remote_address)
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionClosed):
                await sender

    def apply_command(self, raw, *, can_control=False, can_integrate=False):
        started = time.perf_counter()
        ack = self._apply_command(raw, can_control=can_control, can_integrate=can_integrate)
        try:
            message = json.loads(raw)
            message = message if isinstance(message, dict) else {}
        except (TypeError, ValueError):
            message = {}
        role = 'operator' if can_control else 'integration' if can_integrate else 'view'
        self.telemetry.audit(str(message.get('action')), role, message.get('payload'), ack.get('ok'), ack.get('error'),
                             (time.perf_counter() - started) * 1000, message.get('id'), ack.get('incident_id'))
        return ack

    def _apply_command(self, raw, *, can_control=False, can_integrate=False):
        try:
            message = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON number')))
        except (TypeError, ValueError):
            return {'type': 'ack', 'id': None, 'ok': False, 'error': 'Invalid JSON'}
        if not isinstance(message, dict):
            return {'type': 'ack', 'id': None, 'ok': False, 'error': 'Command must be an object'}
        command_id = message.get('id')
        action, payload = message.get('action'), message.get('payload', {})
        ack = {'type': 'ack', 'id': command_id, 'ok': False}
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            return {**ack, 'error': 'A unique command id of 1–128 characters is required'}
        if message.get('type') != 'command' or not isinstance(action, str) or not isinstance(payload, dict):
            return {**ack, 'error': 'Invalid command envelope'}
        permitted = action in COMMANDS and can_control or action in INTEGRATION_COMMANDS and can_integrate
        if not permitted:
            return {**ack, 'error': 'Operator authorization required' if action in COMMANDS | INTEGRATION_COMMANDS else 'Unknown action'}
        identity = ('integration' if action in INTEGRATION_COMMANDS else 'operator', command_id)
        fingerprint = hashlib.sha256(json.dumps({'action': action, 'payload': payload, 'expected_run_id': message.get('expected_run_id')}, sort_keys=True).encode()).hexdigest()
        if identity in self.acks:
            old_fingerprint, old_ack = self.acks[identity]
            if fingerprint != old_fingerprint:
                return {**ack, 'error': 'Command id already used with a different payload'}
            return old_ack
        expected_run = message.get('expected_run_id')
        if expected_run and expected_run != self.engine.run_id:
            return {**ack, 'error': 'Scenario changed; refresh state before issuing this command'}
        try:
            result = self.engine.command(action, payload)
            if isinstance(result, dict) and result.get('ok') is False:
                ack['error'] = str(result.get('error', 'Command rejected'))
            else:
                ack.update(ok=True, result=result)
                if isinstance(result, dict) and result.get('incident_id'):
                    ack['incident_id'] = result['incident_id']
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            ack['error'] = str(exc)
        self.acks[identity] = fingerprint, ack
        while len(self.acks) > 2000:
            self.acks.popitem(last=False)
        return ack

    async def ticker(self):
        last, next_frame = time.monotonic(), time.monotonic()
        while True:
            started = time.monotonic()
            dt = min(.25, max(0, started - last))
            last = started
            self.engine.step(dt)
            self._step_ms = (time.monotonic() - started) * 1000
            if started >= next_frame:
                next_frame = started + 1 / self.frame_hz
                self.seq += 1
                state = self.engine.snapshot(include_world=False)
                if any(client['run_id'] != state['run_id'] for client in self.clients.values()):
                    state['world'] = self.engine.world
                if state['run_id'] != self._journal_run:
                    self._journal_run = state['run_id']
                    self._journal_seen.clear()
                unseen = [e for e in state.get('events', []) if str(e['id']) not in self._journal_seen]
                if unseen:
                    self.journal.append(state['run_id'], unseen)
                    self._journal_seen = {str(e['id']) for e in state.get('events', [])}
                self.telemetry.observe(state, self.engine)
                regular_packet = self._packet(state) if self.clients else None
                world_packet = None
                for ws, client in list(self.clients.items()):
                    changed_run = client['run_id'] != state['run_id']
                    queue = client['queue']
                    if queue.full():
                        queue.get_nowait()  # telemetry coalesces; durable events are replayable
                    if changed_run and world_packet is None:
                        world_packet = self._packet(state, include_world=True)
                    queue.put_nowait((world_packet if changed_run else regular_packet, state['run_id']))
            await asyncio.sleep(max(.001, .05 - (time.monotonic() - started)))

    async def start(self):
        self._listener = await serve(self.websocket, self.host, self.port, process_request=self.http,
                                     max_size=16384, max_queue=8, ping_interval=20, ping_timeout=30,
                                     close_timeout=3, compression='deflate', write_limit=32768)
        self.port = self._listener.sockets[0].getsockname()[1]
        self._tick_task = asyncio.create_task(self.ticker())
        if hasattr(self.engine, 'nodes'):
            await self.engine.nodes.start()
        return self

    async def stop(self):
        if hasattr(self.engine, 'nodes'):
            await self.engine.nodes.stop()
        if self._bridge_task:
            self._bridge_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._bridge_task
        if self._tick_task:
            self._tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._tick_task
        if self._listener:
            self._listener.close()
            await self._listener.wait_closed()
        self.journal.close()
        self.telemetry.close()


async def main_async(args):
    from swarm_ops.engine import OperationsSimulation
    import swarm_ops.scenarios  # noqa: F401  registers pcmc-region and its scenarios
    world_path = ROOT / 'assets' / 'operations' / 'pune-world.json'
    world = json.loads(world_path.read_text(encoding='utf8')) if world_path.exists() else None
    # --world pcmc-region starts on a PCMC scenario; built-in scenarios keep the Pune district world.
    scenario = args.scenario or ('pccoe_pawana_night' if args.world == 'pcmc-region' else 'rooftop_flood')
    setup_logging()
    engine = OperationsSimulation(world=world, seed=args.seed, fleet_size=args.fleet, scenario=scenario, coordination=args.coordination)
    server = OperationsServer(engine, host=args.host, port=args.port, frame_hz=args.frame_hz,
                              control_token=os.environ.get('OPS_CONTROL_TOKEN'))
    runtime_dir = ROOT / '.cache' / 'operations'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # Private local operator link; the token is never returned to public tunnel viewers.
    (runtime_dir / 'operator-url.txt').write_text(f'http://localhost:{args.port}/#control={server.control_token}', encoding='utf8')
    await server.start()
    print(f'Operations viewer: http://localhost:{server.port}', flush=True)
    print(f'Operator link saved locally: {runtime_dir / "operator-url.txt"}', flush=True)
    print('Simulation only. Engine and assets run on this hardware; public viewers are read-only.', flush=True)
    try:
        await asyncio.Future()
    finally:
        await server.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--fleet', type=int, default=20)
    parser.add_argument('--frame-hz', type=int, default=10)
    parser.add_argument('--world', choices=('pune-district', 'pcmc-region'), default='pune-district')
    parser.add_argument('--scenario', default=None, help='starting scenario id (see /ops/scenarios)')
    parser.add_argument('--coordination', choices=('central', 'decentralized', 'orchestrated'), default='central',
                        help='swarm coordination: central auction (default), decentralized CBBA consensus, or orchestrated by DisruptionOps')
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
