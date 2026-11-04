#!/usr/bin/env python3
"""Explicit Fedora installer. --stage writes a reviewable image without changing the host."""

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from focus_blocker import EXTENSION_ID  # noqa: E402
from focus_blocker.client import ServiceError  # noqa: E402
from focus_blocker.lifecycle import exclusive_lock, require_offline_idle  # noqa: E402
from focus_blocker.policy import merge_policy, remove_policy  # noqa: E402
from focus_blocker.rules import Invalid  # noqa: E402


def atomic(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise Invalid(f"Refusing to replace symlink: {path}")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".focus-blocker-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data if isinstance(data, bytes) else data.encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def validate_xpi(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest["browser_specific_settings"]["gecko"]["id"] != EXTENSION_ID:
            raise Invalid("The signed XPI has the wrong extension identifier.")
        expected = json.loads((ROOT / "firefox/manifest.json").read_text())["version"]
        if manifest.get("version") != expected:
            raise Invalid(
                f"Use the signed extension version {expected} that matches this source bundle."
            )
        if not any(name.lower() == "meta-inf/mozilla.rsa" for name in archive.namelist()):
            raise Invalid("The XPI has no Mozilla signature. Sign the unlisted extension first.")
    # Firefox performs the cryptographic signature/trust validation at installation.


def install(args):
    stage = Path(args.stage).resolve() if args.stage else Path("/")
    if args.stage and stage == Path("/"):
        raise Invalid("A staging directory cannot be the filesystem root.")

    def dest(name):
        return stage / name.lstrip("/")

    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0:
        raise Invalid("Choose your ordinary desktop user, not root.")
    if not args.stage:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise Invalid(
                "Install on Fedora as root, or use --stage for a nonprivileged package preview."
            )
        os_release = Path("/etc/os-release").read_text()
        if "ID=fedora" not in os_release and 'ID="fedora"' not in os_release:
            raise Invalid("This installer targets Fedora.")
        for p in (
            "/usr/lib64/firefox/distribution/policies.json",
            "/usr/lib/firefox/distribution/policies.json",
        ):
            if Path(p).exists():
                raise Invalid(
                    f"Existing installation-specific Firefox policy at {p}; reconcile it before installation."
                )
        if not shutil.which("firefox"):
            raise Invalid("Install Fedora's native Firefox package first.")
        subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                "import gi, idna; gi.require_version('Adw', '1'); from gi.repository import Adw",
            ],
            check=True,
        )
        # GTK3 lives in the separate decoration process; it cannot share a
        # process with the main GTK4/libadwaita interface.
        subprocess.run(
            [
                "/usr/bin/python3",
                "-c",
                "import gi, cairo; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk",
            ],
            check=True,
        )
    state = dest("/var/lib/focus-blocker")
    receipt_path = state / "installation.json"
    if receipt_path.exists():
        raise Invalid(
            "An installation already exists. Use --repair to retry activation, or --uninstall before replacing files. Retained logs survive uninstall."
        )
    config_path = dest("/etc/focus-blocker/config.json")
    if config_path.exists() and json.loads(config_path.read_text())["uid"] != account.pw_uid:
        raise Invalid(
            "Existing data belongs to another configured user. Do not reuse it for a different account."
        )
    if not args.signed_xpi and not args.stage:
        raise Invalid("Provide --signed-xpi with your Mozilla-signed extension.")
    if args.signed_xpi:
        validate_xpi(args.signed_xpi)
    policy_path = dest("/etc/firefox/policies/policies.json")
    if policy_path.is_symlink():
        raise Invalid("Refusing to replace a symlinked Firefox policy.")
    previous = json.loads(policy_path.read_text()) if policy_path.exists() else {}
    policy, policy_receipt = merge_policy(
        previous, "file:///usr/local/share/focus-blocker/firefox.xpi"
    )
    files = []
    planned = []

    def write(name, data, mode=0o644):
        files.append(name)
        planned.append((dest(name), data, mode))

    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    for source in sorted((ROOT / "focus_blocker").glob("*.py")):
        write("/usr/local/lib/focus-blocker/focus_blocker/" + source.name, source.read_bytes())
    for executable, module in {
        "focus-blocker": "ui",
        "focus-blocker-service": "service",
        "focus-blocker-native": "native",
        "focus-blocker-notifier": "notifier",
        "focus-blocker-cli": "cli",
    }.items():
        write(
            "/usr/local/bin/" + executable,
            f"#!/usr/bin/python3 -I\nimport sys\nsys.path.insert(0, '/usr/local/lib/focus-blocker')\nfrom focus_blocker.{module} import main\nmain()\n",
            0o755,
        )
    write(
        "/usr/local/share/applications/org.focusblocker.App.desktop",
        (ROOT / "packaging/org.focusblocker.App.desktop").read_bytes(),
    )
    for name in (
        "/usr/local/share/applications/org.focusblocker.Notifier.desktop",
        "/etc/xdg/autostart/org.focusblocker.Notifier.desktop",
    ):
        write(name, (ROOT / "packaging/org.focusblocker.Notifier.desktop").read_bytes())
    write(
        "/usr/local/share/focus-blocker/bonker-walk.gif",
        (ROOT / "packaging/bonker-walk.gif").read_bytes(),
    )
    write(
        "/usr/local/share/icons/hicolor/512x512/apps/org.focusblocker.App.png",
        (ROOT / "packaging/org.focusblocker.App.png").read_bytes(),
    )
    write(
        "/etc/systemd/system/focus-blocker.service",
        (ROOT / "packaging/focus-blocker.service").read_bytes(),
    )
    native = {
        "name": "org.focusblocker.bridge",
        "description": "Local Bonker bridge",
        "path": "/usr/local/bin/focus-blocker-native",
        "type": "stdio",
        "allowed_extensions": [EXTENSION_ID],
    }
    # Fedora's 64-bit Firefox resolves XRESysNativeManifests under /usr/lib64.
    # Register both standard Linux locations; both are collision-checked,
    # recorded in the receipt, labeled and removed by the same installer guards.
    for libdir in ("lib", "lib64"):
        write(
            f"/usr/{libdir}/mozilla/native-messaging-hosts/org.focusblocker.bridge.json",
            json.dumps(native, indent=2) + "\n",
        )
    if args.signed_xpi:
        write("/usr/local/share/focus-blocker/firefox.xpi", Path(args.signed_xpi).read_bytes())
    write(
        "/etc/focus-blocker/config.json",
        json.dumps({"uid": account.pw_uid, "user": account.pw_name}, indent=2) + "\n",
        0o644,
    )
    # Validate the complete plan before writing: never replace another application's files.
    for path, _, _ in planned:
        if path.is_symlink() or (path.exists() and path != config_path):
            raise Invalid(f"Installation path already exists: {path}")
    backups = {
        path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
        for path, _, _ in planned
    }
    old_policy = policy_path.read_bytes() if policy_path.exists() else None
    try:
        for path, data, mode in planned:
            atomic(path, data, mode)
        # Policy edits are last. A missing XPI in a staged image is intentional and reported.
        atomic(policy_path, json.dumps(policy, indent=2) + "\n")
        atomic(
            receipt_path,
            json.dumps(
                {"files": files, "policy": policy_receipt, "phase": "files-installed"}, indent=2
            )
            + "\n",
            0o600,
        )
    except OSError:
        for path, original in backups.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                atomic(path, original[0], original[1])
        if old_policy is None:
            policy_path.unlink(missing_ok=True)
        else:
            atomic(policy_path, old_policy)
        receipt_path.unlink(missing_ok=True)
        raise
    if args.stage:
        print(f"Staged installation at {stage}. No services or host policies changed.")
        if not args.signed_xpi:
            print("Preview only: no signed XPI is included; this image is not ready to deploy.")
    else:
        activate(receipt_path)
        print("Installed. Restart Firefox, verify about:policies, then open Bonker.")


