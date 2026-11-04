"""Unprivileged GNOME login helper for completion notifications with the UI closed."""

import concurrent.futures
import json
import logging
import os
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GioUnix", "2.0")
from gi.repository import Gio, GioUnix, GLib  # noqa: E402

from .client import ServiceError, call  # noqa: E402
from .ui_state import CompanionState, CompletionTracker  # noqa: E402


def main():
    config = json.loads(Path("/etc/focus-blocker/config.json").read_text())
    if config["uid"] != os.getuid():
        return
    app = Gio.Application(
        application_id="org.focusblocker.Notifier", flags=Gio.ApplicationFlags.DEFAULT_FLAGS
    )
    app.register(None)
    if app.get_is_remote():
        return
    companion = None
    view = CompanionState()
    ui_backend = os.environ.get("GDK_BACKEND")

    def render():
        if companion:
            companion.update(view.status if view.visible(time.monotonic()) else None)

    def open_ui():
        view.opening_until = time.monotonic() + 5
        render()
        try:
            desktop = GioUnix.DesktopAppInfo.new_from_filename(
                "/usr/local/share/applications/org.focusblocker.App.desktop"
            )
            if desktop is None:
                raise RuntimeError("Bonker desktop launcher is missing")
            context = companion.window.get_display().get_app_launch_context()
            # The decoration needs XWayland; the main GTK4 UI does not.
            if ui_backend:
                context.setenv("GDK_BACKEND", ui_backend)
            else:
                context.unsetenv("GDK_BACKEND")
            desktop.launch([], context)
        except (GLib.Error, RuntimeError) as exc:
            view.opening_until = 0
            render()
            logging.warning("Cannot open Bonker: %s", exc)

    try:
        from .companion import SessionAnimation

        companion = SessionAnimation(activate=open_ui)
    except (ImportError, ValueError, RuntimeError, GLib.Error) as exc:
        logging.warning("Bonker animation unavailable: %s", exc)

    def ui_presence(opened):
        view.ui_open = opened
        render()

    watch = Gio.bus_watch_name(
        Gio.BusType.SESSION,
        "org.focusblocker.App",
        Gio.BusNameWatcherFlags.NONE,
        lambda *_: ui_presence(True),
        lambda *_: ui_presence(False),
    )
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    completion = CompletionTracker()
    pending = False
    app.hold()

    def poll():
        nonlocal pending
        if pending:
            return True
        pending = True
        future = pool.submit(call, "status")

        def done(result):
            def deliver():
                nonlocal pending
                pending = False
                try:
                    status = result.result()
                except ServiceError:
                    view.status = None
                    render()
                    return False
                view.status = status
                render()
                if completion.update(status):
                    notification = Gio.Notification.new("Bonker session ended")
                    notification.set_icon(Gio.ThemedIcon.new("org.focusblocker.App"))
                    app.send_notification("session-ended", notification)
                return False

            GLib.idle_add(deliver)

        future.add_done_callback(done)
        return True

    app.connect("activate", lambda *_: None)
    GLib.timeout_add_seconds(2, poll)
    poll()
    try:
        app.run([])
    finally:
        Gio.bus_unwatch_name(watch)
        if companion:
            companion.close()
        pool.shutdown(wait=False)


if __name__ == "__main__":
    main()
