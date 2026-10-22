"""Multi-node architecture of the operations system.

Every node is an independent participant that runs concurrently, owns its own state and talks to the
others ONLY through messages on the bus (``swarm_ops.agents.MessageBus``):

    SwarmNode          drones: decide their own aircraft work, REPORT the situation and alerts
    OrchestratorNode   standalone stand-in for DisruptionOps: allocates from situation reports
    AmbulanceService / FireService / RescueService / PoliceService / PublicWorks
                       agencies: own their units, choose which unit answers a dispatch, report status

The engine simulates world physics only (movement on roads and in the air). See
docs/AGENTIC-ASSESSMENT.md and docs/implementation/websocket-protocol.md.
"""
from .base import Node, NodeRuntime, node_for_kind, NODE_OF_KIND  # noqa: F401
from .agencies import AgencyNode  # noqa: F401
