"""Environment-driven configuration for the swarm operations server.

One place that reads the process environment, so Docker, Render and a local shell all configure the
service the same way. Secret values are never logged or returned by ``public_summary()``.

    HOST                    bind address (default 127.0.0.1 locally; 0.0.0.0 in the container)
    PORT                    listen port (Render injects $PORT; default 8787)
    OPS_CONTROL_TOKEN       operator token for control commands (random per start if unset)
    OPS_INTEGRATION_TOKEN   token DisruptionOps uses for agency_request / external_resource_update
    ALLOWED_ORIGINS         comma separated browser Origins allowed to open /ops/ws cross-origin
    FLEET, WORLD, SEED, SCENARIO
    DISRUPTIONOPS_URL, DISRUPTIONOPS_TOKEN   live reference-data mode (see disruptionops_client.py)
    LOG_LEVEL
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def _int(env: dict, name: str, default: int, low: int, high: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {value}")
    return value


def normalise_origin(origin: str) -> str:
    """'https://Foo.example/' -> 'https://foo.example' (scheme + host + port only)."""
    origin = origin.strip().strip("'\"").rstrip("/")
    if not origin:
        return ""
    parts = urlsplit(origin)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"ALLOWED_ORIGINS entry {origin!r} needs a scheme and host, e.g. https://example.com")
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8787
    control_token: str = field(default="", repr=False)
    integration_token: str = field(default="", repr=False)
    public_demo: bool = False
    allowed_origins: tuple[str, ...] = ()
    fleet: int = 20
    world: str = "pune-district"
    seed: int = 42
    scenario: str | None = None
    disruptionops_url: str = ""
    disruptionops_token: str = field(default="", repr=False)
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        origins = tuple(o for o in (normalise_origin(x) for x in env.get("ALLOWED_ORIGINS", "").split(",")) if o)
        world = env.get("WORLD", "pune-district").strip() or "pune-district"
        if world not in ("pune-district", "pcmc-region"):
            raise ValueError(f"WORLD must be 'pune-district' or 'pcmc-region', got {world!r}")
        url = env.get("DISRUPTIONOPS_URL", "").strip().rstrip("/")
        if url and urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("DISRUPTIONOPS_URL must start with http:// or https://")
        level = env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if level not in logging.getLevelNamesMapping():
            raise ValueError(f"LOG_LEVEL {level!r} is not a logging level")
        return cls(
            host=env.get("HOST", "").strip() or "127.0.0.1",
            port=_int(env, "PORT", 8787, 0, 65535),
            control_token=env.get("OPS_CONTROL_TOKEN", ""),
            integration_token=env.get("OPS_INTEGRATION_TOKEN", ""),
            public_demo=env.get("OPS_PUBLIC_DEMO", "").strip().lower() in ("true", "1", "yes"),
            allowed_origins=origins,
            fleet=_int(env, "FLEET", 20, 1, 60),
            world=world,
            seed=_int(env, "SEED", 42, 0, 2**31 - 1),
            scenario=env.get("SCENARIO", "").strip() or None,
            disruptionops_url=url,
            disruptionops_token=env.get("DISRUPTIONOPS_TOKEN", ""),
            log_level=level,
        )

    @property
    def live_data(self) -> bool:
        return bool(self.disruptionops_url)

    def problems(self) -> list[str]:
        """Warnings an operator should read before exposing this publicly."""
        out = []
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            if not self.control_token:
                out.append("OPS_CONTROL_TOKEN is unset on a public bind: a random token is generated per start and "
                           "is not retrievable by remote operators; set it to a long random value.")
            elif len(self.control_token) < 24:
                out.append("OPS_CONTROL_TOKEN is shorter than 24 characters.")
            if not self.integration_token:
                out.append("OPS_INTEGRATION_TOKEN is unset: DisruptionOps cannot send agency_request.")
            elif len(self.integration_token) < 24:
                out.append("OPS_INTEGRATION_TOKEN is shorter than 24 characters.")
        return out

    def public_summary(self) -> dict:
        """Safe to log or serve: secrets appear only as booleans."""
        return {
            "host": self.host, "port": self.port, "world": self.world, "fleet": self.fleet, "seed": self.seed,
            "scenario": self.scenario, "allowed_origins": list(self.allowed_origins),
            "control_token_set": bool(self.control_token), "integration_token_set": bool(self.integration_token),
            "public_demo": self.public_demo,
            "disruptionops_url": self.disruptionops_url or None, "disruptionops_token_set": bool(self.disruptionops_token),
            "live_data": self.live_data, "log_level": self.log_level,
        }

    def origin_allowed(self, origin: str | None, host_header: str | None) -> bool:
        """Same-origin always passes; any other Origin must be listed (or '*' semantics are NOT supported)."""
        if not origin:
            return True
        try:
            normalised = normalise_origin(origin)
        except ValueError:
            return False
        if host_header and urlsplit(normalised).netloc == host_header.strip().lower():
            return True
        return normalised in self.allowed_origins


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def load() -> Settings:
    return Settings.from_env()
