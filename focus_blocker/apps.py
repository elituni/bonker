"""Linux desktop inventory and process monitoring. Never executes desktop Exec strings."""

import configparser
import os
import re
import shlex
import shutil
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .app_identity import INTERPRETERS, runtime_identities, script_argument_matches
from .rules import Invalid, websites_enabled

PROTECTED = {
    "Xwayland",
    "Xorg",
    "gnome-session-ctl",
    "gnome-session-service",
    "gnome-session-init-worker",
    "gdm-wayland-session",
    "dbus-broker-launch",
    "dconf-service",
    "goa-daemon",
    "goa-identity-service",
    "evolution-source-registry",
    "evolution-addressbook-factory",
    "evolution-calendar-factory",
    "evolution-alarm-notify",
    "flatpak-session-helper",
    "flatpak-portal",
    "p11-kit-server",
    "obexd",
    "abrt-applet",
    "gnome-shell",
    "gnome-control-center",
    "gnome-session",
    "gnome-session-binary",
    "gnome-keyring-daemon",
    "gdm",
    "gdm-session-worker",
    "systemd",
    "dbus-broker",
    "dbus-daemon",
    "pipewire",
    "wireplumber",
    "pulseaudio",
    "at-spi-bus-launcher",
    "at-spi2-registryd",
    "xdg-desktop-portal",
    "xdg-desktop-portal-gnome",
    "xdg-desktop-portal-gtk",
    "polkitd",
    "polkit-gnome-authentication-agent-1",
    "gnome-shell-calendar-server",
    "ibus-daemon",
    "ibus-extension-gtk3",
    "ibus-x11",
    "ibus-portal",
    "fcitx5",
    "xdg-document-portal",
    "xdg-permission-store",
}
# These persistent utilities maintain software updates, network access and tablet
# input. They are not appropriate focus targets, even when they expose a launcher.
PROTECTED_EXECUTABLES = {
    "/usr/bin/gnome-software",
    # Accessibility speech workers can inherit Firefox's GNOME scope. They are
    # system services, independent of whether that browser/app is allowed.
    "/usr/bin/speech-dispatcher",
    "/usr/lib/speech-dispatcher-modules/sd_espeak-ng",
    "/usr/lib/speech-dispatcher-modules/sd_dummy",
    "/usr/lib64/speech-dispatcher-modules/sd_espeak-ng",
    "/usr/lib64/speech-dispatcher-modules/sd_dummy",
    "/usr/bin/warp-desktop-svc",
    "/usr/lib/warp/warp-taskbar",
    "/usr/lib/warp/lib/crashpad_handler",
    "/usr/lib/pentablet/PenTablet.sh",
    "/usr/lib/pentablet/PenTablet",
}
GNOME_SHELL_SCRIPTS = {
    "/usr/share/gnome-shell/org.gnome.Shell.Notifications",
    "/usr/share/gnome-shell/org.gnome.ScreenSaver",
}


def protected_executable(executable):
    return Path(executable).name in PROTECTED or executable in PROTECTED_EXECUTABLES


BROWSERS = {
    "brave",
    "brave-browser",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "chrome",
    "vivaldi-bin",
    "opera",
    "librewolf",
    "epiphany",
    "firefox",
    "firefox-bin",
}


@dataclass
class App:
    id: str
    name: str
    icon: str
    executable: str
    flatpak: str = ""
    browser: bool = False
    protected: bool = False
    script: str = ""
    visible: bool = True
    runtime: tuple = ()


