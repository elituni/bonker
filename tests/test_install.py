import argparse
import contextlib
import io
import json
import os
import pwd
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from focus_blocker.rules import Invalid
from scripts import install as installer


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        user = pwd.getpwuid(os.getuid()).pw_name if os.getuid() else "nobody"
        self.args = argparse.Namespace(stage=str(self.root), user=user, signed_xpi=None)
        self.policy = self.root / "etc/firefox/policies/policies.json"
        self.policy.parent.mkdir(parents=True)
        self.original = {"policies": {"Homepage": {"URL": "https://example.org"}}}
        self.policy.write_text(json.dumps(self.original))

    def tearDown(self):
        self.temp.cleanup()

    def test_install_uninstall_preserves_existing_policies_and_history(self):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.args)
            history = self.root / "var/lib/focus-blocker/state.sqlite3"
            history.write_bytes(b"retained history")
            self.assertTrue((self.root / "usr/local/bin/focus-blocker").exists())
            animation = self.root / "usr/local/share/focus-blocker/bonker-walk.gif"
            self.assertEqual(
                animation.read_bytes(), (installer.ROOT / "packaging/bonker-walk.gif").read_bytes()
            )
            installer.uninstall(self.args)
        self.assertEqual(json.loads(self.policy.read_text()), self.original)
        self.assertEqual(history.read_bytes(), b"retained history")
        self.assertFalse((self.root / "usr/local/bin/focus-blocker").exists())
        self.assertFalse(animation.exists())

    def test_does_not_overwrite_a_colliding_executable(self):
        existing = self.root / "usr/local/bin/focus-blocker"
        existing.parent.mkdir(parents=True)
        existing.write_text("unrelated program")
        with self.assertRaises(Invalid):
            installer.install(self.args)
        self.assertEqual(existing.read_text(), "unrelated program")
        self.assertEqual(json.loads(self.policy.read_text()), self.original)

    def test_native_host_registered_and_removed_in_both_linux_libdirs(self):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.args)
            paths = [
                self.root / f"usr/{lib}/mozilla/native-messaging-hosts/org.focusblocker.bridge.json"
                for lib in ("lib", "lib64")
            ]
            for path in paths:
                manifest = json.loads(path.read_text())
                self.assertEqual(manifest["path"], "/usr/local/bin/focus-blocker-native")
                self.assertEqual(manifest["allowed_extensions"], ["focus-blocker@local.invalid"])
            receipt = json.loads(
                (self.root / "var/lib/focus-blocker/installation.json").read_text()
            )
            self.assertTrue(
                all("/" + str(p.relative_to(self.root)) in receipt["files"] for p in paths)
            )
            installer.uninstall(self.args)
        self.assertTrue(all(not p.exists() for p in paths))

    def test_existing_lib64_native_host_is_not_overwritten(self):
        path = self.root / "usr/lib64/mozilla/native-messaging-hosts/org.focusblocker.bridge.json"
        path.parent.mkdir(parents=True)
        path.write_text("unrelated host")
        with self.assertRaises(Invalid):
            installer.install(self.args)
        self.assertEqual(path.read_text(), "unrelated host")
        self.assertFalse((self.root / "usr/local/bin/focus-blocker").exists())
        self.assertEqual(json.loads(self.policy.read_text()), self.original)

    def test_file_write_failure_rolls_back(self):
        original_atomic = installer.atomic
        failed = False

        def atomic(path, data, mode=0o644):
            nonlocal failed
            if path.name == "focus-blocker-service" and not failed:
                failed = True
                raise OSError("simulated write error")
            original_atomic(path, data, mode)

        with patch.object(installer, "atomic", atomic), self.assertRaises(OSError):
            installer.install(self.args)
        self.assertFalse((self.root / "usr/local/bin/focus-blocker").exists())
        self.assertFalse((self.root / "var/lib/focus-blocker/installation.json").exists())
        self.assertEqual(json.loads(self.policy.read_text()), self.original)

    def test_staging_cannot_be_used_to_bypass_host_checks(self):
        self.args.stage = "/"
        for operation in (installer.install, installer.uninstall, installer.repair):
            with self.subTest(operation=operation.__name__), self.assertRaises(Invalid):
                operation(self.args)

    def test_interrupted_removal_can_be_repeated(self):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.args)
            original = installer.atomic

            def fail_policy(path, data, mode=0o644):
                if path.resolve() == self.policy.resolve():
                    raise OSError("disk failure")
                original(path, data, mode)

            with patch.object(installer, "atomic", fail_policy), self.assertRaises(OSError):
                installer.uninstall(self.args)
            receipt = self.root / "var/lib/focus-blocker/installation.json"
            self.assertEqual(json.loads(receipt.read_text())["phase"], "removing")
            with self.assertRaisesRegex(Invalid, "Repeat --uninstall"):
                installer.repair(self.args)
            installer.uninstall(self.args)
            self.assertFalse(receipt.exists())
            self.assertEqual(json.loads(self.policy.read_text()), self.original)

    def test_staged_repair_validates_files_without_host_commands(self):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.args)
            with patch.object(installer.subprocess, "run") as run:
                installer.repair(self.args)
                run.assert_not_called()
            (self.root / "usr/local/bin/focus-blocker").unlink()
            with self.assertRaises(Invalid):
                installer.repair(self.args)


if __name__ == "__main__":
    unittest.main()
