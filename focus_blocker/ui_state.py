"""Small UI state helpers, independent of GTK and safe across worker callbacks."""

import math
import threading


class SessionProgress:
    """Display-only interpolation of a service-owned remaining-time sample."""

    def __init__(self):
        self.duration = 0
        self.remaining = 0
        self.sampled_at = 0

    def update(self, active, now):
        active = active or {}
        duration = active.get("duration", active.get("config", {}).get("minutes", 0) * 60)
        remaining = active.get("remaining", 0)
        self.duration = (
            duration
            if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0
            else 0
        )
        self.remaining = (
            min(self.duration, max(0, remaining))
            if isinstance(remaining, (int, float)) and math.isfinite(remaining)
            else 0
        )
        self.sampled_at = now

    def seconds(self, now):
        return max(0, self.remaining - max(0, now - self.sampled_at))

    def fraction(self, now):
        return self.seconds(now) / self.duration if self.duration else 0

    def tooltip(self, now):
        if not self.duration:
            return "Remaining time unavailable"
        seconds = math.ceil(self.seconds(now))
        if seconds >= 3600:
            return f"{seconds // 3600}:{seconds // 60 % 60:02}:{seconds % 60:02} remaining"
        return f"{seconds // 60}:{seconds % 60:02} remaining"


class CompanionState:
    """Presentation only; never grants permission or controls session deadlines."""

    def __init__(self):
        self.status = None
        self.ui_open = False
        self.opening_until = 0

    def visible(self, now):
        return bool(
            self.status
            and self.status.get("active")
            and self.status["active"].get("config", {}).get("show_animation", True)
            and not self.ui_open
            and now >= self.opening_until
        )


def application_choices(apps, selected):
    """Group launcher aliases without changing identities saved in existing presets."""
    groups = {}
    for app in apps:
        if app.get("protected"):
            continue
        if app.get("flatpak"):
            key = ("flatpak", app["flatpak"])
        elif app.get("executable"):
            key = ("native", app["executable"], app.get("script", ""))
        else:
            key = ("id", app["id"])
        groups.setdefault(key, []).append(app)
    choices = []
    for group in groups.values():
        visible = [app for app in group if app.get("visible", True)]
        aliases = {app["id"] for app in group}
        if visible or selected.intersection(aliases):
            representative = (visible or group)[0]
            choices.append({**representative, "aliases": aliases})
    return sorted(choices, key=lambda app: (app["name"].casefold(), app["id"]))


class StopAuthorization:
    def __init__(self):
        self.lock = threading.Lock()
        self.generation = 0
        self.token = None

    def invalidate(self):
        with self.lock:
            self.generation += 1
            token, self.token = self.token, None
            return token

    def accept(self, generation, token):
        with self.lock:
            if generation != self.generation:
                return False
            self.token = token
            return True

    def matches(self, generation):
        with self.lock:
            return generation == self.generation


class CompletionTracker:
    def __init__(self):
        self.waiting = False

    def update(self, status):
        if status.get("active"):
            self.waiting = True
        elif self.waiting and not status.get("release_pending"):
            self.waiting = False
            return True
        return False
