"""Backward-compatible alias: the engine moved to ``swarm_ops/engine.py``.

``from ops_simulation import OperationsSimulation, SCENARIOS`` (and private helpers) keep working:
this module is replaced by ``swarm_ops.engine`` itself, so patches and identity checks also agree.
New code should import ``swarm_ops.engine``.
"""
import sys

import swarm_ops.engine as _engine

sys.modules[__name__] = _engine
