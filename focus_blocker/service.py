"""Root service. A peer-credential checked Unix socket is the only control API."""

import argparse
import json
import logging
import os
import pwd
import socket
import socketserver
import struct
import threading
import time
from pathlib import Path

from . import EXTENSION_ID, SOCKET
from .apps import Monitor, firefox_ancestor, firefox_roots
from .engine import Engine
from .lifecycle import exclusive_lock
from .rules import Invalid, configuration, evaluate, websites_enabled
from .storage import Store

LOG = logging.getLogger("focus-blocker")


def policy_ready(path=Path("/etc/firefox/policies/policies.json")):
    try:
        st = path.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        policies = json.loads(path.read_text())["policies"]
        settings = policies["ExtensionSettings"][EXTENSION_ID]
        return (
            settings["installation_mode"] == "force_installed"
            and settings.get("private_browsing") is True
            and policies.get("3rdparty", {})
            .get("Extensions", {})
            .get(EXTENSION_ID, {})
            .get("localBridgeConsent")
            is True
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Controller:
    def __init__(self, engine, monitor):
        self.engine = engine
        self.monitor = monitor
        self.lock = threading.RLock()
        self.browsers = {}
        self.protection_error = None
        self.running = True
        self.maintenance = False

    def browser_state(self):
        e = self.engine
        if e.pending:
            return {
                "session_id": e.pending["id"],
                "revision": e.pending["id"],
                "config": {key: e.pending["config"][key] for key in ("mode", "rules")},
                "phase": "starting",
                "remaining": 20,
            }
        if e.active:
            return {
                "session_id": e.active["id"],
                "revision": e.active["id"],
                "config": {key: e.active["config"][key] for key in ("mode", "rules")},
                "phase": "active",
                "remaining": e.remaining(),
            }
        return {
            "session_id": None,
            "revision": "idle",
            "config": None,
            "phase": "idle",
            "remaining": 0,
        }

    def healthy(self, pid, revision=None, require_protection=True):
        row = self.browsers.get(pid)
        return bool(
            row
            and time.monotonic() - row["at"] < 5
            and (row["private"] or not require_protection)
            and (revision is None or row["revision"] == revision)
        )

    def tick(self):
        with self.lock:
            e = self.engine
            e.tick()
            self.monitor.refresh()
            roots = firefox_roots(self.monitor.processes)
            self.browsers = {
                p: row
                for p, row in self.browsers.items()
                if p in roots and self.monitor.processes[p].start == row["start"]
            }
            config = (
                e.pending["config"] if e.pending else (e.active["config"] if e.active else None)
            )
            if config:
                needs_browser = websites_enabled(config) and self.monitor.firefox_allowed(config)
                desired = self.browser_state()["revision"]
                unhealthy = {
                    p
                    for p in roots
                    if not self.healthy(p, desired)
                    and time.monotonic() - self.monitor.seen[(p, self.monitor.processes[p].start)]
                    > 8
                }
                # During startup allow the extension time to install the pending revision.
                self.monitor.enforce(config, unhealthy if not e.pending else set())
                if e.pending:
                    if not needs_browser or (
                        roots and all(self.healthy(p, desired) for p in roots)
                    ):
                        e.activate()
                self.protection_error = (
                    "Firefox protection was lost; affected Firefox processes are being closed."
                    if needs_browser and unhealthy
                    else None
                )
            else:
                self.protection_error = None
                release = e.store.get("release_pending")
                targets = release.get("targets", []) if release else []
                if release and release.get("boot") != e.clock.boot():
                    targets = []
                # Only browsers present when this website session stopped owe an
                # acknowledgement. Unrelated/new profiles cannot stall the next session.
                waiting = [
                    target["pid"]
                    for target in targets
                    if target["pid"] in roots
                    and self.monitor.processes[target["pid"]].start == target["start"]
                ]
                if all(self.healthy(p, "idle", require_protection=False) for p in waiting):
                    e.release_confirmed()

    def dispatch(self, req, peer_pid, peer_uid=None):
        with self.lock:
            self.engine.tick()
            action = req.get("action")
            e = self.engine
            if action in ("begin_uninstall", "abort_uninstall"):
                if peer_uid != 0:
                    raise Invalid("Only the administrator installer can enter maintenance mode.")
                e.idle()
                if action == "begin_uninstall" and e.store.get("release_pending"):
                    raise Invalid(
                        "Wait for the previous session's release to finish before uninstalling."
                    )
                self.maintenance = action == "begin_uninstall"
                return {}
            if action == "status":
                return {
                    **e.status(),
                    "policy_ready": policy_ready(),
                    "protection_error": self.protection_error,
                    "firefox_connected": any(self.healthy(p) for p in self.browsers),
                    "browser_count": len(self.browsers),
                    "maintenance": self.maintenance,
                    "release_pending": bool(e.store.get("release_pending"))
                    or (
                        not e.active
                        and not e.pending
                        and any(
                            row["revision"] not in (None, "idle") for row in self.browsers.values()
                        )
                    ),
                }
            if action == "browser":
                self.monitor.refresh()
                root = firefox_ancestor(peer_pid, self.monitor.processes)
                if root is None:
                    raise Invalid(
                        "Browser messages must originate from a native host launched by Firefox."
                    )
                instance = req.get("instance")
                if not isinstance(instance, str) or not 1 <= len(instance) <= 100:
                    raise Invalid("Invalid Firefox instance.")
                self.browsers[root] = {
                    "at": time.monotonic(),
                    "revision": req.get("revision"),
                    "private": req.get("private") is True and req.get("managed") is True,
                    "instance": instance,
                    "start": self.monitor.processes[root].start,
                }
                return self.browser_state()
            if action == "apps":
                return self.monitor.inventory()
            if action == "normalize":
                return configuration(req.get("config"))
            if action == "test_address":
                config = configuration(req.get("config"))
                return evaluate(req.get("address"), config["mode"], config["rules"])
            if action in ("preview", "prepare", "close_apps"):
                config = configuration(req.get("config"))
                self.monitor.inventory()
                self.monitor.validate(config)
                self.monitor.refresh()
                targets = self.monitor.targets(config)
                if action == "preview":
                    return {
                        "config": config,
                        "closing": sorted(set(r["name"] for r in targets.values())),
                        "websites": websites_enabled(config),
                        "protected": "GNOME, authentication, accessibility, audio, networking and this app",
                    }
                e.idle()
                if self.maintenance:
                    raise Invalid(
                        "The administrator is uninstalling Bonker. New sessions are disabled."
                    )
                if action == "close_apps":
                    self.monitor.signal(targets, force=req.get("force") is True)
                    return {"requested": len(targets)}
                if targets:
                    raise Invalid(
                        "Close the listed applications before starting (or use the close-applications action)."
                    )
                if websites_enabled(config) and self.monitor.firefox_allowed(config):
                    if not policy_ready():
                        raise Invalid(
                            "Managed Firefox policy is not installed. Complete installation first."
                        )
                    roots = firefox_roots(self.monitor.processes)
                    if not roots or not all(self.healthy(p) for p in roots):
                        raise Invalid(
                            "Open Firefox with the managed extension enabled in every profile, including private browsing."
                        )
                e.prepare(config)
                return e.status()
            if action == "cancel":
                return e.stop_edit()
            if action == "set_password":
                e.set_password(req.get("new"), req.get("current", ""))
            elif action == "authorize":
                return {"token": e.authorize(req.get("password"))}
            elif action == "revoke":
                e.revoke(req.get("token"))
            elif action == "stop":
                targets = []
                if (
                    e.active
                    and websites_enabled(e.active["config"])
                    and self.monitor.firefox_allowed(e.active["config"])
                ):
                    self.monitor.refresh()
                    targets = [
                        {"pid": pid, "start": self.monitor.processes[pid].start}
                        for pid in firefox_roots(self.monitor.processes)
                    ]
                return e.stop_reason(req.get("token"), req.get("reason"), release_targets=targets)
            elif action == "presets":
                return e.store.presets()
            elif action == "save_preset":
                e.save_preset(
                    req.get("name"),
                    req.get("config"),
                    req.get("overwrite") is True,
                    req.get("original"),
                )
            elif action == "delete_preset":
                e.delete_preset(req.get("name"))
            elif action == "audit":
                return e.store.audit_page(
                    req.get("before"), req.get("through"), req.get("limit", 25)
                )
            else:
                raise Invalid("Unknown service operation.")
            return {}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        pid, uid, _ = struct.unpack(
            "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        try:
            line = self.rfile.readline(131073)
            if len(line) > 131072 or not line.endswith(b"\n"):
                raise Invalid("Request is too large.")
            req = json.loads(line)
            if not isinstance(req, dict):
                raise Invalid("Request must be an object.")
            if uid not in (0, self.server.target_uid):
                if req.get("action") != "browser":
                    raise Invalid("This account is not configured for Bonker.")
                # System Firefox policy applies to other accounts too. Their extension
                # must be inert, without disclosing the configured user's session.
                result = {
                    "session_id": None,
                    "revision": "idle",
                    "config": None,
                    "phase": "idle",
                    "remaining": 0,
                }
            else:
                result = self.server.controller.dispatch(req, pid, peer_uid=uid)
            response = {"ok": True, "result": result}
        except (Invalid, ValueError, TypeError) as err:
            response = {"ok": False, "error": str(err)}
        except Exception:
            # Avoid logging request payloads (passwords and private stop reasons).
            LOG.exception("Service operation failed")
            response = {
                "ok": False,
                "error": "Operation failed; session protection remains in effect. Check service logs.",
            }
        try:
            self.wfile.write(json.dumps(response).encode() + b"\n")
        except (OSError, TimeoutError):
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    # Bound handlers so an unprivileged client cannot create unlimited threads.
    slots = threading.BoundedSemaphore(16)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        super().process_request(request, address)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def run_service():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/focus-blocker/config.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
        raise SystemExit("Run the installed service as root on Linux.")
    config = json.loads(Path(args.config).read_text())
    account = pwd.getpwuid(int(config["uid"]))
    if account.pw_uid == 0:
        raise SystemExit("The target account must not be root.")
    os.umask(0o077)
    Path("/var/lib/focus-blocker").mkdir(mode=0o700, exist_ok=True)
    Path("/run/focus-blocker").mkdir(mode=0o755, exist_ok=True)
    os.chmod("/run/focus-blocker", 0o755)
    store = Store("/var/lib/focus-blocker/state.sqlite3")
    controller = Controller(Engine(store), Monitor(account.pw_uid, account.pw_dir))
    try:
        Path(SOCKET).unlink(missing_ok=True)
        with Server(SOCKET, Handler) as server:
            server.controller = controller
            server.target_uid = account.pw_uid
            os.chown(SOCKET, 0, account.pw_gid)
            os.chmod(SOCKET, 0o660)

            def monitor_loop():
                while controller.running:
                    try:
                        controller.tick()
                    except Exception:
                        LOG.exception("Protection monitor failed")
                        with controller.lock:
                            controller.protection_error = (
                                "Application protection encountered an error. See service logs."
                            )
                            controller.engine.tick()
                    time.sleep(0.5)

            thread = threading.Thread(target=monitor_loop, daemon=True)
            thread.start()
            server.serve_forever(poll_interval=0.5)
    finally:
        controller.running = False
        Path(SOCKET).unlink(missing_ok=True)


def main():
    if os.geteuid() != 0 or not hasattr(socket, "SO_PEERCRED"):
        raise SystemExit("Run the installed service as root on Linux.")
    state = Path("/var/lib/focus-blocker")
    state.mkdir(mode=0o700, exist_ok=True)
    with exclusive_lock(state / "service.lock"):
        run_service()


if __name__ == "__main__":
    main()
