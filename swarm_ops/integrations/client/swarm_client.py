"""Standalone drop-in client for the swarm WebSocket (needs only ``pip install websockets``).

    client = SwarmClient("ws://host:8787/ops/ws", token)
    client.on_situation_report = lambda msg: print(msg["text"])
    await client.run()          # connects, auto-reconnects, resumes from the last message id

Every ``send_*`` helper returns the ack dict ({"ok": bool, "result"|"error": ...}). ``request_id`` makes
agency_request / orchestrate_incident idempotent: re-sending the same id returns the original answer.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import uuid


class SwarmClient:
    def __init__(self, url, token, *, reconnect_s=2.0):
        self.url, self.token, self.reconnect_s = url, token, reconnect_s
        self.state, self.run_id = {}, None
        self.last_message_id = 0          # resume point: messages with id <= this were already delivered
        self.on_situation_report = self.on_alert = self.on_status = self.on_snapshot = None
        self._ws, self._acks, self._ready = None, {}, asyncio.Event()
        self._stop = False

    # -- connection
    async def run(self):
        from websockets.asyncio.client import connect
        while not self._stop:
            try:
                sep = "&" if "?" in self.url else "?"
                async with connect(f"{self.url}{sep}token={urllib.parse.quote(self.token)}", max_size=None) as ws:
                    self._ws = ws
                    async for raw in ws:
                        self._on_packet(json.loads(raw))
            except Exception:  # noqa: BLE001 - reconnect on any transport error
                pass
            self._ws = None
            self._ready.clear()
            for future in self._acks.values():
                if not future.done():
                    future.set_exception(ConnectionError("connection lost"))
            if not self._stop:
                await asyncio.sleep(self.reconnect_s)

    def start(self):
        self._task = asyncio.create_task(self.run())
        return self._task

    async def wait_ready(self, timeout=10):
        await asyncio.wait_for(self._ready.wait(), timeout)

    async def close(self):
        self._stop = True
        if self._ws:
            await self._ws.close()
        task = getattr(self, "_task", None)
        if task:
            task.cancel()

    def _on_packet(self, packet):
        if packet.get("type") == "ack":
            future = self._acks.get(packet.get("id"))
            if future and not future.done():
                future.set_result(packet)
            return
        state = packet.get("state") or {}
        if "world" not in state and self.state.get("world"):
            state["world"] = self.state["world"]
        if state.get("run_id") != self.run_id:
            self.run_id, self.last_message_id = state.get("run_id"), 0
        self.state = state
        self._ready.set()
        if self.on_snapshot:
            self.on_snapshot(state)
        for message in state.get("messages", []):
            if not isinstance(message.get("id"), int) or message["id"] <= self.last_message_id:
                continue
            self.last_message_id = message["id"]
            handler = {"situation_report": self.on_situation_report, "alert": self.on_alert}.get(message["kind"], self.on_status)
            if handler:
                handler(message)

    # -- commands
    async def send(self, action, payload, command_id=None, timeout=10):
        command_id = command_id or f"sc-{uuid.uuid4().hex[:12]}"
        future = asyncio.get_running_loop().create_future()
        self._acks[command_id] = future
        try:
            await self._ws.send(json.dumps({"type": "command", "id": command_id, "action": action, "payload": payload, "expected_run_id": self.run_id}))
            return await asyncio.wait_for(future, timeout)
        finally:
            self._acks.pop(command_id, None)

    def send_agency_request(self, request_id, position, category="medical", **extra):
        return self.send("agency_request", {"request_id": request_id, "kind": extra.pop("kind", "sos"), "position": position, "category": category, **extra}, request_id)

    def send_orchestrate_incident(self, request_id, position, category, assignments, **extra):
        return self.send("orchestrate_incident", {"request_id": request_id, "position": position, "category": category, "assignments": assignments, **extra}, request_id)

    def send_assign(self, incident_id, drones=(), resources=()):
        return self.send("assign", {"incident_id": incident_id, "drones": list(drones), "resources": list(resources)})

    def send_unassign(self, incident_id, drone_ids=(), resource_ids=()):
        return self.send("unassign", {"incident_id": incident_id, "drone_ids": list(drone_ids), "resource_ids": list(resource_ids)})

    def send_set_coordination(self, mode, incident_id=None, **params):
        return self.send("set_coordination", {"mode": mode, **({"incident_id": incident_id} if incident_id else {}), **params})

    def send_recall_drone(self, drone_id, reason="recalled"):
        return self.send("recall_drone", {"drone_id": drone_id, "reason": reason})

    def send_update_incident(self, incident_id, **fields):
        return self.send("update_incident", {"incident_id": incident_id, **fields})

    def send_cancel_incident(self, incident_id, reason="cancelled"):
        return self.send("cancel_incident", {"incident_id": incident_id, "reason": reason})

    def send_close_incident(self, incident_id, reason="closed"):
        return self.send("close_incident", {"incident_id": incident_id, "reason": reason})

    def send_control(self, autonomy=None, orchestrator=None, reason=None):
        return self.send("control", {k: v for k, v in {"autonomy": autonomy, "orchestrator": orchestrator, "reason": reason}.items() if v is not None})

    def send_request_situation(self, incident_id):
        return self.send("request_situation", {"incident_id": incident_id})
