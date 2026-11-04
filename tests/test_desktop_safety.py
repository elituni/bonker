"""Signal-free regressions for Fedora background services in app.slice."""

import unittest
from dataclasses import asdict

from test_service import monitor, process

from focus_blocker.apps import App, protected_process
from focus_blocker.rules import configuration
from focus_blocker.ui_state import application_choices


class DesktopSafetyTests(unittest.TestCase):
    def test_flatpak_outer_helpers_follow_their_app_not_all_bwrap_processes(self):
        m = monitor()
        group = "/app.slice/app-flatpak-org.example.Music-123.scope"
        m.processes = {
            1: process(1, "/usr/bin/bwrap", group=group),
            2: process(2, "/usr/bin/xdg-dbus-proxy", group=group),
            3: process(3, "/app/bin/music", parent=1, group=group, flatpak="org.example.Music"),
            4: process(4, "/usr/bin/editor", parent=1, group=group),
            5: process(5, "/tmp/bwrap", group=group),
            6: process(
                6, "/usr/bin/bwrap", group="/app.slice/app-flatpak-org.example.Other-123.scope"
            ),
        }
        allowed = configuration({"mode": "whitelist", "apps": ["music.desktop"]})
        self.assertEqual(set(m.targets(allowed)), {4, 5, 6})
        blocked = configuration({"apps": ["music.desktop"]})
        self.assertEqual(set(m.targets(blocked)), {1, 2, 3, 4})

    def test_native_speech_workers_are_not_unsupported_firefox_processes(self):
        for libdir in ("lib", "lib64"):
            with self.subTest(libdir=libdir):
                m = monitor()
                group = "/app.slice/app-gnome-firefox-123.scope"
                m.processes = {
                    1: process(1, "/usr/bin/speech-dispatcher", parent=10, group=group),
                    2: process(
                        2,
                        f"/usr/{libdir}/speech-dispatcher-modules/sd_espeak-ng",
                        parent=1,
                        group=group,
                    ),
                    3: process(
                        3,
                        f"/usr/{libdir}/speech-dispatcher-modules/sd_dummy",
                        parent=1,
                        group=group,
                    ),
                    10: process(10, "/usr/lib64/firefox/firefox", group=group),
                    11: process(11, "/usr/bin/editor", parent=10, group=group),
                    12: process(12, "/opt/brave/brave", parent=10, group=group),
                    13: process(13, "/usr/bin/chromium", parent=10, group=group),
                    14: process(14, "/tmp/sd_espeak-ng", parent=10, group=group),
                    15: process(
                        15,
                        f"/usr/{libdir}/speech-dispatcher-modules/sd_other",
                        parent=10,
                        group=group,
                    ),
                }
                allow = configuration({"mode": "whitelist", "apps": ["firefox.desktop"]})
                self.assertEqual(set(m.targets(allow)), {11, 12, 13, 14, 15})
                block = configuration({"apps": ["firefox.desktop"]})
                self.assertEqual(set(m.targets(block)), {10, 11, 12, 13, 14, 15})
                self.assertFalse(protected_process(m.processes[14]))
                self.assertFalse(protected_process(m.processes[15]))

    def test_app_slice_is_not_application_evidence(self):
        m = monitor()
        paths = [
            "/usr/libexec/gnome-session-ctl",
            "/usr/libexec/gnome-session-service",
            "/usr/libexec/dconf-service",
            "/usr/libexec/evolution-source-registry",
            "/usr/libexec/evolution-calendar-factory",
            "/usr/libexec/evolution-addressbook-factory",
            "/usr/libexec/goa-identity-service",
            "/usr/libexec/flatpak-session-helper",
            "/usr/libexec/p11-kit/p11-kit-server",
            "/usr/libexec/bluetooth/obexd",
            "/usr/libexec/unknown-background-service",
        ]
        m.processes = {
            i: process(i, path, group="/user.slice/app.slice/background.service")
            for i, path in enumerate(paths, 1)
        }
        m.processes[30] = process(30, "/opt/unknown-gui", group="/app.slice/app-unknown-1.scope")
        m.processes[31] = process(31, "/usr/bin/editor", group="/app.slice/editor.service")
        self.assertEqual(set(m.targets(configuration({"mode": "whitelist"}))), {30, 31})

    def test_hidden_service_is_not_a_visible_application(self):
        m = monitor()
        m.apps["hidden"] = App("hidden", "Background helper", "", "/opt/service", visible=False)
        m.processes = {1: process(1, "/opt/service", group="/app.slice/helper.service")}
        self.assertFalse(m.targets(configuration({"mode": "whitelist"})))
        # An explicitly selected old preset can still block a nonessential hidden app.
        self.assertEqual(set(m.targets(configuration({"apps": ["hidden"]}))), {1})

    def test_system_components_survive_blocked_parents_and_scopes(self):
        m = monitor()
        paths = [
            "/usr/bin/Xwayland",
            "/usr/libexec/gnome-session-service",
            "/usr/bin/gnome-software",
            "/usr/lib/pentablet/PenTablet",
            "/usr/lib/warp/warp-taskbar",
            "/usr/bin/warp-desktop-svc",
        ]
        m.processes[1] = process(1, "/usr/bin/editor")
        for i, path in enumerate(paths, 2):
            m.processes[i] = process(i, path, parent=1, group="/app.slice/app-brave-1.scope")
        self.assertEqual(set(m.targets(configuration({"mode": "whitelist"}))), {1})

    def test_gjs_protection_is_specific_to_gnome_shell_modules(self):
        m = monitor()
        for i, script in enumerate(("org.gnome.Shell.Notifications", "org.gnome.ScreenSaver"), 1):
            m.processes[i] = process(
                i,
                "/usr/bin/gjs-console",
                args=["/usr/bin/gjs", "-m", "/usr/share/gnome-shell/" + script],
                group="/app.slice/app-gjs-1.scope",
            )
        m.processes[3] = process(
            3,
            "/usr/bin/gjs-console",
            args=["gjs", "-m", "/opt/app.js"],
            group="/app.slice/app-gjs-2.scope",
        )
        self.assertTrue(protected_process(m.processes[1]))
        self.assertFalse(protected_process(m.processes[3]))
        self.assertEqual(set(m.targets(configuration({"mode": "whitelist"}))), {3})

    def test_bonker_helpers_survive_but_independent_apps_do_not_inherit_protection(self):
        m = monitor()
        group = "/app.slice/app-unknown-browser-1.scope"
        m.processes = {
            1: process(
                1,
                "/usr/bin/python3.14",
                group=group,
                args=["python3", "-I", "/usr/local/bin/focus-blocker-notifier"],
            ),
            2: process(2, "/usr/bin/bwrap", parent=1, group=group),
            3: process(3, "/usr/libexec/glycin-loaders/2+/glycin-image-rs", parent=2, group=group),
            4: process(4, "/usr/bin/editor", parent=1, group=group),
            5: process(5, "/opt/brave/brave", parent=1, group=group),
            6: process(6, "/opt/unknown-gui", parent=1, group="/app.slice/app-other-1.scope"),
            7: process(7, "/usr/bin/bwrap", parent=4, group=group),
        }
        self.assertEqual(set(m.targets(configuration({"mode": "whitelist"}))), {4, 5, 6, 7})
        self.assertEqual(set(m.targets(configuration({"apps": ["editor.desktop"]}))), {4, 7})

    def test_orphan_crashhelper_follows_firefox_even_in_unrelated_scope(self):
        m = monitor()
        m.processes = {
            1: process(1, "/usr/lib64/firefox/crashhelper", group="/app.slice/app-other-1.scope"),
            2: process(2, "/tmp/crashhelper", group="/app.slice/app-other-1.scope"),
        }
        self.assertEqual(
            set(m.targets(configuration({"mode": "whitelist", "apps": ["firefox.desktop"]}))), {2}
        )
        self.assertEqual(set(m.targets(configuration({"apps": ["firefox.desktop"]}))), {1})

    def test_protected_picker_entries_are_not_selected_summary_choices(self):
        app = App("tablet", "Tablet", "", "/usr/lib/pentablet/PenTablet", protected=True)
        self.assertFalse(application_choices([asdict(app)], {"tablet"}))
