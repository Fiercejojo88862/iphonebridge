"""Long-lived MAP + PBAP OBEX sessions.

Per spike/RESULTS.md §2: the iPhone refuses repeat OBEX connects within a
short window. The daemon keeps one MAP session and one PBAP session open
for its lifetime, reopening only on observed failure.
"""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass

import dbus
import dbus.exceptions

from iphonebridge import config
from iphonebridge.bus import obex

log = logging.getLogger(__name__)


class SessionError(RuntimeError):
    pass


@dataclass(slots=True)
class ObexSession:
    """A live OBEX session against the iPhone (Target = "MAP" or "PBAP")."""

    target: str               # "MAP" or "PBAP"
    path: str                 # /org/bluez/obex/client/session{N}

    @property
    def message_access(self) -> dbus.Interface:
        return obex(self.path, "org.bluez.obex.MessageAccess1")

    @property
    def phonebook(self) -> dbus.Interface:
        return obex(self.path, "org.bluez.obex.PhonebookAccess1")

    @property
    def properties(self) -> dbus.Interface:
        return obex(self.path, "org.freedesktop.DBus.Properties")


def _client() -> dbus.Interface:
    return obex("/org/bluez/obex", "org.bluez.obex.Client1")


def _restart_obexd() -> None:
    """Restart user obex.service to clear stale state — see spike/RESULTS.md §2."""
    log.info("restarting user obex.service for a clean state")
    subprocess.run(["systemctl", "--user", "restart", "obex.service"],
                   check=False)
    time.sleep(1.0)


def _create_session(target: str, *, mac: str | None = None,
                    retry_on_forbidden: bool = True) -> ObexSession:
    mac = mac or config.IPHONE_MAC
    log.info("creating OBEX session (Target=%s) to %s", target, mac)
    try:
        path = str(_client().CreateSession(
            mac, {"Target": target}, timeout=30.0
        ))
        return ObexSession(target=target, path=path)
    except dbus.exceptions.DBusException as e:
        msg = e.get_dbus_message() or ""
        if retry_on_forbidden and ("Forbidden" in msg or "0x43" in msg):
            log.warning("OBEX %s got Forbidden — restarting obexd and "
                        "retrying once", target)
            _restart_obexd()
            return _create_session(target, mac=mac, retry_on_forbidden=False)
        raise SessionError(f"CreateSession({target}) to {mac} failed: {e.get_dbus_name()}: {msg}")


class SessionManager:
    """Opens and tracks one MAP and one PBAP session for the daemon lifetime.

    Multi-device: pass a specific ``mac`` to operate on one iPhone; the
    default (``None``) uses ``config.IPHONE_MAC`` (first in ``IPHONE_MACS``)
    for backward compat. For daemon multi-device, create one manager per MAC
    or use the new ``MultiSessionManager`` below.
    """

    def __init__(self, mac: str | None = None) -> None:
        self.mac = mac or config.IPHONE_MAC
        self.map: ObexSession | None = None
        self.pbap: ObexSession | None = None

    def open_all(self) -> None:
        # Restart obexd once at start to give us a known-clean baseline.
        # Idempotent — even if obexd was fine, this just re-creates it.
        _restart_obexd()
        self.map = _create_session("MAP", mac=self.mac)
        log.info("MAP session (%s): %s", self.mac, self.map.path)
        self.pbap = _create_session("PBAP", mac=self.mac)
        log.info("PBAP session (%s): %s", self.mac, self.pbap.path)

    def close_all(self) -> None:
        client = _client()
        for sess in (self.map, self.pbap):
            if sess is None:
                continue
            try:
                client.RemoveSession(sess.path)
                log.info("closed %s session (%s): %s", sess.target, self.mac, sess.path)
            except dbus.exceptions.DBusException as e:
                log.debug("RemoveSession(%s): %s", sess.path, e.get_dbus_name())
        self.map = None
        self.pbap = None

    # Convenience accessors
    @property
    def map_path(self) -> str:
        if self.map is None:
            raise SessionError(f"MAP session not open for {self.mac}")
        return self.map.path

    @property
    def pbap_path(self) -> str:
        if self.pbap is None:
            raise SessionError(f"PBAP session not open for {self.mac}")
        return self.pbap.path


class MultiSessionManager:
    """Manages one SessionManager per MAC in config.IPHONE_MACS."""

    def __init__(self, macs: list[str] | None = None) -> None:
        macs = macs if macs is not None else list(config.IPHONE_MACS)
        # Filter placeholder
        self.macs = [m for m in macs if m.upper() != config.PLACEHOLDER_MAC]
        self.managers: dict[str, SessionManager] = {m: SessionManager(m) for m in self.macs}

    def open_all(self) -> None:
        _restart_obexd()
        for mac, mgr in self.managers.items():
            try:
                mgr.map = _create_session("MAP", mac=mac)
                log.info("MAP session (%s): %s", mac, mgr.map.path)
            except SessionError as e:
                log.warning("MAP open failed for %s: %s", mac, e)
            try:
                mgr.pbap = _create_session("PBAP", mac=mac)
                log.info("PBAP session (%s): %s", mac, mgr.pbap.path)
            except SessionError as e:
                log.warning("PBAP open failed for %s: %s", mac, e)

    def close_all(self) -> None:
        for mgr in self.managers.values():
            mgr.close_all()

    def get(self, mac: str) -> SessionManager | None:
        return self.managers.get(mac.upper()) or self.managers.get(mac)

    @property
    def primary(self) -> SessionManager | None:
        """First manager (backward compat)."""
        return next(iter(self.managers.values()), None)
