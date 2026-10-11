"""Deployable entry point: ``swarm-ops`` / ``python -m swarm_ops.cli``.

Reads its configuration from the environment (see ``swarm_ops.config``) with command-line overrides, then runs the
same OperationsServer as ``python -m swarm_ops.server``. Differences that matter for a deployment:

* binds HOST:PORT from the environment (Render injects PORT);
* cross-origin WebSocket clients are allowed only when their Origin is listed in ALLOWED_ORIGINS (same-origin and
  non-browser clients such as DisruptionOps, which sends no Origin, are always fine);
* adds two read-only JSON routes: ``/ops/config`` (secret-free configuration summary) and ``/ops/data-source``
  (whether reference data came live from DisruptionOps or from the shipped assets);
* never writes the operator token to disk when OPS_CONTROL_TOKEN is supplied, and never prints it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from http import HTTPStatus
from urllib.parse import unquote, urlsplit

from swarm_ops import ROOT
from swarm_ops.config import Settings, configure_logging
from swarm_ops.server import OperationsServer

log = logging.getLogger("swarm_ops")


class DeployedServer(OperationsServer):
    """OperationsServer + configured origin policy + config/data-source routes."""

    def __init__(self, engine, settings: Settings, reference: dict, **kwargs):
        super().__init__(engine, **kwargs)
        self.public_demo = settings.public_demo
        self.settings = settings
        self.reference = reference
        if settings.integration_token:
            self.integration_token = settings.integration_token

    def http(self, connection, request):
        path = unquote(urlsplit(request.path).path)
        if path == "/ops/ws":
            origin = request.headers.get("Origin")
            if not self.settings.origin_allowed(origin, request.headers.get("Host")):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Origin not allowed. Add it to ALLOWED_ORIGINS."})
            return None
        if path == "/ops/config":
            return self._json(HTTPStatus.OK, self.settings.public_summary())
        if path == "/ops/data-source":
            ref = self.reference
            counts = ref["taxonomy"].get("counts", {})
            return self._json(HTTPStatus.OK, {
                "mode": ref["mode"], "source": ref["source"], "fetched_at": ref["fetched_at"], "errors": ref["errors"],
                "taxonomy_counts": counts, "taxonomy_commit": (ref["taxonomy"].get("source") or {}).get("commit"),
                "catalog_units": len(ref["catalog"].get("resources", [])), "catalog_lifelines": len(ref["catalog"].get("lifelines", [])),
                "open_incidents": len(ref.get("incidents", []))})
        return super().http(connection, request)


async def run(settings: Settings, frame_hz: int) -> None:
    import swarm_ops.scenarios  # noqa: F401  registers pcmc-region and its scenarios
    from swarm_ops.disruptionops_client import load_reference_data
    from swarm_ops.engine import OperationsSimulation

    reference = await asyncio.to_thread(load_reference_data, settings)
    log.info("reference data: %s from %s", reference["mode"], reference["source"])
    world_path = ROOT / "assets" / "operations" / "pune-world.json"
    world = json.loads(world_path.read_text(encoding="utf8")) if world_path.exists() else None
    scenario = settings.scenario or ("pccoe_pawana_night" if settings.world == "pcmc-region" else "rooftop_flood")
    engine = OperationsSimulation(world=world, seed=settings.seed, fleet_size=settings.fleet, scenario=scenario)
    server = DeployedServer(engine, settings, reference, host=settings.host, port=settings.port, frame_hz=frame_hz,
                            control_token=settings.control_token or None)
    (ROOT / ".cache" / "operations").mkdir(parents=True, exist_ok=True)
    await server.start()
    for problem in settings.problems():
        log.warning(problem)
    log.info("swarm-ops listening on %s:%s (world=%s fleet=%s scenario=%s) %s", settings.host, server.port, settings.world,
             settings.fleet, scenario, json.dumps(settings.public_summary()))
    if not settings.control_token:
        log.info("OPS_CONTROL_TOKEN not set: a random operator token is used; open http://localhost:%s on this machine "
                 "to be granted control.", server.port)
    try:
        await asyncio.Future()
    finally:
        await server.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Swarm operations server (configuration from the environment).")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--fleet", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--world", choices=("pune-district", "pcmc-region"))
    parser.add_argument("--scenario")
    parser.add_argument("--frame-hz", type=int, default=10)
    parser.add_argument("--print-config", action="store_true", help="print the secret-free configuration and exit")
    args = parser.parse_args(argv)
    try:
        base = Settings.from_env()
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    overrides = {k: v for k, v in {"host": args.host, "port": args.port, "fleet": args.fleet, "seed": args.seed,
                                   "world": args.world, "scenario": args.scenario}.items() if v is not None}
    settings = Settings(**{**base.__dict__, **overrides})
    configure_logging(settings.log_level)
    if args.print_config:
        print(json.dumps(settings.public_summary(), indent=1))
        return 0
    try:
        asyncio.run(run(settings, args.frame_hz))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
