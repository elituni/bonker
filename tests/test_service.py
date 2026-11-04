import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import FakeClock, config

from focus_blocker.apps import App, Monitor, Process, app_matches, protected_process
from focus_blocker.engine import Engine
from focus_blocker.rules import Invalid, configuration
from focus_blocker.service import Controller
from focus_blocker.storage import Store


def process(pid, exe, parent=9000, group="/user.slice/session.slice", args=None, flatpak=""):
    return Process(pid, parent, str(pid * 100), exe, args or [exe], group, flatpak)


def monitor():
    obj = Monitor.__new__(Monitor)
    obj.apps = {
        "firefox.desktop": App(
            "firefox.desktop", "Firefox", "firefox", "/usr/bin/firefox", browser=True
        ),
        "brave.desktop": App("brave.desktop", "Brave", "brave", "/opt/brave/brave", browser=True),
        "editor.desktop": App("editor.desktop", "Editor", "text-editor", "/usr/bin/editor"),
        "music.desktop": App(
            "music.desktop", "Music", "music", "/usr/bin/flatpak", flatpak="org.example.Music"
        ),
    }
    obj.processes = {}
    obj.seen = {}
    obj.refresh = Mock()
    obj.inventory = lambda: [asdict(a) for a in obj.apps.values()]
    obj.signaled = {}
    return obj


