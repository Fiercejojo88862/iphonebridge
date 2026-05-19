"""Sinks — consumers of normalized iphonebridge events.

Each sink implements `handle(event)`. The daemon calls every registered
sink for every event. Failures in one sink should not affect the others.
"""
from __future__ import annotations

from typing import Protocol

from iphonebridge.events import SmsEvent


class Sink(Protocol):
    name: str

    def handle(self, event: SmsEvent) -> None: ...