def activate(receipt_path):
    try:
        subprocess.run(
            [
                "restorecon",
                "-RF",
                "/usr/local/lib/focus-blocker",
                "/usr/local/share/focus-blocker",
                "/usr/lib/mozilla/native-messaging-hosts",
                "/usr/lib64/mozilla/native-messaging-hosts",
                "/etc/focus-blocker",
                "/var/lib/focus-blocker",
            ],
            check=True,
        )
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "focus-blocker.service"], check=True)
        receipt = json.loads(receipt_path.read_text())
        receipt["phase"] = "activated"
        atomic(receipt_path, json.dumps(receipt, indent=2) + "\n", 0o600)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Invalid(
            "Files are installed, but activation did not finish. Fix the reported dependency/SELinux/service error and run this installer with --repair. An idle failed installation can also be removed with --uninstall. "
            + str(exc)
        ) from exc


def repair(args):
    stage = Path(args.stage).resolve() if args.stage else Path("/")
    if args.stage and stage == Path("/"):
        raise Invalid("A staging directory cannot be the filesystem root.")
    if not args.stage and (sys.platform != "linux" or os.geteuid() != 0):
        raise Invalid("Repair on Fedora as root, or validate a --stage directory.")
    receipt_path = stage / "var/lib/focus-blocker/installation.json"
    receipt = json.loads(receipt_path.read_text())
    if receipt.get("phase") == "removing":
        raise Invalid("Removal was interrupted. Repeat --uninstall to finish before reinstalling.")
    for name in receipt["files"]:
        path = stage / name.lstrip("/")
        if not path.is_file() or path.is_symlink():
            raise Invalid(
                f"Installed file is missing or unsafe: {path}. Finish --uninstall before reinstalling."
            )
        if not args.stage and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022):
            raise Invalid(f"Installed file is not protected against ordinary-user changes: {path}")
    if args.stage:
        print("Staged files validated. No host activation performed.")
    else:
        activate(receipt_path)
        print("Service activation retried. Check systemctl status and Firefox about:policies.")


