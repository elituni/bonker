import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from focus_blocker.rules import Invalid
from scripts.build_extension import RELEASE_FILES, build_extension
from scripts.install import validate_xpi


class PackageTests(unittest.TestCase):
    def test_icons_are_packaged_as_transparent_pngs_and_versions_match(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "extension.zip"
            subprocess.run(
                [sys.executable, str(root / "scripts/build_extension.py"), "--output", str(path)],
                check=True,
                capture_output=True,
            )
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["name"], "Bonker")
                self.assertEqual(
                    archive.read("bonk.jpg"), (root / "packaging/bonk.jpg").read_bytes()
                )
                self.assertIn("presentation.js", archive.namelist())
                icons = {**manifest["icons"], **manifest["browser_action"]["default_icon"]}
                for size, name in icons.items():
                    data = archive.read(name)
                    self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                    self.assertEqual(struct.unpack(">II", data[16:24]), (int(size), int(size)))
                    self.assertEqual(data[25], 6, "PNG must preserve RGBA transparency")
            # Synthetic signature marker only tests installer metadata checks;
            # it is never installed and is not represented as a signed artifact.
            old = Path(tmp) / "old.xpi"
            manifest["version"] = "0.0.1"
            with zipfile.ZipFile(old, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("META-INF/mozilla.rsa", "test fixture")
            with self.assertRaisesRegex(Invalid, "version"):
                validate_xpi(old)


class ExtensionBuildPrivacyTests(unittest.TestCase):
    def fixture(self, root):
        shutil.copytree(Path(__file__).resolve().parents[1] / "firefox", root)

    def test_only_release_files_are_packaged_despite_local_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "firefox"
            self.fixture(root)
            secret = Path(tmp) / "outside.key"
            secret.write_text("private fixture")
            for name in (".env", "credentials.json", "backup.zip", "icons/private.key"):
                (root / name).write_text("private fixture")
            (root / "external.key").symlink_to(secret)
            output = Path(tmp) / "extension.zip"
            build_extension(root, output)
            first = output.read_bytes()
            build_extension(root, output)
            self.assertEqual(output.read_bytes(), first)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), set(RELEASE_FILES))
                for name in RELEASE_FILES:
                    self.assertEqual(archive.read(name), (root / name).read_bytes())

    def test_required_file_or_directory_symlinks_fail_before_output_changes(self):
        for kind in ("file", "icons", "root", "missing"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "firefox"
                self.fixture(root)
                if kind == "file":
                    original = root / "background.js"
                    outside = Path(tmp) / "outside.js"
                    original.rename(outside)
                    original.symlink_to(outside)
                elif kind in ("icons", "root"):
                    original = root / "icons" if kind == "icons" else root
                    outside = Path(tmp) / "outside"
                    original.rename(outside)
                    original.symlink_to(outside, target_is_directory=True)
                else:
                    (root / "background.js").unlink()
                output = Path(tmp) / "extension.zip"
                output.write_bytes(b"existing delivery")
                with self.assertRaises(ValueError):
                    build_extension(root, output)
                self.assertEqual(output.read_bytes(), b"existing delivery")
