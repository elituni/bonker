"""Session state machine. All calls are serialized by the service."""

import copy
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from .rules import Invalid, configuration


class Clock:
    def wall(self):
        return time.time()

    def elapsed(self):
        return (
            time.clock_gettime(time.CLOCK_BOOTTIME)
            if hasattr(time, "CLOCK_BOOTTIME")
            else time.monotonic()
        )

    def boot(self):
        p = Path("/proc/sys/kernel/random/boot_id")
        return p.read_text().strip() if p.exists() else "development"


def digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


class Engine:
    def __init__(self, store, clock=None):
        self.store = store
        self.clock = clock or Clock()
        self.active = store.get("active")
        self.pending = None
        self.tokens = {}
        self.failures = 0
        self.retry_at = 0
        self.error = None
        if self.active and self.active["boot"] != self.clock.boot():
            remaining = max(
                0, min(self.active["duration"], self.active["ends_at"] - self.clock.wall())
            )
            self.active["boot"] = self.clock.boot()
            self.active["elapsed_start"] = self.clock.elapsed() - (
                self.active["duration"] - remaining
            )
            with self.store.db:
                self.store.put("active", self.active)
        self.tick()

    def remaining(self):
        if not self.active:
            return 0
        return max(
            0, self.active["duration"] - (self.clock.elapsed() - self.active["elapsed_start"])
        )

    def tick(self):
        if self.pending and self.clock.elapsed() >= self.pending["timeout"]:
            self.pending = None
            self.error = "Session did not start: Firefox did not acknowledge protection in time."
        if self.active and self.remaining() <= 0:
            # Expiry must release in memory even if the filesystem is full.
            self.active = None
            self.tokens.clear()
            try:
                with self.store.db:
                    self.store.put("active", None)
            except Exception:
                self.error = "Session expired; saving the expired state failed."

    def status(self):
        self.tick()
        active = copy.deepcopy(self.active)
        if active:
            active["remaining"] = self.remaining()
            active["grace_remaining"] = max(0, 180 - (active["duration"] - active["remaining"]))
        return {
            "active": active,
            "starting": bool(self.pending),
            "password_set": bool(self.store.get("password")),
            "error": self.error,
        }

    def idle(self):
        self.tick()
        if self.active or self.pending:
            raise Invalid("This setting cannot change during a session.")

    def check_password(self, value):
        if self.clock.elapsed() < self.retry_at:
            raise Invalid("Please wait a few seconds before trying the password again.")
        saved = self.store.get("password")
        if not isinstance(value, str) or len(value) > 1024:
            raise Invalid("Invalid password.")
        actual = (
            hashlib.scrypt(
                value.encode(), salt=bytes.fromhex(saved["salt"]), n=16384, r=8, p=1
            ).hex()
            if saved
            else ""
        )
        if not saved or not hmac.compare_digest(saved["hash"], actual):
            self.failures += 1
            self.retry_at = self.clock.elapsed() + min(30, 2 ** min(self.failures - 1, 5))
            raise Invalid("Incorrect current password.")
        self.failures = 0
        self.retry_at = 0

    def set_password(self, new, current=""):
        self.idle()
        if not isinstance(new, str) or not new or len(new) > 1024:
            raise Invalid("Enter a nonempty password of at most 1024 characters.")
        if self.store.get("password"):
            self.check_password(current)
        salt = secrets.token_bytes(16)
        hashed = hashlib.scrypt(new.encode(), salt=salt, n=16384, r=8, p=1).hex()
        with self.store.db:
            self.store.put("password", {"salt": salt.hex(), "hash": hashed})

    def prepare(self, raw):
        self.idle()
        if self.store.get("release_pending"):
            raise Invalid(
                "Wait for Firefox to acknowledge the previous session's release before starting another."
            )
        if not self.store.get("password"):
            raise Invalid("Set your emergency-stop password in Settings first.")
        config = configuration(raw)
        if (
            config["mode"] == "blacklist"
            and not config["apps"]
            and not any(r["action"] == "block" for r in config["rules"])
        ):
            raise Invalid("Choose at least one website or application to block.")
        self.error = None
        self.pending = {
            "id": secrets.token_hex(16),
            "config": config,
            "revision": digest(config),
            "timeout": self.clock.elapsed() + 20,
        }

    def activate(self):
        self.tick()
        if not self.pending:
            return
        config = self.pending["config"]
        active = {
            "id": self.pending["id"],
            "config": config,
            "revision": self.pending["revision"],
            "started_at": self.clock.wall(),
            "ends_at": self.clock.wall() + config["minutes"] * 60,
            "duration": config["minutes"] * 60,
            "boot": self.clock.boot(),
            "elapsed_start": self.clock.elapsed(),
        }
        with self.store.db:
            self.store.put("active", active)
        self.active = active
        self.pending = None

    def stop_edit(self):
        self.tick()
        if self.pending:
            config = self.pending["config"]
            self.pending = None
            return config
        if not self.active:
            raise Invalid("There is no active session.")
        if self.active["duration"] - self.remaining() >= 180:
            raise Invalid("The correction window has ended. Use Emergency stop.")
        config = self.active["config"]
        with self.store.db:
            self.store.put("active", None)
        self.active = None
        self.tokens.clear()
        return copy.deepcopy(config)

    def authorize(self, password):
        self.tick()
        if not self.active:
            raise Invalid("The session has already ended.")
        self.check_password(password)
        self.tick()
        if not self.active:
            raise Invalid("The session ended while checking the password.")
        token = secrets.token_urlsafe(32)
        self.tokens[token] = (self.active["id"], self.clock.elapsed() + 300)
        return token

    def revoke(self, token):
        self.tokens.pop(token, None)

    def stop_reason(self, token, reason, release_targets=None):
        self.tick()
        if not self.active:
            return {"expired": True}
        auth = self.tokens.get(token)
        if not auth or auth[0] != self.active["id"] or auth[1] <= self.clock.elapsed():
            raise Invalid("Enter your password again to authorize this stop.")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 10000:
            raise Invalid("Enter a reason (1–10,000 characters).")
        event = {
            "session_id": self.active["id"],
            "event": "release_authorized",
            "timestamp": self.clock.wall(),
            "started_at": self.active["started_at"],
            "planned_end": self.active["ends_at"],
            "duration": self.active["duration"],
            "elapsed": self.active["duration"] - self.remaining(),
            "mode": self.active["config"]["mode"],
            "preset_name": self.active["config"]["preset_name"],
            "reason": reason,
        }
        # One durable transaction: no release if recording the reason fails.
        with self.store.db:
            self.store.append(event)
            self.store.put(
                "release_pending",
                {
                    "session_id": self.active["id"],
                    "timestamp": self.clock.wall(),
                    "boot": self.clock.boot(),
                    "targets": release_targets or [],
                },
            )
            self.store.put("active", None)
        self.active = None
        self.tokens.clear()
        return {"expired": False}

    def release_confirmed(self):
        event = self.store.get("release_pending")
        if event:
            with self.store.db:
                self.store.append(
                    {
                        "session_id": event["session_id"],
                        "event": "released",
                        "timestamp": self.clock.wall(),
                    }
                )
                self.store.put("release_pending", None)

    def save_preset(self, name, raw, overwrite=False, original=None):
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise Invalid("Enter a preset name of 1–100 characters.")
        name = name.strip()
        config = configuration(raw)
        config["preset_name"] = name
        exists = self.store.db.execute("SELECT 1 FROM presets WHERE name=?", (name,)).fetchone()
        if exists and not overwrite:
            raise Invalid("That preset already exists. Choose Update preset or another name.")
        with self.store.db:
            if original and original != name:
                if exists:
                    raise Invalid("A preset with the new name already exists.")
                self.store.db.execute("DELETE FROM presets WHERE name=?", (original,))
            self.store.db.execute(
                "INSERT INTO presets VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (name, json.dumps(config)),
            )
            count, size = self.store.db.execute(
                "SELECT COUNT(*), COALESCE(SUM(length(value)+length(name)*6+64),0) FROM presets"
            ).fetchone()
            if count > 500 or size > 2 * 1024 * 1024:
                raise Invalid(
                    "Preset storage limit reached (500 presets or 2 MiB). Remove unused presets first."
                )

    def delete_preset(self, name):
        with self.store.db:
            self.store.db.execute("DELETE FROM presets WHERE name=?", (name,))
