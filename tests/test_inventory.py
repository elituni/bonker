import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from focus_blocker.apps import App, Monitor, Process, discover
from focus_blocker.rules import Invalid
from focus_blocker.ui_state import application_choices


class InventoryTests(unittest.TestCase):
    def test_hidden_launchers_keep_identity_but_not_duplicate_picker_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, hidden in [
                ("org.kde.krita", False),
                ("krita_png", True),
                ("krita_jpeg", True),
            ]:
                (root / (name + ".desktop")).write_text(
                    "[Desktop Entry]\nType=Application\nName=Krita\nExec=/usr/bin/krita %F\n"
                    + ("NoDisplay=true\n" if hidden else "")
                )
            apps = discover(tmp, roots=[root])
        self.assertEqual(len(apps), 3)
        rows = application_choices([asdict(app) for app in apps.values()], set())
        self.assertEqual([r["id"] for r in rows], ["org.kde.krita.desktop"])
        self.assertEqual(rows[0]["aliases"], set(apps))
        selected = {"krita_png.desktop"}
        self.assertTrue(rows[0]["aliases"] & selected)
        m = Monitor.__new__(Monitor)
        m.apps = apps
        m.processes = {1: Process(1, 0, "1", "/usr/bin/krita", ["krita"], "")}
        config = {"apps": list(selected), "mode": "blacklist", "rules": []}
        m.validate(config)
        self.assertEqual(set(m.targets(config)), {1})

    def test_user_hidden_override_masks_system_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, user = Path(tmp) / "base", Path(tmp) / "user"
            base.mkdir()
            user.mkdir()
            (base / "editor.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Editor\nExec=/usr/bin/editor\n"
            )
            (user / "editor.desktop").write_text("[Desktop Entry]\nHidden=true\n")
            self.assertEqual(discover(tmp, roots=[base, user]), {})

    def test_settings_panels_cannot_target_settings_even_from_old_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bluetooth.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Bluetooth\n"
                "Exec=/usr/bin/gnome-control-center bluetooth\nNoDisplay=true\n"
            )
            apps = discover(tmp, roots=[root])
        m = Monitor.__new__(Monitor)
        m.apps = apps
        m.processes = {1: Process(1, 0, "1", "/usr/bin/gnome-control-center", [], "/app.slice/")}
        config = {"apps": list(apps), "mode": "blacklist", "rules": []}
        with self.assertRaises(Invalid):
            m.validate(config)
        self.assertFalse(m.targets(config))
        self.assertFalse(m.targets({**config, "mode": "whitelist", "apps": []}))
        self.assertEqual(application_choices([asdict(a) for a in apps.values()], set()), [])

    def test_hidden_only_entries_remain_removable_when_saved(self):
        app = asdict(App("hidden", "Hidden helper", "", "/usr/bin/helper", visible=False))
        self.assertEqual(application_choices([app], set()), [])
        self.assertEqual(len(application_choices([app], {"hidden"})), 1)

    def test_flatpaks_and_scripts_are_not_grouped_by_shared_interpreter(self):
        apps = [
            App("a", "A", "", "/usr/bin/flatpak", flatpak="org.example.A"),
            App("b", "B", "", "/usr/bin/flatpak", flatpak="org.example.B"),
            App("c", "C", "", "/usr/bin/python3", script="/opt/c.py"),
            App("d", "D", "", "/usr/bin/python3", script="/opt/d.py"),
        ]
        self.assertEqual(len(application_choices([asdict(a) for a in apps], set())), 4)
