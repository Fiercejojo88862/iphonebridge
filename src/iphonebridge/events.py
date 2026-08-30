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
# Extension markers — truncate before these when normalizing.
_PHONE_EXT_RE = re.compile(r"\s*(?:ext\.?|x)\s*\d+\s*$", re.IGNORECASE)

def normalize_phone(raw: str | None) -> str | None:
    """Canonicalize a phone string to E.164-ish digits (no ``+``).

    Handles the forms seen in MAP / PBAP / user input without pulling in
    ``phonenumbers``:

    * ``"+1 (561) 235-1044"``      → ``"15551234567"``
    * ``"0044 7700 900123"``        → ``"447700900123"`` (``00`` → ``+``)
    * ``"07700 900123"``            → ``"7700900123"`` (kept as-is; resolver
      handles trunk-``0`` vs ``+44`` via suffix matching)
    * ``"+33 6 12 34 56 78 x123"``  → ``"33612345678"`` (extension stripped)
    * ``"Mom"``                     → ``None``  (looked like a name)
    * ``"555.123.4567"``            → ``"5551234567"``

    Returns ``None`` for non-phones (<7 digits or >15 digits). E.164 max
    is 15 digits; anything longer is likely a concatenation / garbage.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    # Strip extension suffix like " x123" / " ext 123"
    s = _PHONE_EXT_RE.sub("", s).strip()
    # ``00`` international prefix → treat as ``+``
    if s.startswith("00"):
        s = "+" + s[2:]
    # Keep only digits for the canonical form (drop the leading ``+`` deliberately
    # so stored and incoming forms compare as digit strings).
    digits = _PHONE_KEEP.sub("", s)
    # E.164 bounds: 7 (minimum plausible) to 15 (max per spec)
    if not (7 <= len(digits) <= 15):
        return None
    return digits


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

EventKind = Literal["sms_received", "sms_seen", "sms_sent"]


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


def sms_sent_event(
    recipient: str,
    body: str,
    *,
    contact_name: str | None = None,
    transfer_path: str = "",
) -> SmsEvent:
    """Build an SmsEvent for a message *we* just sent via MAP PushMessage.

    For a sent message the relevant party is the recipient, so the
    `sender_*` / `contact_name` fields carry the recipient — that keeps it
    in the same conversation thread as incoming messages from that person.
    """
    handle = (transfer_path.rsplit("/", 1)[-1] if transfer_path
              else f"sent-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}")
    return SmsEvent(
        kind="sms_sent",
        handle=handle,
        sender_phone=recipient,
        sender_phone_norm=normalize_phone(recipient),
        contact_name=contact_name,
        body=body,
        timestamp=datetime.now().astimezone(),
        is_read=True,
        raw_status="sent",
        raw_type="sms_sent",
        message_path=None,
    )


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