def discover(home, roots=None):
    roots = (
        roots
        if roots is not None
        else [
            Path("/usr/share/applications"),
            Path("/usr/local/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            Path(home) / ".local/share/flatpak/exports/share/applications",
            Path(home) / ".local/share/applications",
        ]
    )
    found = {}
    for root in roots:
        for path in sorted(root.glob("*.desktop")):
            try:
                cp = configparser.ConfigParser(interpolation=None, strict=False)
                cp.read(path)
                d = cp["Desktop Entry"]
                # A user tombstone masks the system entry with the same desktop ID.
                found.pop(path.name, None)
                if d.get("Type") != "Application" or d.get("Hidden", "false") == "true":
                    continue
                args = shlex.split(d.get("Exec", ""))
                if args and Path(args[0]).name == "env":
                    args = [a for a in args[1:] if "=" not in a]
                binary = args[0] if args else ""
                resolved = shutil.which(binary, path="/usr/local/bin:/usr/bin:/bin") or binary
                executable = os.path.realpath(resolved) if resolved.startswith("/") else ""
                flatpak = d.get("X-Flatpak", "")
                script = (
                    next(
                        (
                            os.path.realpath(a)
                            for a in args[1:]
                            if a.startswith("/") and not a.startswith("/app/")
                        ),
                        "",
                    )
                    if INTERPRETERS.fullmatch(Path(binary).name)
                    else ""
                )
                basename = Path(executable).name
                protected = (
                    protected_executable(executable) or path.name == "org.focusblocker.App.desktop"
                )
                app = App(
                    path.name,
                    d.get("Name", path.stem),
                    d.get("Icon", "application-x-executable"),
                    executable,
                    flatpak,
                    "WebBrowser" in d.get("Categories", "") or basename in BROWSERS,
                    protected,
                    script,
                    d.get("NoDisplay", "false") != "true",
                    runtime_identities(executable) if not flatpak else (),
                )
                if executable or flatpak:
                    found[app.id] = app
            except (OSError, ValueError, configparser.Error, KeyError):
                continue
    return found


@dataclass
class Process:
    pid: int
    ppid: int
    start: str
    exe: str
    args: list
    group: str
    flatpak: str = ""


def read_process(pid, uid):
    root = Path("/proc") / str(pid)
    try:
        if root.stat().st_uid != uid:
            return None
        stat = (root / "stat").read_text()
        fields = stat[stat.rfind(")") + 2 :].split()
        args = (root / "cmdline").read_bytes().decode(errors="replace").strip("\0").split("\0")
        exe = os.readlink(root / "exe").removesuffix(" (deleted)")
        group = (root / "cgroup").read_text().strip().split("::")[-1]
        flatpak = ""
        info = root / "root/.flatpak-info"
        if info.is_file():
            cp = configparser.ConfigParser(interpolation=None)
            cp.read(info)
            flatpak = cp.get("Application", "name", fallback="")
        return Process(int(pid), int(fields[1]), fields[19], exe, args, group, flatpak)
    except (OSError, IndexError, ValueError, configparser.Error):
        return None


def scan(uid):
    return {
        p.pid: p
        for entry in Path("/proc").iterdir()
        if entry.name.isdigit()
        if (p := read_process(entry.name, uid)) is not None
    }


def protected_process(p):
    basename = Path(p.exe).name
    if protected_executable(p.exe) or basename.startswith(("gsd-", "ibus-engine-")):
        return True
    if p.exe in {"/usr/bin/gjs", "/usr/bin/gjs-console"} and (
        len(p.args) == 3 and p.args[1] == "-m" and p.args[2] in GNOME_SHELL_SCRIPTS
    ):
        return True
    if INTERPRETERS.fullmatch(basename) and any(
        script_argument_matches(path, p.args) for path in PROTECTED_EXECUTABLES
    ):
        return True
    # Only our installed launchers/module invocation are exempt, never all Python.
    return (
        basename.startswith("python")
        and len(p.args) >= 3
        and p.args[1] == "-I"
        and p.args[2]
        in {
            "/usr/local/bin/focus-blocker",
            "/usr/local/bin/focus-blocker-native",
            "/usr/local/bin/focus-blocker-notifier",
        }
    )


def firefox_process(p):
    return Path(p.exe).name in {"firefox", "firefox-bin"} and not p.flatpak


def firefox_support_process(p):
    # Fedora's orphan crashhelper can outlive its browser in the GNOME app scope.
    # It is not a browser or native-messaging peer. Limit this exception to the
    # bundled executable paths, never everything inheriting Firefox's scope.
    return not p.flatpak and p.exe in {
        "/usr/lib64/firefox/crashhelper",
        "/usr/lib/firefox/crashhelper",
    }


def firefox_roots(processes):
    return {
        pid
        for pid, p in processes.items()
        if firefox_process(p)
        and not any(a in ("-contentproc", "-utility") for a in p.args)
        and not (p.ppid in processes and firefox_process(processes[p.ppid]))
    }


def firefox_ancestor(pid, processes):
    roots = firefox_roots(processes)
    visited = set()
    while pid in processes and pid not in visited:
        if pid in roots:
            return pid
        visited.add(pid)
        pid = processes[pid].ppid
    return None


def app_matches(app, p, include_scope=True):
    unit = re.sub(r"\\x([0-9a-f]{2})", lambda m: chr(int(m[1], 16)), p.group)
    if app.flatpak:
        if p.flatpak:
            return app.flatpak == p.flatpak
        # Outer sandbox/proxy processes lack /.flatpak-info. Only exact known
        # launch helpers may use the Flatpak app scope, not arbitrary executables.
        return (
            include_scope
            and p.exe
            in {
                "/usr/bin/bwrap",
                "/usr/bin/flatpak",
                "/usr/bin/xdg-dbus-proxy",
            }
            and any(
                part.startswith("app-flatpak-" + app.flatpak + "-") and part.endswith(".scope")
                for part in unit.split("/")
            )
        )
    if p.flatpak:
        return False
    # GNOME applications can run as D-Bus activated user services.
    identity = app.id.removesuffix(".desktop")
    scoped = any(
        component == identity + ".service"
        or component.startswith("app-" + identity + "-")
        or component.startswith("app-gnome-" + identity + "-")
        for component in unit.split("/")
    )
    direct = any(
        executable == p.exe
        and (not script or script_argument_matches(script, p.args))
        and (script or not INTERPRETERS.fullmatch(Path(executable).name))
        for executable, script in ((app.executable, app.script), *app.runtime)
    )
    if Path(app.executable).name == "firefox" and (
        firefox_process(p) or firefox_support_process(p)
    ):
        direct = True
    return direct or (include_scope and scoped)


class Monitor:
    def __init__(self, uid, home):
        self.uid = uid
        self.home = home
        self.apps = discover(home)
        self.processes = {}
        self.signaled = {}
        self.seen = {}

    def refresh(self):
        self.processes = scan(self.uid)
        now = time.monotonic()
        for pid, p in self.processes.items():
            self.seen.setdefault((pid, p.start), now)
        live = {(p.pid, p.start) for p in self.processes.values()}
        self.seen = {k: v for k, v in self.seen.items() if k in live}
        self.signaled = {k: v for k, v in self.signaled.items() if k in live}

    def inventory(self):
        self.apps = discover(self.home)
        return [asdict(a) for a in sorted(self.apps.values(), key=lambda a: a.name.casefold())]

    def validate(self, config):
        missing = set(config["apps"]) - self.apps.keys()
        if missing:
            raise Invalid("These apps are no longer installed: " + ", ".join(sorted(missing)))
        if any(self.apps[a].protected for a in config["apps"]):
            raise Invalid("Essential system components cannot be selected for blocking.")

    def firefox_allowed(self, config):
        selected = any(
            a in config["apps"] and Path(app.executable).name == "firefox"
            for a, app in self.apps.items()
        )
        return not selected if config["mode"] == "blacklist" else selected

    def targets(self, config, unhealthy_firefox=None):
        targets = {}
        websites = websites_enabled(config)
        unhealthy_firefox = unhealthy_firefox or set()
        identities = {}
        directly_selected = set()
        direct_identities = {}
        for pid, p in self.processes.items():
            # A known independent executable takes precedence over an inherited
            # GNOME scope. Otherwise a terminal could implicitly allow other apps.
            direct = [a for a in self.apps.values() if app_matches(a, p, include_scope=False)]
            direct_identities[pid] = direct
            if any(a.id in config["apps"] for a in direct):
                directly_selected.add(pid)
            identities[pid] = direct or [a for a in self.apps.values() if app_matches(a, p)]
        protected_pids = {
            pid
            for pid, p in self.processes.items()
            if protected_process(p) or any(a.protected for a in direct_identities[pid])
        }
        # Image decoders/sandbox helpers of the shell or Bonker stay with their
        # owner. Do not exempt all bwrap, gjs or a shared GNOME/browser scope.
        changed = True
        while changed:
            changed = False
            for pid, p in self.processes.items():
                if (
                    pid not in protected_pids
                    and p.ppid in protected_pids
                    and p.group == self.processes[p.ppid].group
                    and not direct_identities[pid]
                    and not p.flatpak
                    and Path(p.exe).name not in BROWSERS
                ):
                    protected_pids.add(pid)
                    changed = True
        selected_pids = {
            pid
            for pid, matches in identities.items()
            if any(a.id in config["apps"] for a in matches)
        }
        if config["mode"] == "whitelist":
            # Helpers of an allowed app inherit its permission. Known independent
            # applications still need their own entry, even when launched by one.
            changed = True
            while changed:
                changed = False
                for pid, p in self.processes.items():
                    if pid not in selected_pids and p.ppid in selected_pids and not identities[pid]:
                        selected_pids.add(pid)
                        changed = True
        for pid, p in self.processes.items():
            if pid in protected_pids:
                continue
            matches = identities[pid]
            selected = pid in selected_pids
            browser = Path(p.exe).name in BROWSERS or any(a.browser for a in matches)
            unsupported = browser and not (firefox_process(p) or firefox_support_process(p))
            # app.slice also contains session, account and D-Bus services.
            # Only an actual app-*.scope supplies unknown-application evidence.
            app_scope = any(
                c.startswith("app-") and c.endswith(".scope") for c in p.group.split("/")
            )
            if config["mode"] == "blacklist":
                blocked = selected
            else:
                blocked = (
                    bool(any(a.visible for a in matches) or p.flatpak or app_scope) and not selected
                )
            blocked |= websites and unsupported
            blocked |= (
                websites
                and firefox_process(p)
                and firefox_ancestor(pid, self.processes) in unhealthy_firefox
            )
            if blocked:
                targets[pid] = {
                    "process": p,
                    "name": matches[0].name if matches else Path(p.exe).name,
                }
        # Include descendants/helpers of identified blocked applications.
        changed = True
        while changed:
            changed = False
            for pid, p in self.processes.items():
                # Only an independently identified allowed app escapes a blocked
                # parent's sweep. Helpers can inherit a misleading allowed scope.
                allowed = config["mode"] == "whitelist" and pid in directly_selected
                if (
                    pid not in targets
                    and p.ppid in targets
                    and not allowed
                    and pid not in protected_pids
                ):
                    targets[pid] = {"process": p, "name": targets[p.ppid]["name"]}
                    changed = True
        return targets

    def signal(self, targets, force=False):
        now = time.monotonic()
        for pid, row in targets.items():
            p = row["process"]
            key = (pid, p.start)
            fresh = read_process(pid, self.uid)
            if not fresh or fresh.start != p.start or protected_process(fresh):
                continue
            sig = signal.SIGKILL if force else signal.SIGTERM
            # pidfds avoid signaling a reused PID after the identity check.
            try:
                fd = os.pidfd_open(pid)
                try:
                    check = read_process(pid, self.uid)
                    if check and check.start == p.start:
                        signal.pidfd_send_signal(fd, sig)
                        self.signaled.setdefault(key, now)
                finally:
                    os.close(fd)
            except ProcessLookupError:
                pass

    def enforce(self, config, unhealthy_firefox):
        targets = self.targets(config, unhealthy_firefox)
        force = {
            pid: r
            for pid, r in targets.items()
            if time.monotonic() - self.signaled.get((pid, r["process"].start), time.monotonic())
            >= 3
        }
        self.signal(targets)
        self.signal(force, force=True)
        return sorted(set(r["name"] for r in targets.values()))