class ApplicationTests(unittest.TestCase):
    def test_blacklist_targets_apps_and_children_not_other_apps(self):
        m = monitor()
        m.processes = {
            1: process(1, "/usr/bin/editor"),
            2: process(2, "/usr/bin/helper", 1),
            3: process(3, "/usr/bin/other"),
        }
        c = configuration({"apps": ["editor.desktop"]})
        self.assertEqual(set(m.targets(c)), {1, 2})

    def test_whitelist_handles_unknown_app_scopes_and_keeps_essential_services(self):
        m = monitor()
        m.processes = {
            1: process(1, "/usr/bin/editor"),
            2: process(
                2, "/home/user/unknown", group="/user.slice/app.slice/app-unknown-123.scope"
            ),
            3: process(
                3, "/usr/bin/gnome-shell", group="/user.slice/app.slice/gnome-shell.service"
            ),
            4: process(4, "/usr/bin/pipewire"),
            5: process(5, "/usr/bin/ibus-daemon", group="/user.slice/app.slice/ibus.service"),
        }
        c = configuration({"mode": "whitelist", "apps": ["editor.desktop"]})
        self.assertEqual(set(m.targets(c)), {2})

    def test_brave_is_blocked_even_if_selected_in_website_whitelist(self):
        m = monitor()
        m.processes = {1: process(1, "/opt/brave/brave")}
        c = configuration({"mode": "whitelist", "apps": ["brave.desktop"]})
        self.assertEqual(set(m.targets(c)), {1})

    def test_flatpak_uses_application_id_not_shared_launcher(self):
        m = monitor()
        m.processes = {
            1: process(1, "/app/bin/music", flatpak="org.example.Music"),
            2: process(2, "/app/bin/other", flatpak="org.example.Other"),
        }
        c = configuration({"apps": ["music.desktop"]})
        self.assertEqual(set(m.targets(c)), {1})

    def test_allowed_helpers_inherit_but_independent_apps_do_not(self):
        m = monitor()
        group = "/user.slice/app.slice/dbus-org.example.Editor.service"
        m.processes = {
            1: process(1, "/usr/bin/editor", group=group),
            2: process(2, "/usr/libexec/editor-helper", parent=1, group=group),
            3: process(3, "/opt/brave/brave", parent=1, group=group),
        }
        c = configuration({"mode": "whitelist", "apps": ["editor.desktop"]})
        self.assertEqual(set(m.targets(c)), {3})

    def test_firefox_wrapper_matches_real_binary(self):
        m = monitor()
        p = process(10, "/usr/lib64/firefox/firefox")
        self.assertTrue(app_matches(m.apps["firefox.desktop"], p))

    def test_orphan_firefox_crashhelper_is_not_an_unsupported_browser(self):
        m = monitor()
        group = "/user.slice/app.slice/app-gnome-firefox-123.scope"
        for libdir in ("lib", "lib64"):
            with self.subTest(libdir=libdir):
                m.processes = {
                    1: process(1, f"/usr/{libdir}/firefox/crashhelper", parent=9000, group=group),
                    2: process(2, "/opt/brave/brave", group=group),
                    3: process(3, "/usr/bin/chromium", group=group),
                    4: process(4, "/tmp/firefox/crashhelper", group=group),
                }
                c = configuration({"rules": [{"host": "example.com", "action": "block"}]})
                self.assertEqual(set(m.targets(c)), {2, 3, 4})

    def test_explicit_firefox_app_block_still_targets_its_crashhelper(self):
        m = monitor()
        m.processes = {
            1: process(
                1,
                "/usr/lib64/firefox/crashhelper",
                group="/user.slice/app.slice/app-gnome-firefox-123.scope",
            )
        }
        self.assertEqual(set(m.targets(configuration({"apps": ["firefox.desktop"]}))), {1})

    def test_independent_app_does_not_inherit_allowed_gnome_scope(self):
        m = monitor()
        group = "/user.slice/app.slice/app-gnome-firefox-123.scope"
        m.processes = {
            1: process(1, "/usr/lib64/firefox/firefox", group=group),
            2: process(2, "/usr/bin/editor", parent=1, group=group),
        }
        c = configuration({"mode": "whitelist", "apps": ["firefox.desktop"]})
        self.assertEqual(set(m.targets(c)), {2})

    def test_protection_does_not_exempt_arbitrary_python(self):
        protected = process(
            1,
            "/usr/bin/python3.14",
            args=["/usr/bin/python3", "-I", "/usr/local/bin/focus-blocker"],
        )
        spoof = process(
            2, "/usr/bin/python3.14", args=["python3", "-c", "print('hi')", "focus_blocker.ui"]
        )
        self.assertTrue(protected_process(protected))
        self.assertFalse(protected_process(spoof))


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "db")
        self.clock = FakeClock()
        self.engine = Engine(self.store, self.clock)
        self.engine.set_password("secret")
        self.monitor = monitor()
        self.monitor.processes = {
            10: process(10, "/usr/lib64/firefox/firefox"),
            11: process(11, "/usr/bin/python3.14", parent=10),
        }
        self.monitor.seen = {
            (p.pid, p.start): time.monotonic() - 100 for p in self.monitor.processes.values()
        }
        self.monitor.enforce = Mock()
        self.controller = Controller(self.engine, self.monitor)
        self.policy = patch("focus_blocker.service.policy_ready", return_value=True)
        self.policy.start()

    def tearDown(self):
        self.policy.stop()
        self.store.db.close()
        self.temp.cleanup()

    def heartbeat(self, revision="idle", managed=True, private=True, pid=11):
        return self.controller.dispatch(
            {
                "action": "browser",
                "instance": "profile-a",
                "revision": revision,
                "managed": managed,
                "private": private,
            },
            pid,
        )

    def test_session_waits_for_matching_firefox_acknowledgement(self):
        self.heartbeat()
        self.controller.dispatch({"action": "prepare", "config": config()}, 100)
        self.controller.tick()
        self.assertIsNone(self.engine.active)
        pending = self.heartbeat()
        self.assertEqual(pending["phase"], "starting")
        self.heartbeat(pending["revision"])
        self.controller.tick()
        self.assertIsNotNone(self.engine.active)
        self.assertEqual(self.engine.remaining(), 600)

    def test_ordinary_or_private_disabled_extension_cannot_start(self):
        for managed, private in ((False, True), (True, False)):
            with self.subTest(managed=managed, private=private):
                self.heartbeat(managed=managed, private=private)
                with self.assertRaises(Invalid):
                    self.controller.dispatch({"action": "prepare", "config": config()}, 100)

    def test_non_firefox_process_cannot_claim_browser_connection(self):
        with self.assertRaises(Invalid):
            self.heartbeat(pid=99999)

    def test_missing_integration_restricts_browser_not_timer(self):
        self.heartbeat()
        self.engine.prepare(config())
        self.engine.activate()
        self.heartbeat(self.engine.active["id"])
        self.controller.browsers[10]["at"] = time.monotonic() - 6
        self.controller.tick()
        self.assertEqual(self.monitor.enforce.call_args.args[1], {10})
        self.assertEqual(self.engine.remaining(), 600)
        self.assertIsNotNone(self.controller.protection_error)

    def test_empty_app_whitelist_can_start_without_firefox(self):
        self.monitor.processes = {}
        self.controller.dispatch(
            {"action": "prepare", "config": {"mode": "whitelist", "apps": []}}, 100
        )
        self.controller.tick()
        self.assertIsNotNone(self.engine.active)

    def test_browser_release_audit_waits_for_idle_acknowledgement(self):
        self.engine.prepare(config())
        self.engine.activate()
        self.heartbeat(self.engine.active["id"])
        self.controller.dispatch(
            {"action": "stop", "token": self.engine.authorize("secret"), "reason": "Need access"},
            100,
        )
        self.controller.tick()
        self.assertEqual(len(self.store.audit()), 1)
        self.heartbeat("idle")
        self.controller.tick()
        self.assertEqual(self.store.audit()[0]["event"], "released")

    def test_app_only_stop_is_not_stalled_by_unintegrated_firefox(self):
        self.engine.prepare({"apps": ["editor.desktop"]})
        self.engine.activate()
        self.controller.dispatch(
            {"action": "stop", "token": self.engine.authorize("secret"), "reason": "Done"}, 100
        )
        self.controller.tick()
        self.assertIsNone(self.store.get("release_pending"))
        self.engine.prepare({"apps": ["editor.desktop"]})

    def test_new_profile_does_not_hold_old_session_release_open(self):
        self.engine.prepare(config())
        self.engine.activate()
        self.heartbeat(self.engine.active["id"])
        self.controller.dispatch(
            {"action": "stop", "token": self.engine.authorize("secret"), "reason": "Done"}, 100
        )
        self.monitor.processes[20] = process(20, "/usr/lib64/firefox/firefox")
        self.heartbeat("idle", private=False)
        self.controller.tick()
        self.assertIsNone(self.store.get("release_pending"))

    def test_release_wait_survives_restart_but_not_browser_exit(self):
        self.engine.prepare(config())
        self.engine.activate()
        self.heartbeat(self.engine.active["id"])
        self.controller.dispatch(
            {"action": "stop", "token": self.engine.authorize("secret"), "reason": "Done"}, 100
        )
        recovered = Controller(Engine(self.store, self.clock), self.monitor)
        recovered.tick()
        self.assertIsNotNone(self.store.get("release_pending"))
        self.monitor.processes = {}
        recovered.tick()
        self.assertIsNone(self.store.get("release_pending"))

    def test_uninstall_reservation_requires_root_and_blocks_start(self):
        with self.assertRaises(Invalid):
            self.controller.dispatch({"action": "begin_uninstall"}, 100, peer_uid=1000)
        self.controller.dispatch({"action": "begin_uninstall"}, 100, peer_uid=0)
        with self.assertRaises(Invalid):
            self.controller.dispatch(
                {"action": "prepare", "config": {"apps": ["editor.desktop"]}}, 100
            )
        self.controller.dispatch({"action": "abort_uninstall"}, 100, peer_uid=0)
        self.controller.dispatch({"action": "prepare", "config": {"apps": ["editor.desktop"]}}, 100)
        with self.assertRaises(Invalid):
            self.controller.dispatch({"action": "begin_uninstall"}, 100, peer_uid=0)

    def test_uninstall_refuses_pending_browser_release(self):
        self.engine.prepare(config())
        self.engine.activate()
        self.controller.dispatch(
            {"action": "stop", "token": self.engine.authorize("secret"), "reason": "Done"}, 100
        )
        with self.assertRaises(Invalid):
            self.controller.dispatch({"action": "begin_uninstall"}, 100, peer_uid=0)

    def test_release_wait_does_not_survive_reboot(self):
        self.engine.prepare(config())
        self.engine.activate()
        self.controller.dispatch(
            {"action": "stop", "token": self.engine.authorize("secret"), "reason": "Done"}, 100
        )
        self.clock.boot_id = "boot-b"
        recovered = Controller(Engine(self.store, self.clock), self.monitor)
        recovered.tick()
        self.assertIsNone(self.store.get("release_pending"))

    def test_cancel_status_waits_for_browser_release(self):
        self.engine.prepare(config())
        self.engine.activate()
        self.heartbeat(self.engine.active["id"])
        self.controller.dispatch({"action": "cancel"}, 100)
        self.assertTrue(self.controller.dispatch({"action": "status"}, 100)["release_pending"])
        self.heartbeat("idle")
        self.assertFalse(self.controller.dispatch({"action": "status"}, 100)["release_pending"])

    def test_browser_receives_only_website_configuration(self):
        self.engine.prepare(
            {**config(), "preset_name": "Private project", "apps": ["editor.desktop"]}
        )
        self.assertEqual(set(self.controller.browser_state()["config"]), {"mode", "rules"})
        self.engine.activate()
        self.assertEqual(set(self.controller.browser_state()["config"]), {"mode", "rules"})

    def test_second_profile_must_also_acknowledge(self):
        self.heartbeat()
        self.monitor.processes[20] = process(20, "/usr/lib64/firefox/firefox")
        self.monitor.seen[(20, "2000")] = time.monotonic() - 100
        with self.assertRaises(Invalid):
            self.controller.dispatch({"action": "prepare", "config": config()}, 100)


if __name__ == "__main__":
    unittest.main()
