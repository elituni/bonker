import copy
import io
import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from focus_blocker.engine import Engine
from focus_blocker.native import serve
from focus_blocker.policy import merge_policy, remove_policy
from focus_blocker.rules import Invalid, evaluate, hostname, normalize_rules
from focus_blocker.storage import Store


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.uptime = 100.0
        self.boot_id = "boot-a"

    def wall(self):
        return self.now

    def elapsed(self):
        return self.uptime

    def boot(self):
        return self.boot_id

    def advance(self, seconds):
        self.now += seconds
        self.uptime += seconds


def config(minutes=10, mode="blacklist"):
    return {
        "minutes": minutes,
        "mode": mode,
        "rules": [
            {"host": "youtube.com", "action": "block", "subdomains": True},
            {"host": "music.youtube.com", "action": "allow", "subdomains": True},
        ],
        "apps": [],
    }


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = Store(self.path)
        self.clock = FakeClock()
        self.engine = Engine(self.store, self.clock)
        self.engine.set_password("original")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def start(self, minutes=10):
        self.engine.prepare(config(minutes))
        self.engine.activate()

    def test_positive_duration_and_service_maximum(self):
        for value in (0, -1, 361, 360.1, True, "360"):
            with self.subTest(value=value), self.assertRaises(Invalid):
                self.engine.prepare(config(value))
        self.start(360)
        self.assertEqual(self.engine.remaining(), 21600)

    def test_short_sessions_allowed_and_expire(self):
        self.start(1)
        self.clock.advance(60)
        self.assertIsNone(self.engine.status()["active"])

    def test_animation_preference_survives_restart_without_changing_timer(self):
        self.engine.prepare({**config(minutes=1), "show_animation": False})
        self.engine.activate()
        self.clock.advance(10)
        restored = Engine(self.store, self.clock)
        self.assertFalse(restored.status()["active"]["config"]["show_animation"])
        self.assertEqual(restored.remaining(), 50)
        self.clock.advance(50)
        self.assertIsNone(restored.status()["active"])

    def test_start_waits_for_activation(self):
        self.engine.prepare(config())
        self.clock.advance(5)
        self.engine.activate()
        self.assertEqual(self.engine.remaining(), 600)

    def test_startup_timeout_unwinds_pending(self):
        self.engine.prepare(config())
        self.clock.advance(20)
        self.engine.tick()
        self.assertIsNone(self.engine.pending)
        self.assertIsNone(self.engine.active)

    def test_correction_boundary(self):
        self.start()
        self.clock.advance(179.999)
        self.assertEqual(self.engine.stop_edit()["minutes"], 10)
        self.start()
        self.clock.advance(180)
        with self.assertRaises(Invalid):
            self.engine.stop_edit()
        self.assertIsNotNone(self.engine.active)
        self.assertEqual(self.store.audit(), [])

    def test_restart_does_not_renew_grace_or_duration(self):
        self.start()
        self.clock.advance(181)
        recovered = Engine(self.store, self.clock)
        self.assertEqual(recovered.remaining(), 419)
        with self.assertRaises(Invalid):
            recovered.stop_edit()

    def test_sleep_counts_and_wall_clock_changes_do_not_extend(self):
        self.start()
        self.clock.now -= 7200
        self.clock.uptime += 240
        self.assertEqual(self.engine.remaining(), 360)

    def test_reboot_recovery_and_expired_boot(self):
        self.start()
        self.clock.advance(240)
        self.clock.boot_id = "boot-b"
        self.clock.uptime = 10
        recovered = Engine(self.store, self.clock)
        self.assertEqual(recovered.remaining(), 360)
        self.assertEqual(recovered.status()["active"]["grace_remaining"], 0)
        self.clock.now += 400
        self.clock.boot_id = "boot-c"
        self.assertIsNone(Engine(self.store, self.clock).active)

    def test_password_change_requires_current_and_idle(self):
        with self.assertRaises(Invalid):
            self.engine.set_password("replacement")
        self.clock.advance(2)
        self.engine.check_password("original")
        self.engine.set_password("replacement", "original")
        self.engine.check_password("replacement")
        self.start()
        with self.assertRaises(Invalid):
            self.engine.set_password("third", "replacement")
        self.assertNotIn("replacement", json.dumps(self.store.get("password")))

    def test_empty_first_password_rejected(self):
        store = Store(":memory:")
        try:
            engine = Engine(store, self.clock)
            with self.assertRaises(Invalid):
                engine.set_password("")
            engine.set_password("first")
            engine.check_password("first")
        finally:
            store.db.close()

    def test_password_alone_does_not_stop(self):
        self.start()
        token = self.engine.authorize("original")
        self.assertTrue(token)
        self.assertIsNotNone(self.engine.active)
        with self.assertRaises(Invalid):
            self.engine.stop_reason(token, " \n ")
        self.assertEqual(self.store.audit(), [])

    def test_reason_saved_before_release_and_confirmation_appended(self):
        self.start()
        self.clock.advance(200)
        token = self.engine.authorize("original")
        reason = "Needed a reference I forgot to allow.\nRestarting with a corrected list."
        self.engine.stop_reason(token, reason)
        self.assertIsNone(self.engine.active)
        self.assertEqual(self.store.audit()[0]["reason"], reason)
        self.assertEqual(self.store.audit()[0]["elapsed"], 200)
        self.assertEqual(self.store.audit()[0]["event"], "release_authorized")
        self.engine.release_confirmed()
        self.assertEqual(self.store.audit()[0]["event"], "released")
        self.engine.release_confirmed()
        self.assertEqual(len(self.store.audit()), 2)

    def test_failed_reason_write_never_releases(self):
        self.start()
        token = self.engine.authorize("original")
        with patch.object(self.store, "append", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.engine.stop_reason(token, "Emergency")
        self.assertIsNotNone(self.engine.active)
        self.assertIsNotNone(self.store.get("active"))
        self.clock.advance(600)
        with patch.object(self.store, "put", side_effect=sqlite3.OperationalError("disk full")):
            self.engine.tick()
        self.assertIsNone(self.engine.active)

    def test_expiry_during_stop_does_not_log_false_early_stop(self):
        self.start(1)
        token = self.engine.authorize("original")
        self.clock.advance(60)
        self.assertTrue(self.engine.stop_reason(token, "too late")["expired"])
        self.assertEqual(self.store.audit(), [])

    def test_authorization_cannot_be_reused_or_used_after_continue(self):
        self.start()
        token = self.engine.authorize("original")
        self.engine.revoke(token)
        with self.assertRaises(Invalid):
            self.engine.stop_reason(token, "reason")
        token = self.engine.authorize("original")
        self.engine.stop_edit()
        self.start()
        with self.assertRaises(Invalid):
            self.engine.stop_reason(token, "reason")

    def test_presets_and_active_sessions_are_independent_copies(self):
        draft = config()
        self.engine.save_preset("Work", draft)
        loaded = self.store.presets()[0]["config"]
        self.engine.prepare(loaded)
        self.engine.activate()
        loaded["rules"].clear()
        draft["minutes"] = 25
        self.engine.save_preset("Work", draft, overwrite=True)
        self.assertEqual(self.engine.active["duration"], 600)
        self.assertEqual(len(self.engine.active["config"]["rules"]), 2)
        self.assertEqual(self.store.presets()[0]["config"]["minutes"], 25)

    def test_duplicate_preset_requires_explicit_overwrite(self):
        self.engine.save_preset("Work", config())
        with self.assertRaises(Invalid):
            self.engine.save_preset("Work", config(20))
        self.engine.save_preset("Study", config(), original="Work")
        self.assertEqual(self.store.presets()[0]["name"], "Study")

    def test_audit_survives_preset_deletion_and_is_append_only(self):
        self.engine.save_preset("Work", config())
        self.start()
        self.engine.stop_reason(self.engine.authorize("original"), "Need to leave")
        self.engine.delete_preset("Work")
        self.assertEqual(len(self.store.audit()), 1)
        for sql in ("DELETE FROM audit", "UPDATE audit SET value='{}'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.store.db.execute(sql)
        self.store.db.rollback()
        recovered = Store(self.path)
        self.assertEqual(recovered.audit()[0]["reason"], "Need to leave")
        recovered.db.close()


class RuleTests(unittest.TestCase):
    def test_shared_cases(self):
        fixture = json.loads((Path(__file__).parent / "rule_cases.json").read_text())
        for case in fixture:
            with self.subTest(case=case["url"]):
                self.assertEqual(
                    evaluate(case["url"], case["mode"], case["rules"])["allowed"], case["allowed"]
                )

    def test_normalization_and_international_hosts(self):
        self.assertEqual(hostname("HTTPS://MUSIC.YouTube.com/watch?v=1#x"), "music.youtube.com")
        self.assertEqual(hostname("faß.de"), "xn--fa-hia.de")
        self.assertEqual(hostname("https://[::1]:8000/path"), "::1")
        self.assertEqual(hostname("example.com."), "example.com")
        for value in (
            "",
            "*.example.com",
            "https://user:pass@example.com",
            "file:///tmp/x",
            "a b.com",
            "example.com:bad",
            "-bad.com",
        ):
            with self.subTest(value=value), self.assertRaises(Invalid):
                hostname(value)

    def test_duplicate_rules_and_conflicts(self):
        rule = {"host": "Example.com", "action": "block", "subdomains": True}
        self.assertEqual(len(normalize_rules([rule, rule])), 1)
        with self.assertRaises(Invalid):
            normalize_rules([rule, {**rule, "action": "allow"}])

    def test_browser_numeric_and_encoded_host_normalization(self):
        for address in ("127.1", "0x7f000001", "0177.0.0.1", "2130706433", "127.0.1"):
            self.assertEqual(hostname("https://" + address), "127.0.0.1")
        self.assertEqual(hostname("https://%65xample.com"), "example.com")
        self.assertEqual(hostname("https://0x"), "0.0.0.0")
        for address in (
            "example.com..",
            "1.2.3.256",
            "4294967296",
            "09",
            "example.123",
            "1.2.3.4.5",
        ):
            with self.subTest(address=address), self.assertRaises(Invalid):
                hostname(address)


class PolicyTests(unittest.TestCase):
    def test_managed_consent_preserves_other_extension_settings(self):
        from focus_blocker import EXTENSION_ID

        original = {
            "policies": {
                "3rdparty": {
                    "Extensions": {
                        EXTENSION_ID: {"localBridgeConsent": False, "otherSetting": "keep"},
                        "unrelated@example.org": {"setting": "untouched"},
                    }
                }
            }
        }
        merged, receipt = merge_policy(original, "file:///extension.xpi")
        self.assertIs(
            merged["policies"]["3rdparty"]["Extensions"][EXTENSION_ID]["localBridgeConsent"], True
        )
        self.assertEqual(remove_policy(merged, receipt), original)

    def test_existing_policies_are_preserved_and_restored(self):
        original = {
            "policies": {
                "Homepage": {"URL": "https://example.com"},
                "DisableSafeMode": False,
                "ExtensionSettings": {"other@example.org": {"installation_mode": "allowed"}},
            }
        }
        snapshot = copy.deepcopy(original)
        merged, receipt = merge_policy(original, "file:///extension.xpi")
        self.assertEqual(original, snapshot)
        self.assertEqual(merged["policies"]["Homepage"], original["policies"]["Homepage"])
        self.assertEqual(remove_policy(merged, receipt), original)

    def test_later_unrelated_changes_survive_uninstall(self):
        merged, receipt = merge_policy({}, "file:///extension.xpi")
        merged["policies"]["Homepage"] = {"URL": "https://changed.example"}
        self.assertEqual(
            remove_policy(merged, receipt)["policies"]["Homepage"]["URL"], "https://changed.example"
        )


class NativeTests(unittest.TestCase):
    def test_framed_protocol_and_restricted_operations(self):
        data = json.dumps(
            {
                "action": "stop",
                "instance": "test",
                "revision": "idle",
                "private": True,
                "managed": True,
                "password": "not forwarded",
            }
        ).encode()
        source = io.BytesIO(struct.pack("=I", len(data)) + data)
        target = io.BytesIO()
        calls = []

        def request(action, **args):
            calls.append((action, args))
            return {"phase": "idle"}

        serve(source, target, request)
        self.assertEqual(calls[0][0], "browser")
        self.assertNotIn("password", calls[0][1])
        output = target.getvalue()
        size = struct.unpack("=I", output[:4])[0]
        self.assertEqual(json.loads(output[4 : 4 + size]), {"phase": "idle"})

    def test_oversized_message_rejected(self):
        output = io.BytesIO()
        serve(io.BytesIO(struct.pack("=I", 65537)), output)
        self.assertEqual(output.getvalue(), b"")


if __name__ == "__main__":
    unittest.main()
