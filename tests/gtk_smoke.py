"""Real GTK rendering with an inert fake service; never enforcement validation.

Run on GNOME: .venv/bin/python tests/gtk_smoke.py --capture-dir /tmp/focus-ui
Repeat with GDK_SCALE=2, and --theme light/dark. No host policy or service is used.
"""

import argparse
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker import ui  # noqa: E402

STATUS = {
    "active": None,
    "starting": False,
    "password_set": False,
    "error": None,
    "policy_ready": False,
    "firefox_connected": False,
    "protection_error": None,
}
CONFIG = {
    "minutes": 25,
    "mode": "blacklist",
    "rules": [
        {"host": "youtube.com", "action": "block", "subdomains": True},
        {"host": "music.youtube.com", "action": "allow", "subdomains": True},
        {
            "host": "a-very-long-hostname-for-layout-inspection.example.com",
            "action": "block",
            "subdomains": False,
        },
    ],
    "apps": [],
}


def fake_call(action, **kwargs):
    if action == "apps":
        return [
            {
                "id": "org.example.Inert.desktop",
                "name": "Inert layout example application",
                "icon": "application-x-executable",
                "flatpak": "",
                "protected": False,
            }
        ]
    if action == "presets":
        return [{"name": "Example focus session", "config": CONFIG}]
    if action == "audit":
        return {"records": [], "through": 0, "next_before": None}
    if action == "status":
        return dict(STATUS)
    raise RuntimeError(f"Inert UI: {action} is not simulated; no service was contacted")


def capture(window, directory, name):
    if directory is None:
        return
    directory.mkdir(parents=True, exist_ok=True)
    paintable = ui.Gtk.WidgetPaintable.new(window)
    scale = window.get_scale_factor()
    node = None
    deadline = time.monotonic() + 3
    window.queue_draw()
    while node is None and time.monotonic() < deadline:
        while ui.GLib.MainContext.default().pending():
            ui.GLib.MainContext.default().iteration(False)
        snapshot = ui.Gtk.Snapshot.new()
        snapshot.scale(scale, scale)
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        if node is None:
            time.sleep(0.01)
    assert node is not None, "Window did not render"
    texture = window.get_renderer().render_texture(node, None)
    path = directory / f"{name}.png"
    assert texture.save_to_png(str(path))
    print(f"Capture {path}: {texture.get_width()}x{texture.get_height()}, scale {scale}")


