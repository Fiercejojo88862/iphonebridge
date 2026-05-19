"""Typer CLI entrypoints."""
from __future__ import annotations

import logging
import os
import sys

import typer

from iphonebridge import bluez_setup, config

app = typer.Typer(
    add_completion=False,
    help="iPhone ↔ Linux Bluetooth bridge for Pop!_OS.",
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


@app.command()
def run(verbose: bool = typer.Option(False, "-v", "--verbose")):
    """Start the iphonebridge daemon (runs until Ctrl+C / SIGTERM)."""
    _setup_logging(verbose)
    # Import inside command to avoid loading dbus stack just to print --help
    from iphonebridge.daemon import Daemon
    Daemon().run()


@app.command()
def doctor(verbose: bool = typer.Option(False, "-v", "--verbose")):
    """Check that all prerequisites are in place."""
    _setup_logging(verbose)
    log = logging.getLogger("doctor")

    ok = True

    # bluez-obexd present?
    if not os.path.exists("/usr/libexec/bluetooth/obexd"):
        log.error("bluez-obexd binary not found at /usr/libexec/bluetooth/obexd")
        log.error("    → sudo apt install bluez-obexd")
        ok = False
    else:
        log.info("bluez-obexd installed")

    # Adapter CoD
    cod = bluez_setup.current_cod()
    if cod is None:
        log.error("Adapter %s not reachable via DBus", config.ADAPTER)
        ok = False
    else:
        match = bluez_setup.desired_cod_matches(cod)
        if match:
            log.info("Adapter CoD = 0x%06x (A/V Hands-Free)  OK", cod)
        else:
            log.warning("Adapter CoD = 0x%06x — not A/V Hands-Free. "
                        "Run `iphonebridge run` (needs sudo) or set manually:",
                        cod)
            log.warning("    sudo btmgmt class %d %d",
                        config.COD_MAJOR, config.COD_MINOR)
            ok = False

    # State dir writable
    try:
        config.ensure_dirs()
        log.info("State dir writable: %s", config.STATE_DIR)
    except OSError as e:
        log.error("State dir not writable: %s", e)
        ok = False

    if ok:
        typer.echo(typer.style("All checks passed.", fg=typer.colors.GREEN))
    else:
        typer.echo(typer.style("One or more checks FAILED.",
                               fg=typer.colors.RED))
        raise typer.Exit(code=1)


@app.command()
def contacts_sync(verbose: bool = typer.Option(False, "-v", "--verbose")):
    """Force a fresh PBAP pull from the iPhone (rebuilds the contacts cache)."""
    _setup_logging(verbose)
    # Heavyweight — needs sessions
    from iphonebridge.contacts import pull_phonebook
    from iphonebridge.obex.sessions import SessionManager
    sm = SessionManager()
    sm.open_all()
    try:
        n = pull_phonebook(sm)
        typer.echo(f"Pulled {n} contacts into {config.CONTACTS_DB}")
    finally:
        sm.close_all()


@app.command("sms-list")
def sms_list(
    n: int = typer.Option(20, "-n", "--limit", help="Max events to show (most recent first)"),
    me: bool = typer.Option(False, "--me", help="Hide events you sent (sender == empty)"),
):
    """Show recent SMS events from the local JSONL log.

    Reads ~/.local/state/iphonebridge/events.jsonl. Only includes events
    the daemon has caught since startup — for full inbox history we'd
    need an on-demand MAP query, deferred.
    """
    import json
    from datetime import datetime

    if not config.EVENTS_JSONL.exists():
        typer.echo(typer.style(
            f"No event log yet at {config.EVENTS_JSONL}",
            fg=typer.colors.YELLOW,
        ))
        typer.echo("Is the daemon running? Try: systemctl --user status iphonebridge")
        raise typer.Exit(code=1)

    raw = config.EVENTS_JSONL.read_text(errors="replace").strip().splitlines()
    events: list[dict] = []
    for line in raw:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if me:
        events = [e for e in events if e.get("sender_phone")]

    # Most recent first, capped to n
    events = events[-n:][::-1]
    if not events:
        typer.echo("(no events)")
        return

    for e in events:
        sender = (e.get("contact_name") or e.get("sender_phone") or "?")
        body = (e.get("body") or "").replace("\n", " ⏎ ")
        if len(body) > 120:
            body = body[:119] + "…"
        ts_raw = e.get("seen_at", "")
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            ts = dt.astimezone().strftime("%m-%d %H:%M")
        except (ValueError, AttributeError):
            ts = ts_raw[:16]
        sender_styled = typer.style(f"{sender:>20s}", fg=typer.colors.CYAN, bold=True)
        ts_styled = typer.style(ts, dim=True)
        typer.echo(f"{ts_styled}  {sender_styled}  {body}")


@app.command()
def version():
    """Print version and exit."""
    from iphonebridge import __version__
    typer.echo(f"iphonebridge {__version__}")


if __name__ == "__main__":
    app()
