"""Structured application logging: JSON lines (ts, level, logger, run_id, sim_t, event, fields).

* ``setup_logging()`` installs a rotating JSON-lines file (``.cache/logs/app.jsonl``), a separate
  audit file (``audit.jsonl``) and a console handler (pretty when a TTY or LOG_FORMAT=pretty, else JSON).
* ``LOG_LEVEL`` env selects the level (default INFO).
* Secrets never reach a log: values registered with ``register_secret`` are masked, keys named
  token/secret/password/authorization/api_key are masked, and ``Bearer ...``, ``token=...``,
  ``#control=...`` and ``sk-...`` patterns are scrubbed from every message and field.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

REDACTED = '[redacted]'
_SECRET_KEY = re.compile(r'token|secret|password|passwd|authorization|api[_-]?key|cookie|credential', re.I)
_SECRET_TEXT = [
    (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{6,}'), r'\1' + REDACTED),
    (re.compile(r'(?i)((?:token|control|access_token|api_key|apikey|password)=)[^&\s"\']+'), r'\1' + REDACTED),
    (re.compile(r'sk-[A-Za-z0-9_-]{16,}'), REDACTED),
]
_secrets: set[str] = set()
_lock = threading.Lock()


class LogContext:
    """Process-wide run context stamped on every record (set by the telemetry service)."""
    run_id: str | None = None
    sim_t: float | None = None


def register_secret(value) -> None:
    """Mask this exact value wherever it appears in log output."""
    if isinstance(value, str) and len(value) >= 6:
        with _lock:
            _secrets.add(value)


def scrub_text(text: str) -> str:
    if _secrets:
        for secret in tuple(_secrets):
            if secret in text:
                text = text.replace(secret, REDACTED)
    for pattern, repl in _SECRET_TEXT:
        text = pattern.sub(repl, text)
    return text


def scrub(value, depth=0):
    """Return a JSON-safe copy of ``value`` with secrets masked."""
    if depth > 6:
        return '[deep]'
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {str(k): (REDACTED if _SECRET_KEY.search(str(k)) else scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [scrub(v, depth + 1) for v in list(value)[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_text(str(value))


def utc_iso(ts: float | None = None) -> str:
    moment = datetime.fromtimestamp(ts, timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        sim_t = getattr(record, 'sim_t', None)
        entry = {'ts': utc_iso(record.created), 'level': record.levelname, 'logger': record.name,
                 'run_id': getattr(record, 'run_id', None) or LogContext.run_id,
                 'sim_t': sim_t if sim_t is not None else LogContext.sim_t,
                 'event': getattr(record, 'event', None) or 'log', 'msg': scrub_text(record.getMessage())}
        fields = getattr(record, 'fields', None)
        if fields:
            entry['fields'] = scrub(fields)
        if record.exc_info:
            entry['exc'] = scrub_text(self.formatException(record.exc_info))
        return json.dumps(entry, separators=(',', ':'), default=str)


class PrettyFormatter(logging.Formatter):
    COLORS = {'DEBUG': '\033[2m', 'INFO': '\033[36m', 'WARNING': '\033[33m', 'ERROR': '\033[31m', 'CRITICAL': '\033[35m'}

    def __init__(self, color=False):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        sim_t = getattr(record, 'sim_t', None)
        sim_t = LogContext.sim_t if sim_t is None else sim_t
        stamp = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        level = f'{record.levelname:<7}'
        if self.color:
            level = f'{self.COLORS.get(record.levelname, "")}{level}\033[0m'
        sim = f' t={sim_t:.1f}' if isinstance(sim_t, (int, float)) else ''
        event = getattr(record, 'event', None)
        fields = getattr(record, 'fields', None)
        extra = ' ' + ' '.join(f'{k}={v}' for k, v in scrub(fields).items()) if fields else ''
        text = f'{stamp} {level} {record.name}{sim} {(event + ": ") if event else ""}{scrub_text(record.getMessage())}{extra}'
        if record.exc_info:
            text += '\n' + scrub_text(self.formatException(record.exc_info))
        return text


class _Only(logging.Filter):
    def __init__(self, prefix, invert=False):
        super().__init__()
        self.prefix, self.invert = prefix, invert

    def filter(self, record):
        match = record.name == self.prefix or record.name.startswith(self.prefix + '.')
        return not match if self.invert else match


def log_event(logger: logging.Logger, event: str, msg: str = '', level=logging.INFO, **fields) -> None:
    """``log_event(log, 'ws.connect', role='view')`` -> JSON line with event name and fields."""
    if logger.isEnabledFor(level):
        logger.log(level, msg or event, extra={'event': event, 'fields': fields})


def setup_logging(log_dir: Path | str | None = None, level: str | None = None, console: bool | None = None,
                  fmt: str | None = None, max_bytes: int = 5_000_000, backups: int = 5) -> Path:
    """Idempotently configure the ``swarm`` logger tree. Returns the log directory."""
    root_dir = Path(__file__).resolve().parents[2]
    log_dir = Path(log_dir) if log_dir else root_dir / '.cache' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    level_name = (level or os.environ.get('LOG_LEVEL') or 'INFO').upper()
    lvl = getattr(logging, level_name, logging.INFO)
    base = logging.getLogger('swarm')
    for handler in list(base.handlers):
        if getattr(handler, '_swarm_telemetry', False):
            base.removeHandler(handler)
            handler.close()
    base.setLevel(lvl)
    base.propagate = False

    def add(handler, formatter, flt=None):
        handler.setFormatter(formatter)
        handler.setLevel(lvl)
        handler._swarm_telemetry = True
        if flt:
            handler.addFilter(flt)
        base.addHandler(handler)

    add(RotatingFileHandler(log_dir / 'app.jsonl', maxBytes=max_bytes, backupCount=backups, encoding='utf8'),
        JsonFormatter(), _Only('swarm.audit', invert=True))
    add(RotatingFileHandler(log_dir / 'audit.jsonl', maxBytes=max_bytes, backupCount=backups, encoding='utf8'),
        JsonFormatter(), _Only('swarm.audit'))
    fmt = (fmt or os.environ.get('LOG_FORMAT') or '').lower()
    if console is None or console:
        tty = getattr(sys.stderr, 'isatty', lambda: False)()
        pretty = fmt == 'pretty' or (fmt != 'json' and tty)
        add(logging.StreamHandler(sys.stderr), PrettyFormatter(color=pretty and tty) if pretty else JsonFormatter())
    return log_dir
