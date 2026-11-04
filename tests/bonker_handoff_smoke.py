"""Inert cross-process GTK4/GTK3 handoff on a real session bus; no enforcement/input injection."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
UI_ID = "org.focusblocker.HandoffWindow"
NOTIFIER_ID = "org.focusblocker.HandoffNotifier"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--worker", choices=["ui", "notifier"])
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    state = args.directory / "fake-status.json"
    events = args.directory / "events.jsonl"

    def record(event, **data):
        with events.open("a") as output:
            output.write(json.dumps({"event": event, **data}) + "\n")

    def save(status):
        temporary = state.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(status))
        temporary.replace(state)

    def command(worker):
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            "--directory",
            str(args.directory),
            "--worker",
            worker,
        ]

    def fake_call(action, **kw):
        status = json.loads(state.read_text())
        if action == "status":
            return status
        if action == "prepare":
            status["active"] = {
                "id": "FAKE",
                "remaining": 60,
                "ends_at": time.time() + 60,
                "grace_remaining": 60,
                "config": kw["config"],
            }
            save(status)
            return {"starting": True, "active": None}
        if action in ("apps", "presets"):
            return []
        if action == "audit":
            return {"records": [], "through": 0, "next_before": None}
        raise AssertionError("No real service call allowed: " + action)

    if args.worker == "ui":
        from focus_blocker import ui

        ui.call = fake_call
        app = ui.Application()
        app.set_application_id(UI_ID)
        app.register(None)
        app.activate()
        window = app.get_active_window()
        record("ui-open", backend=type(window.get_display()).__name__)

        def commands():
            status = json.loads(state.read_text())
            if status.pop("start_requested", False):
                save(status)
                window.begin({"minutes": 1, "mode": "blacklist", "apps": ["FAKE"], "rules": []})
            if status.get("close_requested"):
                window.close()
                return False
            return True

        ui.GLib.timeout_add(100, commands)
        app.run([])
        record("ui-closed")
        return

    if args.worker == "notifier":
        requested_backend = os.environ.get("GDK_BACKEND")
        from focus_blocker import companion, notifier

        # The harness imports the widget early to instrument it. Production
        # captures the UI environment before this lazy GTK3/XWayland import.
        assert companion.Gtk.init_check()[0]
        if requested_backend is None:
            os.environ.pop("GDK_BACKEND", None)
        else:
            os.environ["GDK_BACKEND"] = requested_backend
        current = []
        original = companion.SessionAnimation

        class TrackedAnimation(original):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                current.append(self)
                self.previous_visible = False

            def update(self, status):
                super().update(status)
                visible = self.window.get_visible()
                if visible != self.previous_visible:
                    record("animation", visible=visible)
                    self.previous_visible = visible

        factory = notifier.Gio.Application
        watcher = notifier.Gio.bus_watch_name

        def application(**kw):
            kw["application_id"] = NOTIFIER_ID
            app = factory(**kw)
            action = notifier.Gio.SimpleAction.new("test-click", None)
            action.connect("activate", lambda *_: current[0].activate())
            app.add_action(action)
            return app

        def watch(bus, _name, *rest):
            return watcher(bus, UI_ID, *rest)

        class Launcher:
            def launch(self, _files, context):
                env = dict(item.split("=", 1) for item in context.get_environment())
                subprocess.Popen(command("ui"), env=env)
                record("launch-ui", backend=env.get("GDK_BACKEND"))

        real_gio = notifier.Gio

        class GioProxy:
            Application = staticmethod(application)
            bus_watch_name = staticmethod(watch)

            def __getattr__(self, name):
                return getattr(real_gio, name)

        with (
            patch.object(companion, "SessionAnimation", TrackedAnimation),
            patch.object(notifier, "call", fake_call),
            patch.object(notifier, "Gio", GioProxy()),
            patch.object(
                notifier,
                "GioUnix",
                SimpleNamespace(
                    DesktopAppInfo=SimpleNamespace(new_from_filename=lambda _: Launcher())
                ),
            ),
        ):
            notifier.main()
        return

    events.write_text("")
    idle = {
        "active": None,
        "starting": False,
        "password_set": True,
        "policy_ready": True,
        "firefox_connected": True,
        "release_pending": False,
    }
    save(idle)
    processes = []

    def wait_for(event, count=1, **matches):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            rows = [json.loads(line) for line in events.read_text().splitlines()]
            if (
                sum(
                    row["event"] == event and all(row.get(k) == v for k, v in matches.items())
                    for row in rows
                )
                >= count
            ):
                return
            time.sleep(0.1)
        raise AssertionError(f"Timed out waiting for {event} {matches}: {rows}")

    try:
        processes.append(subprocess.Popen(command("notifier"), start_new_session=True))
        processes.append(subprocess.Popen(command("ui"), start_new_session=True))
        wait_for("ui-open")
        save({**idle, "start_requested": True})
        wait_for("ui-closed")
        wait_for("animation", visible=True)
        # Activate a test-only D-Bus action, not synthetic system mouse input.
        subprocess.run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                NOTIFIER_ID,
                "--object-path",
                "/org/focusblocker/HandoffNotifier",
                "--method",
                "org.gtk.Actions.Activate",
                "test-click",
                "[]",
                "{}",
            ],
            check=True,
        )
        wait_for("ui-open", count=2)
        wait_for("animation", visible=False)
        status = json.loads(state.read_text())
        assert status["active"], "Fake service must still own an active session"
        time.sleep(0.5)
        assert (
            sum(
                json.loads(line)["event"] == "ui-closed" for line in events.read_text().splitlines()
            )
            == 1
        )
        save({**status, "close_requested": True})
        wait_for("ui-closed", count=2)
        wait_for("animation", count=2, visible=True)
        save(idle)
        wait_for("animation", count=2, visible=False)
        rows = [json.loads(line) for line in events.read_text().splitlines()]
        assert all(
            row["backend"].endswith("WaylandDisplay") for row in rows if row["event"] == "ui-open"
        )
        print(
            "PASS: real cross-process close-on-start, animation, click-action reopen, native Wayland UI, close-return and expiry presentation. FAKE service only."
        )
        print(events.read_text())
    finally:
        for process in processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
