import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from focus_blocker.app_identity import forwarded_program, runtime_identities
from focus_blocker.apps import App, Monitor, Process, app_matches, discover


def process(pid, exe, parent=0, args=None, scope=""):
    return Process(pid, parent, str(pid), exe, args or [exe], scope)


class LauncherIdentityTests(unittest.TestCase):
    def test_sibling_launcher_and_helpers_survive_unrelated_chromium_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "ChatGPT"
            binary.touch()
            launcher = root / "codex-launcher"
            launcher.write_text('#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/ChatGPT" "$@"\n')
            runtime = runtime_identities(str(launcher))
            self.assertIn((str(binary), ""), runtime)
            app = App("chatgpt.desktop", "ChatGPT", "", str(launcher), runtime=runtime)
            other = App("editor.desktop", "Editor", "", "/usr/bin/editor")
            browser = App("brave.desktop", "Brave", "", "/opt/brave/brave", browser=True)
            m = Monitor.__new__(Monitor)
            m.apps = {a.id: a for a in [app, other, browser]}
            scope = "/app.slice/app-org.chromium.Chromium-10.scope"
            m.processes = {
                1: process(1, str(binary), scope=scope),
                2: process(2, str(root / "resources/codex"), parent=1, scope=scope),
                3: process(3, "/opt/node", parent=2, scope=scope),
                4: process(4, "/usr/bin/editor", parent=1, scope=scope),
                5: process(5, "/opt/brave/brave", parent=1, scope=scope),
            }
            config = {"mode": "whitelist", "apps": [app.id], "rules": []}
            self.assertEqual(set(m.targets(config)), {4, 5})
            # Explicit app blocking still works for the same launcher and children.
            self.assertEqual(set(m.targets({**config, "mode": "blacklist"})), {1, 2, 3, 4, 5})

    def test_interpreted_desktop_entry_matches_only_its_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "tweaks"
            script.write_text('#!/usr/bin/python3\nprint("fixture never executed")\n')
            (root / "tweaks.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Tweaks\nExec=" + str(script) + "\n"
            )
            app = discover(tmp, roots=[root])["tweaks.desktop"]
            python = os.path.realpath("/usr/bin/python3")
            self.assertTrue(app_matches(app, process(1, python, args=[python, str(script)])))
            self.assertFalse(
                app_matches(app, process(2, python, args=[python, "/tmp/other.py", str(script)]))
            )
            self.assertFalse(app_matches(app, process(3, python, args=[python, "-c", str(script)])))

    def test_shared_interpreter_without_script_does_not_claim_every_process(self):
        app = App("module.desktop", "Module", "", "/usr/bin/python3")
        self.assertFalse(
            app_matches(app, process(1, app.executable, args=[app.executable, "-c", "pass"]))
        )

    def test_program_name_starting_with_sh_is_not_an_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "showcase"
            executable.touch()
            (root / "showcase.desktop").write_text(
                f"[Desktop Entry]\nType=Application\nName=Showcase\nExec={executable} /tmp/example\n"
            )
            app = discover(tmp, roots=[root])["showcase.desktop"]
            self.assertEqual(app.script, "")
            self.assertTrue(app_matches(app, process(1, str(executable))))

    def test_javascript_and_shell_scripts_have_exact_runtime_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            for interpreter in ("/usr/bin/gjs-console", "/usr/bin/bash"):
                script = Path(tmp) / Path(interpreter).name
                script.write_text("#!" + interpreter + "\n# fixture\n")
                self.assertIn(
                    (os.path.realpath(interpreter), str(script)), runtime_identities(str(script))
                )

    def test_literal_exec_is_resolved_without_evaluating_shell(self):
        self.assertEqual(
            forwarded_program("/opt/app/start", '#!/bin/sh\nexec "/opt/app/main" "$@"\n'),
            "/opt/app/main",
        )
        for body in [
            'exec "$(touch /tmp/should-not-run)" "$@"',
            'touch /tmp/should-not-run\nexec /opt/app/main "$@"',
            'exec /opt/app/main "$@"; echo unsafe',
            'exec /opt/app/* "$@"',
            'exec /opt/app/main;other "$@"',
            'exec "$(dirname "$0")/../other" "$@"',
        ]:
            self.assertIsNone(forwarded_program("/opt/app/start", "#!/bin/sh\n" + body))

    def test_fifo_and_wrapper_cycles_are_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "fifo"
            os.mkfifo(fifo)
            self.assertEqual(runtime_identities(str(fifo)), ())
            launcher = Path(tmp) / "start"
            launcher.write_text('#!/bin/sh\nexec "' + str(launcher) + '" "$@"\n')
            self.assertLessEqual(len(runtime_identities(str(launcher))), 2)

    def test_fedora_aliases_are_exact_paths(self):
        with patch("focus_blocker.app_identity.os.path.isfile", return_value=True):
            terminal = runtime_identities("/usr/bin/gnome-terminal")
            office = runtime_identities("/usr/bin/libreoffice")
        self.assertIn(("/usr/libexec/gnome-terminal-server", ""), terminal)
        self.assertIn(("/usr/lib64/libreoffice/program/soffice.bin", ""), office)
        self.assertNotIn(("/tmp/gnome-terminal-server", ""), terminal)

    def test_allowed_independent_app_is_not_swept_up_as_blocked_parent_helper(self):
        m = Monitor.__new__(Monitor)
        m.apps = {
            "blocked": App("blocked", "Blocked", "", "/opt/blocked"),
            "allowed": App("allowed", "Allowed", "", "/opt/allowed"),
        }
        m.processes = {
            1: process(1, "/opt/blocked"),
            2: process(2, "/opt/allowed", parent=1),
            3: process(3, "/opt/helper", parent=1),
        }
        config = {"mode": "whitelist", "apps": ["allowed"], "rules": []}
        self.assertEqual(set(m.targets(config)), {1, 3})

    def test_blocked_app_helpers_do_not_escape_through_an_inherited_allowed_scope(self):
        m = Monitor.__new__(Monitor)
        m.apps = {
            "editor.desktop": App("editor.desktop", "Editor", "", "/opt/editor"),
            "other.desktop": App("other.desktop", "Other", "", "/opt/other"),
        }
        scope = "/app.slice/app-gnome-editor-1.scope"
        m.processes = {
            1: process(1, "/opt/editor", scope=scope),
            2: process(2, "/opt/other", parent=1, scope=scope),
            3: process(3, "/opt/helper", parent=2, scope=scope),
        }
        config = {"mode": "whitelist", "apps": ["editor.desktop"], "rules": []}
        self.assertEqual(set(m.targets(config)), {2, 3})
