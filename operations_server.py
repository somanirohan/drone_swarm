"""Backward-compatible entry point: the server moved to ``swarm_ops/server.py``.

    python operations_server.py --port 8787 --fleet 20     (old command, still works)
    python -m swarm_ops.server --port 8787 --fleet 20      (preferred, from the repository root)
"""
import sys

import swarm_ops.server as _server

if __name__ == '__main__':
    _server.main()
else:
    sys.modules[__name__] = _server
