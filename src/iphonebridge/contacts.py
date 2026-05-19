"""Contacts cache — pull vCards from iPhone via PBAP, store in SQLite,
resolve phone numbers to display names.

PBAP API quirk (per spike/RESULTS.md §4): use `Select(location, phonebook)`
not `SetFolder`. Then `PullAll(targetfile, filters)`.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import dbus

from iphonebridge import config
from iphonebridge.bus import obex
from iphonebridge.events import normalize_phone
from iphonebridge.obex.sessions import SessionManager

log = logging.getLogger(__name__)


# ---- vCard parsing ------------------------------------------------------

_VCARD_BLOCK = re.compile(
    r"BEGIN:VCARD(?P<body>.*?)END:VCARD", re.DOTALL | re.IGNORECASE
)

def _parse_vcards(blob: str) -> list[tuple[str | None, list[str]]]:
    """Return [(full_name, [phone_norm, ...]), ...]."""
    out: list[tuple[str | None, list[str]]] = []
    for m in _VCARD_BLOCK.finditer(blob):
        body = m.group("body")
        fn: str | None = None
        phones: list[str] = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.upper().startswith("FN:"):
                fn = line[3:].strip() or None
            elif line.upper().startswith("TEL"):
                # forms: TEL:1234, TEL;TYPE=CELL:1234, TEL;TYPE=CELL,VOICE:1234
                _, _, val = line.partition(":")
                norm = normalize_phone(val)
                if norm:
                    phones.append(norm)
        if fn or phones:
            out.append((fn, phones))
    return out


# ---- SQLite schema ------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name    TEXT NOT NULL,
    updated_at   REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS phones (
    phone_norm   TEXT NOT NULL,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    UNIQUE(phone_norm, contact_id)
);
CREATE INDEX IF NOT EXISTS idx_phones_norm ON phones(phone_norm);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def _open_db() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.CONTACTS_DB)
    conn.executescript(_SCHEMA)
    return conn


# ---- PBAP pull ----------------------------------------------------------

def pull_phonebook(sessions: SessionManager, *, max_contacts: int = 65535) -> int:
    """Pull the iPhone's main phonebook over PBAP and return contact count.

    Replaces the local cache atomically (transaction).
    """
    pbap = obex(sessions.pbap_path, "org.bluez.obex.PhonebookAccess1")
    log.info("PBAP Select(int, pb)")
    pbap.Select("int", "pb")

    out = Path(tempfile.mkdtemp(prefix="iphonebridge_pb_")) / "pb.vcf"
    log.info("PBAP PullAll → %s (max=%d)", out, max_contacts)
    ret = pbap.PullAll(
        str(out),
        {"MaxListCount": dbus.UInt16(max_contacts),
         "Format": dbus.String("Vcard30")},
    )
    transfer_path = str(ret[0]) if isinstance(ret, (tuple, list)) else str(ret)

    # Wait for transfer to complete (poll properties)
    tprops = obex(transfer_path, "org.freedesktop.DBus.Properties")
    for _ in range(600):  # up to 60s for huge phonebooks
        try:
            status = str(tprops.Get("org.bluez.obex.Transfer1", "Status"))
        except dbus.exceptions.DBusException:
            status = "gone"
            break
        if status in ("complete", "error"):
            break
        time.sleep(0.1)
    log.info("transfer status: %s, file size: %d bytes",
             status, out.stat().st_size if out.exists() else 0)

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("PBAP transfer wrote no file")

    blob = out.read_text(errors="replace")
    parsed = _parse_vcards(blob)
    log.info("parsed %d contacts from %d bytes", len(parsed), out.stat().st_size)

    now = time.time()
    with closing(_open_db()) as db:
        with db:  # transaction
            db.execute("DELETE FROM contacts")
            db.execute("DELETE FROM phones")
            for fn, phones in parsed:
                if not fn and not phones:
                    continue
                cur = db.execute(
                    "INSERT INTO contacts(full_name, updated_at) VALUES (?, ?)",
                    (fn or "", now),
                )
                cid = cur.lastrowid
                for p in phones:
                    db.execute(
                        "INSERT OR IGNORE INTO phones(phone_norm, contact_id) "
                        "VALUES (?, ?)",
                        (p, cid),
                    )
            db.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES "
                "('last_pull', ?), ('count', ?)",
                (str(now), str(len(parsed))),
            )

    # Clean up the temp file
    try:
        out.unlink()
        out.parent.rmdir()
    except OSError:
        pass

    return len(parsed)


# ---- Lookup -------------------------------------------------------------

class ContactsResolver:
    """In-process cache + SQLite-backed resolver. Cheap to construct.

    The instance is the stable handle held by event listeners — call
    `refresh()` to reload from disk in place, don't replace the object,
    otherwise bound `resolve` methods become stale.
    """

    def __init__(self) -> None:
        self._mem: dict[str, str] = {}
        self._warm()

    def _warm(self) -> None:
        try:
            with closing(_open_db()) as db:
                for phone, name in db.execute(
                    "SELECT p.phone_norm, c.full_name "
                    "FROM phones p JOIN contacts c ON c.id = p.contact_id "
                    "WHERE c.full_name != ''"
                ):
                    self._mem[phone] = name
        except sqlite3.Error as e:
            log.warning("contacts cache warm failed: %s", e)

    def refresh(self) -> int:
        """Re-read the SQLite cache into memory. Returns new count."""
        self._mem.clear()
        self._warm()
        return len(self._mem)

    def resolve(self, raw: str | None) -> str | None:
        norm = normalize_phone(raw)
        if not norm:
            return None
        # Match exact, or suffix-match (US numbers might be stored 10 vs 11 digit)
        if norm in self._mem:
            return self._mem[norm]
        # Try the last 10 digits (covers US +1 mismatches)
        if len(norm) > 10:
            tail = norm[-10:]
            for k, v in self._mem.items():
                if k.endswith(tail):
                    return v
        return None

    def count(self) -> int:
        return len(self._mem)
