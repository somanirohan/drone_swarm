"""Plug-in scenarios and worlds; importing this package registers them with swarm_ops.engine."""
from swarm_ops.scenarios import pcmc  # noqa: F401  (registers pcmc-region, pccoe_pawana_night, pcmc_random)

WORLDS = {pcmc.WORLD_ID: pcmc.WORLD_PATH}
