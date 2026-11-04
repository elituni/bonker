"""Publication boundaries for source bundles, independent of a Git installation."""

import tempfile
import unittest
from pathlib import Path

from scripts.build_bundle import public_source_files


class SourceBundlePrivacyTests(unittest.TestCase):
    def test_local_state_credentials_evidence_and_keys_stay_out(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / ".gitignore").write_bytes((root / ".gitignore").read_bytes())
            private = (
                "docs/HANDOFF.md",
                "docs/NEW_CHAT_HANDOFF.md",
                "docs/UPGRADE_0.3.0.md",
                "docs/validation/report.json",
                "docs/screenshots/draft.png",
                "tests/.env",
                "tests/.env.production",
                "tests/credentials-amo.json",
                "tests/secrets-local.json",
                "tests/passwords.sqlite3-wal",
                "tests/state.db",
                "tests/signing.key",
                "tests/cert.pem",
                "tests/browser.xpi",
                "tests/profile.zip",
                "tests/firefox-profile-local/prefs.js",
                "tests/__pycache__/code.pyc",
                "tests/private/notes.txt",
                "tests/.aws/config",
            )
            public = (
                "README.md",
                "docs/INSTALLATION.md",
                "tests/.env.example",
                "tests/test_core.py",
            )
            for name in private + public:
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            bundled = {
                path.relative_to(staging).as_posix() for path in public_source_files(staging)
            }
            self.assertFalse(bundled.intersection(private))
            self.assertTrue(set(public).issubset(bundled))

    def test_symlink_cannot_include_external_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("")
            (root / "docs").mkdir()
            outside = root / "secret.txt"
            outside.write_text("private fixture")
            (root / "docs/linked.txt").symlink_to(outside)
            self.assertNotIn(root / "docs/linked.txt", public_source_files(root))