def focus_reaches(window, target, scope=None):
    # Top-level traversal depends on compositor focus and can loop in the
    # sidebar while this inert window is inactive. Exercise the current page's
    # real GTK focus chain; full-window human keyboard review remains separate.
    scope = scope or window.stack.get_visible_child()
    window.set_focus(None)
    visited = []
    for _ in range(150):
        scope.child_focus(ui.Gtk.DirectionType.TAB_FORWARD)
        # Real Tab events return to the main loop between presses. X11 focus
        # notifications are asynchronous; a tight loop can stay on the same row.
        while ui.GLib.MainContext.default().pending():
            ui.GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
        focused = window.get_focus()
        if len(visited) < 15:
            visited.append((type(focused).__name__, window.stack.get_visible_child_name()))
        if focused is target or (focused and focused.is_ancestor(target)):
            return True
    print("Failed focus traversal:", type(target).__name__, visited)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Skip compositor-dependent keyboard traversal; still validate construction and UI state",
    )
    parser.add_argument("--capture-dir", type=Path)
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument(
        "--font-dpi",
        type=int,
        choices=(96, 192),
        help="Override only this process's font DPI for XWayland scale checks",
    )
    args = parser.parse_args()
    failures = []
    with (
        tempfile.TemporaryDirectory(prefix="focus-icons-") as icons,
        patch.object(ui, "call", fake_call),
    ):
        app = ui.Application()
        app.set_application_id("org.focusblocker.SmokeTest")
        app.set_flags(ui.Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        if args.font_dpi:
            ui.Gtk.Settings.get_default().set_property("gtk-xft-dpi", args.font_dpi * 1024)
        style = ui.Adw.StyleManager.get_default()
        style.set_color_scheme(
            ui.Adw.ColorScheme.FORCE_DARK
            if args.theme == "dark"
            else ui.Adw.ColorScheme.FORCE_LIGHT
        )
        # Match installed icon discovery without installing anything on the host.
        icon = Path(icons) / "hicolor/512x512/apps/org.focusblocker.App.png"
        icon.parent.mkdir(parents=True)
        icon.write_bytes(
            (
                Path(__file__).resolve().parents[1] / "packaging/org.focusblocker.App.png"
            ).read_bytes()
        )
        app.activate()
        window = app.get_active_window()
        assert window is not None
        ui.Gtk.IconTheme.get_for_display(window.get_display()).add_search_path(icons)
        print(
            "Display:",
            type(window.get_display()).__name__,
            "scale:",
            window.get_scale_factor(),
            "icon theme:",
            ui.Gtk.Settings.get_default().get_property("gtk-icon-theme-name"),
        )

        def steps():
            if args.render_only:
                print("Render-only run: keyboard traversal explicitly skipped")
            assert window.config()["minutes"] == 25
            for minutes in (1, 60, 359, 360):
                window.load_config({**CONFIG, "minutes": minutes})
                assert window.config()["minutes"] == minutes
            window.load_config(CONFIG)
            reset_config = {
                **CONFIG,
                "preset_name": "Example focus session",
                "show_animation": False,
                "apps": ["org.example.Inert.desktop"],
            }
            window.render_presets([{"name": "Example focus session", "config": reset_config}])
            window.load_config(reset_config, preset=True)
            assert window.preset_dropdown.get_selected() == 1
            window.search.set_text("something")
            window.test_address.set_text("https://example.com")
            window.match_result.set_text("Old result")
            window.render_presets(window.presets)
            assert window.config() == reset_config, "Async preset refresh must preserve draft"
            window.preset_dropdown.set_selected(0)
            assert window.config() == ui.configuration({})
            assert (
                window.search.get_text()
                == window.test_address.get_text()
                == window.match_result.get_text()
                == ""
            )
            assert window.preset_snapshot is None
            window.load_config(CONFIG)
            yield
            assert window.get_width() >= 640
            assert window.stack.get_width() > 300
            assert not window.split.get_collapsed(), (
                f"Unexpected collapse at {window.get_width()}x{window.get_height()}"
            )
            capture(window, args.capture_dir, "wide-session")
            with patch.object(window, "begin") as begin:
                dialog = window.confirm_start("25 minutes · Blacklist", CONFIG)
                assert not dialog.get_decorated()
                assert dialog.get_default_widget().get_label() == "Cancel"

                def no_drag_handle(widget):
                    assert not isinstance(widget, (ui.Gtk.WindowHandle, ui.Gtk.HeaderBar))
                    child = widget.get_first_child()
                    while child:
                        no_drag_handle(child)
                        child = child.get_next_sibling()

                no_drag_handle(dialog.get_child())
                begin.assert_not_called()
                yield
                capture(dialog, args.capture_dir, "start-confirmation")
                dialog.get_default_widget().emit("clicked")
                begin.assert_not_called()
                dialog = window.confirm_start("25 minutes · Blacklist", CONFIG)
                dialog.get_child().get_last_child().get_last_child().emit("clicked")
                begin.assert_called_once_with(CONFIG)
            # Selected icons stay outside the filtered/scrolled picker and old
            # alias selections remain removable with mouse or keyboard activation.
            sample_apps = [
                {
                    "id": "firefox",
                    "name": "Firefox",
                    "icon": "firefox",
                    "flatpak": "",
                    "executable": "/usr/bin/firefox",
                },
                {
                    "id": "editor",
                    "name": "Text Editor",
                    "icon": "org.gnome.TextEditor",
                    "flatpak": "",
                    "executable": "/usr/bin/editor",
                },
                {
                    "id": "editor-alias",
                    "name": "Text Editor",
                    "icon": "org.gnome.TextEditor",
                    "flatpak": "",
                    "executable": "/usr/bin/editor",
                    "visible": False,
                },
                {
                    "id": "calc",
                    "name": "Calculator",
                    "icon": "org.gnome.Calculator",
                    "flatpak": "",
                    "executable": "/usr/bin/gnome-calculator",
                },
            ]
            window.set_apps(sample_apps)
            window.load_config({**CONFIG, "rules": [], "apps": ["firefox", "editor-alias", "calc"]})
            yield
            assert window.selected_title.get_text() == "Selected (3)"
            capture(window, args.capture_dir, "selected-apps")
            window.search.set_text("no matching applications")
            window.render_apps()
            yield
            assert window.app_list.get_first_child() is None
            assert window.selected_title.get_text() == "Selected (3)"
            chip = window.selected_apps.get_first_child().get_child()
            assert chip.get_tooltip_text() == "Calculator · Remove"
            assert args.render_only or focus_reaches(window, chip, window.setup), (
                "Selected icon unreachable by Tab within Session"
            )
            chip.emit("clicked")
            assert "calc" not in window.selected
            assert window.selected_title.get_text() == "Selected (2)"
            window.mode.set_selected(1)
            assert window.selected_title.get_text() == "Selected (2)"
            window.selected.add("missing-app")
            window.render_apps()
            assert window.selected_title.get_text() == "Selected (3)"
            window.selected_apps.get_last_child().get_child().emit("clicked")
            assert "missing-app" not in window.selected
            window.remove_selected_apps({"editor", "editor-alias"})
            assert window.selected == {"firefox"}
            window.search.set_text("")
            window.set_apps(fake_call("apps"))
            window.load_config(CONFIG)
            assert window.selected_title.get_text() == "Selected (0)"
            assert not window.selected_apps.get_visible()
            for name in ("presets", "log", "settings"):
                window.stack.set_visible_child_name(name)
                if name == "presets":
                    group = window.preset_box.get_last_child()
                    details = group.get_last_child()
                    assert isinstance(details, ui.Gtk.Expander)
                    assert not details.get_expanded()
                    before = window.config()
                    details.set_expanded(True)
                    assert details.get_child() is not None
                    assert window.config() == before, "Preview changed session draft"
                yield
                assert window.nav.get_selected_row().destination == name
                capture(window, args.capture_dir, name)
            assert args.render_only or focus_reaches(window, window.new_password), (
                "Password field unreachable by Tab"
            )
            assert args.render_only or focus_reaches(window, window.password_button), (
                "Save password unreachable by Tab"
            )
            window.stack.set_visible_child_name("session")
            window.set_default_size(640, 650)
            yield
            assert window.split.get_collapsed(), "Narrow navigation did not collapse"
            assert window.menu_button.get_visible()
            assert args.render_only or focus_reaches(
                window, window.menu_button, window.menu_button.get_parent()
            ), "Navigation unreachable by Tab in header"
            yield
            capture(window, args.capture_dir, "narrow-session")
            window.menu_button.emit("clicked")
            yield
            assert window.split.get_show_sidebar()
            capture(window, args.capture_dir, "narrow-navigation")
            window.nav.select_row(window.nav_rows["settings"])
            window.nav.emit("row-activated", window.nav_rows["settings"])
            yield
            assert window.stack.get_visible_child_name() == "settings"
            assert not window.split.get_show_sidebar()
            window.stack.set_visible_child_name("session")
            # Fake service-owned active data, solely to inspect the actual widgets.
            STATUS["active"] = {
                "remaining": 1200,
                "ends_at": time.time() + 1200,
                "grace_remaining": 0,
                "config": CONFIG,
            }
            window.render_status(dict(STATUS))
            with patch.object(window, "request") as request:
                window.begin({**CONFIG, "show_animation": False})
                request.call_args.args[1]({"starting": True})
                assert not window.close_when_active, "Animation off keeps session window open"
            yield
            assert args.render_only or focus_reaches(window, window.emergency_button), (
                "Emergency stop unreachable by Tab"
            )
            yield
            capture(window, args.capture_dir, "focus")
            window.emergency_button.emit("clicked")
            yield
            assert args.render_only or focus_reaches(window, window.stop_password), (
                "Stop password unreachable by Tab"
            )
            assert args.render_only or focus_reaches(window, window.verify_button), (
                "Verify unreachable by Tab"
            )
            yield
            capture(window, args.capture_dir, "emergency")
            window.stack.set_visible_child_name("settings")
            yield
            assert window.session.get_visible_child_name() == "running"
            STATUS["active"] = None
            window.stack.set_visible_child_name("session")
            window.render_status(dict(STATUS))
            window.set_default_size(1040, 780)
            yield
            assert not window.split.get_collapsed(), "Wide navigation did not expand"
            assert window.stack.get_width() > 300
            # A stale pre-start status reply must not cancel the close-on-start latch.
            replies = []
            with patch.object(
                window,
                "request",
                side_effect=lambda action, callback, **kw: replies.append((action, callback)),
            ):
                window.polling = False
                window.poll()
                window.begin(CONFIG)
                replies[1][1]({"starting": True})
                assert window.close_when_active
                replies[0][1](dict(STATUS))
                assert window.close_when_active
                assert len(replies) == 3, "A fresh status poll must replace the stale reply"
            with patch.object(window, "close") as close:
                window.render_status({**STATUS, "starting": True})
                close.assert_not_called()
                running = {
                    **STATUS,
                    "active": {
                        "remaining": 60,
                        "ends_at": time.time() + 60,
                        "grace_remaining": 60,
                        "config": CONFIG,
                    },
                }
                window.render_status(running)
                close.assert_called_once()
                window.close_when_active = True
                with patch.object(window, "request") as request:
                    window.grace_button.emit("clicked")
                    request.assert_called_once_with("cancel", window.restore_after_cancel)
                window.render_status(running)
                close.assert_called_once()
                window.close_when_active = True
                window.render_status({**running, "protection_error": "Inert test failure"})
                close.assert_called_once()
                assert window.protection_label.get_text() == "Inert test failure"
                # Reopening an existing session must remain open.
                window.render_status(running)
                close.assert_called_once()
            window.render_status(dict(STATUS))
            print(
                "GTK checks passed: duration, navigation, emergency flow, start-close/reopen, stale replies; keyboard="
                + ("SKIPPED" if args.render_only else "page-local")
            )

        sequence = steps()

        def advance():
            try:
                next(sequence)
            except StopIteration:
                if args.interactive:
                    print("Inert UI remains open. Service operations are unsupported.", flush=True)
                else:
                    window.close()
                    app.quit()
                return False
            except Exception:
                failures.append(traceback.format_exc())
                window.close()
                app.quit()
                return False
            return True

        # Let each resize/transition settle; toggling collapsed before the first
        # allocation can overflow libadwaita's initial animation measurements.
        ui.GLib.timeout_add(700, advance)
        app.run([])
    if failures:
        raise AssertionError("\n".join(failures))
    print("GTK construction smoke test passed (fake service, no enforcement)")


if __name__ == "__main__":
    main()
