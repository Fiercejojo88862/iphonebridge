"""JSONL event log sink.

Appends one JSON object per line to ~/.local/state/iphonebridge/events.jsonl.
Useful for debugging, replay-tuning future correlator logic, and as the
durable record before SQLite catches up.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from iphonebridge import config
from iphonebridge.events import SmsEvent

log = logging.getLogger(__name__)


class JsonlSink:
    name = "jsonl"

    def __init__(self, path: Path | None = None) -> None:
        config.ensure_dirs()
        self.path = path or config.EVENTS_JSONL
        log.info("jsonl sink → %s", self.path)

    def handle(self, event: SmsEvent) -> None:
        self._append(event.to_dict())

    def handle_ancs(self, event) -> None:
        """Same JSONL, kind: 'ancs_notification' instead of 'sms_received'."""
        self._append(event.to_dict())

    def _append(self, payload: dict) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as e:
            log.error("jsonl write failed: %s", e)
