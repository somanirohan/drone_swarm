"""Pune disaster-operations simulator: engine, WebSocket/HTTP server and optional integrations.

Modules:
    swarm_ops.engine        simulation engine (was ops_simulation.py)
    swarm_ops.server        local HTTP + WebSocket server (was operations_server.py)
    swarm_ops.integrations  optional DisruptionOps bridge and patch proposal

Run the server from the repository root: python -m swarm_ops.server --port 8787 --fleet 20
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # repository root (assets/, web/, .cache/ live here)
