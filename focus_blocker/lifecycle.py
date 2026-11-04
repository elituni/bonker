"""Exclusive service lifetime lock and conservative offline removal checks."""

import fcntl
import json
import math
import os
import sqlite3
from contextlib import contextmanager

from .engine import Clock
from .rules import Invalid


@contextmanager
def exclusive_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Invalid(
                "Another service or installer operation is running. Try again when it finishes."
            ) from None
        yield
    finally:
        os.close(fd)


def require_offline_idle(database, clock=None):
    """Read only, while holding service.lock. Missing DB is a never-started install."""
    if not database.exists():
        return
    clock = clock or Clock()
    try:
        db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise Invalid("Cannot read saved state; repair the service before removal.") from exc
    try:
        settings = dict(
            db.execute("SELECT key,value FROM settings WHERE key IN ('active','release_pending')")
        )
        if json.loads(settings.get("release_pending", "null")) is not None:
            raise Invalid(
                "Browser release is still pending. Use --repair to restore the service first."
            )
        active = json.loads(settings.get("active", "null"))
        if active is not None:
            duration = active["duration"]
            if (
                not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or not 0 < duration <= 21600
            ):
                raise ValueError("Invalid saved duration")
            remaining = (
                duration - (clock.elapsed() - active["elapsed_start"])
                if active["boot"] == clock.boot()
                else min(duration, active["ends_at"] - clock.wall())
            )
            if not math.isfinite(remaining) or remaining > 0:
                raise Invalid(
                    "A saved session is still active. Use --repair, or wait until it expires before uninstalling."
                )
    except (sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, Invalid):
            raise
        raise Invalid(
            "Cannot verify saved session state; repair the service before removal."
        ) from exc
    finally:
        db.close()
