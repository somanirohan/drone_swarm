"""Node base class and the runtime that runs the nodes concurrently.

Two ways to run the same nodes:

* ``async`` (live server): one asyncio task per node. Each task owns its node's inbox, handles messages
  as they arrive and ticks at the node's own cadence; nodes never call each other.
* ``inline`` (headless, tests, deterministic): ``NodeRuntime.pump()`` steps every node in a fixed
  order until all inboxes and outboxes are empty. Same node code, same messages.
"""
from __future__ import annotations

import asyncio
from collections import deque

# Simulator unit kind -> id of the agency node that owns units of that kind.
NODE_OF_KIND = {"ambulance": "ambulance_service", "fire_engine": "fire_service", "rescue": "rescue_service", "boat": "rescue_service",
                "police": "police_service", "engineering": "public_works", "pump": "public_works", "water_tanker": "public_works",
                "supply_truck": "public_works", "bus": "public_works"}


def node_for_kind(kind):
    return NODE_OF_KIND.get(kind, "public_works")


class Node:
    """A message-driven participant. Subclasses implement ``handle`` (one message) and ``tick`` (periodic)."""

    id = "node"
    label = "node"
    cadence = 1.0          # simulated seconds between ticks
    wall_period = 0.1      # seconds an async task sleeps between inbox checks

    def __init__(self, runtime):
        self.rt = runtime
        self.sim = runtime.sim
        self.outbox = deque()
        self.remote_until = -1.0       # an external process attached for this node while sim time < remote_until
        self._last_tick = -1e9
        self.stats = {"received": 0, "sent": 0, "ticks": 0}

    # -- messaging (the only way nodes interact)
    def send(self, to, kind, text, incident_id=None, request_id=None, body=None):
        self.outbox.append((to, kind, text, incident_id, request_id, body))

    def flush(self):
        count = 0
        while self.outbox:
            to, kind, text, incident_id, request_id, body = self.outbox.popleft()
            self.rt.bus.post(self.id, to, kind, text, incident_id, request_id, body)
            self.stats["sent"] += 1
            count += 1
        return count

    @property
    def remote(self):
        return self.sim.t < self.remote_until

    # -- life cycle
    def handle(self, message):  # pragma: no cover - overridden
        pass

    def tick(self):  # pragma: no cover - overridden
        return False

    def wants_tick(self):
        return False

    def due(self):
        return self.sim.t - self._last_tick >= self.cadence

    def step(self):
        """Drain the inbox, handle each message, tick when due, flush the outbox. True if anything happened."""
        messages = self.rt.bus.drain(self.id)
        self.stats["received"] += len(messages)
        if not self.remote:
            for message in messages:
                self.handle(message)
        did = False
        if self.due() or self.wants_tick():
            self._last_tick = self.sim.t
            self.stats["ticks"] += 1
            if not self.remote:
                did = bool(self.tick())
        return bool(messages) or did or bool(self.flush())

    def describe(self):
        return {"id": self.id, "label": self.label, "remote": self.remote, **self.stats}


class NodeRuntime:
    """Builds the nodes for one simulation and steps them (inline) or runs them as asyncio tasks."""

    def __init__(self, sim):
        self.sim = sim
        self.mode = "inline"
        self.tasks = {}
        self._generation = 0
        self.build()

    def build(self):
        from .agencies import AGENCY_CLASSES
        from .orchestrator import OrchestratorNode
        from .swarm import SwarmNode
        self.bus = self.sim.bus
        self.swarm = SwarmNode(self)
        self.orchestrator = OrchestratorNode(self)
        self.agencies = {cls.id: cls(self) for cls in AGENCY_CLASSES}
        self.nodes = [self.swarm, self.orchestrator, *self.agencies.values()]
        self.by_id = {node.id: node for node in self.nodes}

    def reset(self):
        """New run: rebuild the nodes against the new bus (async tasks keep running and pick them up)."""
        self.build()
        self._generation += 1

    def pump(self, rounds=16):
        """Inline mode: step all nodes until quiet. A no-op while the async tasks run."""
        if self.mode == "async":
            return 0
        steps = 0
        for _ in range(rounds):
            busy = False
            for node in list(self.nodes):
                busy |= node.step()
            steps += 1
            if not busy:
                break
        return steps

    # -- async mode
    async def start(self):
        if self.mode == "async":
            return self
        self.mode = "async"
        for node_id in self.by_id:
            self.tasks[node_id] = asyncio.create_task(self._loop(node_id), name=f"node:{node_id}")
        return self

    async def _loop(self, node_id):
        while True:
            node = self.by_id.get(node_id)
            try:
                if node is not None:
                    node.step()
            except Exception as error:  # a node failure must not stop the others
                self.sim.decisions.record(node_id, "node_error", f"{type(error).__name__}: {error}"[:200])
            await asyncio.sleep(node.wall_period if node is not None else 0.1)

    async def stop(self):
        tasks, self.tasks = list(self.tasks.values()), {}
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.mode = "inline"

    def snapshot(self):
        return {"mode": self.mode, "nodes": [node.describe() for node in self.nodes],
                "orchestrator": {"enabled": self.orchestrator.enabled, "mode": "internal" if self.orchestrator.enabled else "external"}}
