"""Focused inert GTK checks; calls handlers only, with no system input or service."""

import argparse
from pathlib import Path
from unittest.mock import patch

import gtk_smoke as smoke

ui = smoke.ui


def children(widget):
    child = widget.get_first_child()
    while child:
        yield child
        yield from children(child)
        child = child.get_next_sibling()


def controller(widget, kind):
    model = widget.observe_controllers()
    return next(
        model.get_item(i) for i in range(model.get_n_items()) if isinstance(model.get_item(i), kind)
    )


def settle():
    smoke.time.sleep(0.05)
    deadline = smoke.time.monotonic() + 0.4
    while smoke.time.monotonic() < deadline:
        while ui.GLib.MainContext.default().pending():
            ui.GLib.MainContext.default().iteration(False)
        smoke.time.sleep(0.005)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    args = parser.parse_args()
    ui.Adw.init()
    ui.Adw.StyleManager.get_default().set_color_scheme(ui.Adw.ColorScheme.FORCE_DARK)
    app = ui.Adw.Application(application_id="org.focusblocker.ConfirmationPreview")
    app.register(None)
    with patch.object(ui, "call", smoke.fake_call):
        window = ui.FocusWindow(app)
        window.present()
        settle()
        ui.GLib.source_remove(window.timer_id)
        window.timer_id = ui.GLib.timeout_add_seconds(60, lambda: True)
        try:
            with patch.object(window, "begin") as begin:
                dialog = window.confirm_start(
                    "25 minutes · Blacklist\n\nYou can stop freely for the first 3 minutes. After that, your password and a reason are required.",
                    smoke.CONFIG,
                )
                settle()
                assert isinstance(dialog, ui.Gtk.Window)
                assert not dialog.get_decorated()
                assert dialog.get_default_widget().get_label() == "Cancel"
                assert dialog.get_focus() is dialog.get_default_widget()
                assert not any(
                    isinstance(w, (ui.Gtk.WindowHandle, ui.Gtk.HeaderBar))
                    for w in children(dialog.get_child())
                )
                begin.assert_not_called()
                smoke.capture(dialog, args.capture_dir, "start-confirmation")
                dialog.get_default_widget().emit("clicked")
                settle()
                begin.assert_not_called()
                assert not dialog.get_visible()
                dialog = window.confirm_start("Inert confirmation", smoke.CONFIG)
                settle()
                keys = controller(dialog, ui.Gtk.EventControllerKey)
                assert keys.emit("key-pressed", 0xFF1B, 0, 0)
                settle()
                assert not dialog.get_visible()
                begin.assert_not_called()
                dialog = window.confirm_start("Inert confirmation", smoke.CONFIG)
                settle()
                start = dialog.get_child().get_last_child().get_last_child()
                start.emit("clicked")
                begin.assert_called_once_with(smoke.CONFIG)
                settle()
            window.session.set_visible_child_name("emergency")
            window.password_step.set_visible(False)
            window.reason_step.set_visible(True)
            buttons = [w for w in children(window.emergency) if isinstance(w, ui.Gtk.Button)]
            assert "Continue session" not in [w.get_label() for w in buttons]
            back = next(w for w in buttons if w.get_label() == "Back to session")
            stop = next(w for w in buttons if w.get_label() == "Stop session and record reason")
            assert stop.has_css_class("destructive-action")
            generation = window.authorization.generation
            window.authorization.accept(generation, "inert-token")
            with patch.object(window.pool, "submit") as submit:
                back.emit("clicked")
                assert not window.authorization.matches(generation)
                assert window.authorization.token is None
                submit.assert_called_once_with(ui.call, "revoke", token="inert-token")
            window.session.set_visible_child_name("emergency")
            with patch.object(window, "submit_reason") as submit_reason:
                stop.emit("clicked")
                submit_reason.assert_called_once_with()
            settle()
            smoke.capture(window, args.capture_dir, "emergency-reason")
        finally:
            window.close()
            settle()
    print(
        "PASS: restored standard confirmation, Cancel/Escape/Start, Back revocation and stop-action wiring. FAKE service; no system input/enforcement."
    )


if __name__ == "__main__":
    main()
