"""Normalized event types emitted by the OBEX layer.

The iPhone's MAP server speaks bMessages and proprietary metadata;
the rest of the daemon shouldn't have to care. Everything upstream
sees these simple dataclasses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

# ---- helpers ------------------------------------------------------------

_PHONE_KEEP = re.compile(r"\D")

def normalize_phone(raw: str | None) -> str | None:
    """Reduce a phone string to digits only (E.164-ish minus the +).

    "+1 (561) 235-1044" → "15551234567"
    "5612351044"        → "5612351044"
    "Mom"               → None  (looked like a name)
    """
    if not raw:
        return None
    digits = _PHONE_KEEP.sub("", raw)
    # 7+ digits is plausibly a phone number
    return digits if len(digits) >= 7 else None


def parse_map_timestamp(ts: str | None) -> datetime | None:
    """Parse MAP's timestamp format: '20260519T181423' or with timezone suffix."""
    if not ts:
        return None
    # MAP timestamps: YYYYMMDDTHHMMSS, optionally followed by a TZ offset
    base = ts[:15]
    try:
        dt = datetime.strptime(base, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    # MAP timestamps are local-time on the iPhone; we'll treat as local
    return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


# ---- event types --------------------------------------------------------

EventKind = Literal["sms_received", "sms_seen"]


@dataclass(slots=True)
class SmsEvent:
    """A single SMS message event from the iPhone via MAP."""

    kind: EventKind
    handle: str                 # BlueZ obex Message1 path tail, e.g. "message93446842893444124"
    sender_phone: str | None    # raw, as given by MAP
    sender_phone_norm: str | None  # digits-only, for contacts lookup
    contact_name: str | None    # resolved from contacts cache, may be None
    body: str | None            # MAP puts the SMS text in `Subject`
    timestamp: datetime | None
    is_read: bool
    raw_status: str | None
    raw_type: str | None
    # Full BlueZ obex DBus path to the Message1 object, so downstream
    # code (e.g. libnotify sink) can write back read-state.
    message_path: str | None = None
    seen_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def display_sender(self) -> str:
        """Best name we have for the sender."""
        return self.contact_name or self.sender_phone or "(unknown)"

    def to_dict(self) -> dict:
        """Serializable form for JSONL log."""
        return {
            "kind": self.kind,
            "handle": self.handle,
            "sender_phone": self.sender_phone,
            "sender_phone_norm": self.sender_phone_norm,
            "contact_name": self.contact_name,
            "body": self.body,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "is_read": self.is_read,
            "raw_status": self.raw_status,
            "raw_type": self.raw_type,
            "seen_at": self.seen_at.isoformat(),
        }


def sms_event_from_message1_props(
    handle: str, props: dict, contact_name: str | None = None,
) -> SmsEvent:
    """Construct an SmsEvent from BlueZ's org.bluez.obex.Message1 properties.

    See spike/RESULTS.md §3 — the SMS body comes from `Subject`.
    """
    sender_raw = props.get("Sender") or props.get("SenderAddress")
    sender_raw = str(sender_raw) if sender_raw is not None else None
    norm = normalize_phone(sender_raw)
    return SmsEvent(
        kind="sms_received",
        handle=handle,
        sender_phone=sender_raw,
        sender_phone_norm=norm,
        contact_name=contact_name,
        body=str(props.get("Subject", "")) or None,
        timestamp=parse_map_timestamp(props.get("Timestamp")),
        is_read=bool(props.get("Read", False)),
        raw_status=str(props.get("Status", "")) or None,
        raw_type=str(props.get("Type", "")) or None,
    )
