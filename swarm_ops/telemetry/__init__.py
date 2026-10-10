"""Structured logging and run recording (see docs/TELEMETRY.md)."""
from .logging_setup import LogContext, log_event, register_secret, scrub, scrub_text, setup_logging
from .recorder import RunRecorder, read_dataset, to_csv
from .schemas import DATASETS, data_dictionary_markdown, validate
from .service import Telemetry

__all__ = ['Telemetry', 'RunRecorder', 'DATASETS', 'setup_logging', 'log_event', 'register_secret', 'scrub', 'scrub_text',
           'LogContext', 'read_dataset', 'to_csv', 'validate', 'data_dictionary_markdown']