@contextmanager
def removal_guard(state):
    from focus_blocker.client import call

    with ExitStack() as locks:
        reserved = False
        try:
            call("begin_uninstall")
            reserved = True
        except ServiceError:
            # Never infer idle from a broken socket. Exclude both a live daemon
            # and a concurrent daemon start, then inspect retained state.
            locks.enter_context(exclusive_lock(state / "service.lock"))
            active = subprocess.run(
                ["systemctl", "show", "-p", "ActiveState", "--value", "focus-blocker.service"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if active not in ("inactive", "failed"):
                raise Invalid("The service is not safely stopped. Use --repair before removal.")
            require_offline_idle(state / "state.sqlite3")
        try:
            subprocess.run(["systemctl", "disable", "--now", "focus-blocker.service"], check=True)
        except (OSError, subprocess.CalledProcessError):
            if reserved:
                try:
                    call("abort_uninstall")
                except ServiceError:
                    pass
            raise
        if reserved:
            locks.enter_context(exclusive_lock(state / "service.lock"))
            require_offline_idle(state / "state.sqlite3")
        yield


def uninstall(args):
    stage = Path(args.stage).resolve() if args.stage else Path("/")
    if args.stage and stage == Path("/"):
        raise Invalid("A staging directory cannot be the filesystem root.")
    if not args.stage and (sys.platform != "linux" or os.geteuid() != 0):
        raise Invalid("Uninstall on Fedora as root, or pass a staged image directory.")
    state = stage / "var/lib/focus-blocker"
    receipt = json.loads((state / "installation.json").read_text())
    policy_path = stage / "etc/firefox/policies/policies.json"
    current = json.loads(policy_path.read_text())
    restored = remove_policy(current, receipt["policy"])
    with ExitStack() as locks:
        if not args.stage:
            locks.enter_context(removal_guard(state))
        receipt["phase"] = "removing"
        atomic(state / "installation.json", json.dumps(receipt, indent=2) + "\n", 0o600)
        atomic(policy_path, json.dumps(restored, indent=2) + "\n")
        for name in receipt["files"]:
            # Keep configuration to retain the data-owner identity across reinstall.
            if name != "/etc/focus-blocker/config.json":
                (stage / name.lstrip("/")).unlink(missing_ok=True)
        if not args.stage:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
        (state / "installation.json").unlink()
    print(
        "Uninstalled. Session database, password, presets and permanent log are retained. Restart Firefox."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user", default=os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name
    )
    parser.add_argument("--signed-xpi")
    parser.add_argument("--stage", help="Write into a directory instead of modifying the host")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--uninstall", action="store_true")
    operations.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    try:
        operation = uninstall if args.uninstall else repair if args.repair else install
        if args.stage:
            operation(args)
        else:
            if sys.platform != "linux" or os.geteuid() != 0:
                raise Invalid("Run on Fedora as root, or use --stage.")
            with exclusive_lock(Path("/run/lock/focus-blocker-install.lock")):
                operation(args)
    except (
        Invalid,
        ServiceError,
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
