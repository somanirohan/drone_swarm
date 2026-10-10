"""Live browser streaming for the drone swarm: HTTP viewer plus a WebSocket state feed.

One port serves the static viewer (web/, assets/) and the WebSocket at /ws so a
single tunnel or port-forward exposes everything. Viewers are read-only.
"""
import asyncio
import gzip
import json
import mimetypes
import threading
from http import HTTPStatus
from pathlib import Path
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

ROOT = Path(__file__).resolve().parents[2]  # repository root
SERVED = {'web': ROOT / 'web', 'assets': ROOT / 'assets'}
COMPRESSIBLE = {'.html', '.js', '.obj', '.json', '.css', '.mtl'}

_loop = None
_clients = set()
_scene = {}
_last_frame = None
_log = []
_gzip_cache = {}


def _respond(status, body, content_type, gz=False):
    headers = Headers([('Content-Type', content_type), ('Content-Length', str(len(body))), ('Cache-Control', 'public, max-age=3600')])
    if gz:
        headers['Content-Encoding'] = 'gzip'
    return Response(status.value, status.phrase, headers, body)


def _http(connection, request):
    path = request.path.split('?')[0]
    if path == '/ws':
        return None
    if path in ('/', '/index.html'):
        path = '/web/legacy/index.html'
    parts = path.lstrip('/').split('/', 1)
    base = SERVED.get(parts[0])
    if base is None or len(parts) < 2:
        return _respond(HTTPStatus.NOT_FOUND, b'not found', 'text/plain')
    target = (base / parts[1]).resolve()
    if base not in target.parents or not target.is_file():
        return _respond(HTTPStatus.NOT_FOUND, b'not found', 'text/plain')
    ctype = {'.js': 'text/javascript', '.obj': 'text/plain'}.get(target.suffix) or mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
    accepts_gzip = 'gzip' in request.headers.get('Accept-Encoding', '')
    if target.suffix in COMPRESSIBLE and accepts_gzip:
        if target not in _gzip_cache:
            _gzip_cache[target] = gzip.compress(target.read_bytes(), 6)
        return _respond(HTTPStatus.OK, _gzip_cache[target], ctype, gz=True)
    return _respond(HTTPStatus.OK, target.read_bytes(), ctype)


async def _handler(ws):
    _clients.add(ws)
    try:
        await ws.send(json.dumps({'type': 'init', 'scene': _scene, 'log': _log[-40:]}))
        if _last_frame:
            await ws.send(_last_frame)
        async for _ in ws:  # viewers are read-only; ignore anything they send
            pass
    finally:
        _clients.discard(ws)


async def _broadcast(message):
    for ws in list(_clients):
        try:
            await ws.send(message)
        except Exception:
            _clients.discard(ws)


async def _main(host, port, ready):
    # Disable ping timeouts so a backgrounded browser tab is not dropped mid-mission.
    async with serve(_handler, host, port, process_request=_http, max_size=2**16, ping_interval=20, ping_timeout=60, compression=None):
        ready.set()
        await asyncio.Future()


def start(host, port, scene):
    """Start the server in a background thread; returns once it is listening."""
    global _loop
    _scene.update(scene)
    ready = threading.Event()

    def run():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(_main(host, port, ready))

    threading.Thread(target=run, daemon=True).start()
    if not ready.wait(10):
        raise RuntimeError('web server failed to start')


def publish(frame):
    global _last_frame
    frame['type'] = 'frame'
    _last_frame = json.dumps(frame, separators=(',', ':'))
    if _loop and _clients:
        asyncio.run_coroutine_threadsafe(_broadcast(_last_frame), _loop)


def log(message):
    _log.append(message)
    del _log[:-200]
    if _loop and _clients:
        asyncio.run_coroutine_threadsafe(_broadcast(json.dumps({'type': 'log', 'message': message})), _loop)
