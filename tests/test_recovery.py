import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_core import FakeClock, config

from focus_blocker.client import ServiceError, export_audit, write_audit
from focus_blocker.engine import Engine
from focus_blocker.lifecycle import exclusive_lock, require_offline_idle
from focus_blocker.rules import Invalid
from focus_blocker.storage import Store
from scripts import install as installer


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "state.sqlite3"
        self.store = Store(self.path)
        self.clock = FakeClock()
        self.engine = Engine(self.store, self.clock)
        self.engine.set_password("secret")

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_offline_check_rejects_active_pending_and_corrupt_state(self):
        require_offline_idle(self.path, self.clock)
        self.engine.prepare(config(1))
        self.engine.activate()
        with self.assertRaises(Invalid):
            require_offline_idle(self.path, self.clock)
        self.clock.boot_id = "boot-b"
        with self.assertRaises(Invalid):
            require_offline_idle(self.path, self.clock)
        self.clock.advance(60)
        require_offline_idle(self.path, self.clock)
        with self.store.db:
            self.store.put("release_pending", {"session_id": "a"})
        with self.assertRaises(Invalid):
            require_offline_idle(self.path, self.clock)
        with self.store.db:
            self.store.put("release_pending", None)
            self.store.put("active", {"duration": float("nan")})
        with self.assertRaises(Invalid):
            require_offline_idle(self.path, self.clock)

    def test_lifetime_lock_excludes_concurrent_removal_or_start(self):
        path = self.root / "service.lock"
        with exclusive_lock(path):
            with self.assertRaises(Invalid), exclusive_lock(path):
                pass
        with exclusive_lock(path):
            pass

    def test_missing_socket_does_not_imply_idle(self):
        with (
            patch("focus_blocker.client.call", side_effect=ServiceError("offline")),
            patch.object(installer.subprocess, "run", return_value=Mock(stdout="active\n")) as run,
        ):
            with self.assertRaises(Invalid), installer.removal_guard(self.root):
                pass
            self.assertEqual(run.call_count, 1, "must not stop an active unresponsive service")

    def test_failed_idle_install_can_be_removed_offline(self):
        with (
            patch("focus_blocker.client.call", side_effect=ServiceError("offline")),
            patch.object(installer.subprocess, "run", return_value=Mock(stdout="failed\n")) as run,
        ):
            with installer.removal_guard(self.root):
                with self.assertRaises(Invalid), exclusive_lock(self.root / "service.lock"):
                    pass
            self.assertEqual(run.call_count, 2)

    def test_stop_failure_aborts_online_reservation(self):
        with (
            patch("focus_blocker.client.call", return_value={}) as call,
            patch.object(installer.subprocess, "run", side_effect=OSError("cannot stop")),
        ):
            with self.assertRaises(OSError), installer.removal_guard(self.root):
                pass
            self.assertEqual(
                [c.args[0] for c in call.call_args_list], ["begin_uninstall", "abort_uninstall"]
            )

    def test_activation_failures_leave_receipt_for_retry(self):
        receipt = self.root / "installation.json"
        for position in range(3):
            receipt.write_text(json.dumps({"phase": "files-installed", "files": []}))
            outcomes = [Mock()] * position + [OSError("activation failure")]
            with patch.object(installer.subprocess, "run", side_effect=outcomes):
                with self.assertRaisesRegex(Invalid, "--repair"):
                    installer.activate(receipt)
            self.assertEqual(json.loads(receipt.read_text())["phase"], "files-installed")
            with patch.object(installer.subprocess, "run", return_value=Mock()):
                installer.activate(receipt)
            self.assertEqual(json.loads(receipt.read_text())["phase"], "activated")


class AuditPagingTests(unittest.TestCase):
    def test_large_unicode_log_pages_and_snapshot_export(self):
        store = Store(":memory:")
        self.addCleanup(store.db.close)
        with store.db:
            for i in range(80):
                store.append(
                    {"event": "release_authorized", "session_id": str(i), "reason": "😀" * 10000}
                )
                store.append({"event": "released", "session_id": str(i), "timestamp": i + 1})
        first = store.audit_page(limit=1)
        with store.db:
            store.append({"event": "released", "session_id": "future", "timestamp": 999})
        second = store.audit_page(first["next_before"], first["through"], limit=1)
        self.assertEqual(second["records"][0]["released_at"], 80)
        self.assertEqual(second["through"], 160)
        sizes = []

        def fetch(action, **args):
            self.assertEqual(action, "audit")
            page = store.audit_page(**args)
            sizes.append(len(json.dumps({"ok": True, "result": page}).encode()))
            return page

        output = io.StringIO()
        write_audit(output, fetch)
        records = json.loads(output.getvalue())
        self.assertEqual(len(records), 161)
        self.assertEqual(len({r["record_id"] for r in records}), 161)
        self.assertTrue(all(size < 4 * 1024 * 1024 for size in sizes))
        for invalid in (0, 26, True, "25"):
            with self.assertRaises(Invalid):
                store.audit_page(limit=invalid)

    def test_failed_export_keeps_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "export.json"
            destination.write_text("existing backup")
            with self.assertRaises(ServiceError):
                export_audit(destination, Mock(side_effect=ServiceError("offline")))
            self.assertEqual(destination.read_text(), "existing backup")
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)
