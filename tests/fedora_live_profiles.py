"""Authorized real one-minute website test across isolated Firefox profiles/private windows.

Uses signed enterprise policy, never temporary loading. Refuses any preflight
closures; does not select applications, Codex or terminals for blocking.
"""

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker.client import call  # noqa: E402


def wait_until(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.25)
    raise AssertionError("Timed out waiting for the stated browser/service condition")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-authorized-profile-test", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from marionette_driver.marionette import Marionette

    args.output.mkdir(parents=True, exist_ok=True)
    config = {
        "minutes": 1,
        "mode": "blacklist",
        "apps": [],
        "preset_name": None,
        "rules": [{"host": "example.com", "action": "block", "subdomains": False}],
    }
    initial = call("status")
    assert not initial["active"] and not initial["starting"] and initial["password_set"]
    browsers, processes, logs = [], [], []
    began = False
    try:
        for number in range(2):
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            profile = Path(tempfile.mkdtemp(prefix=f"focus-profile-{number}-"))
            (profile / "user.js").write_text(f'user_pref("marionette.port", {port});\n')
            log = (args.output / f"firefox-profile-{number}.log").open("w")
            logs.append(log)
            process = subprocess.Popen(
                [
                    "/usr/bin/firefox",
                    "--headless",
                    "--no-remote",
                    "--marionette",
                    "--profile",
                    str(profile),
                    "about:blank",
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            processes.append(process)
            browser = Marionette(host="localhost", port=port, socket_timeout=20)
            browser.raise_for_port(timeout=30)
            browser.start_session()
            browsers.append(browser)
            assert Path(browser.profile).resolve() == profile.resolve(), (
                "Automation endpoint is not the isolated profile created by this test"
            )
            browser.timeout.page_load = 15
            browser.navigate("https://example.com/")
            assert "Example Domain" in browser.title
            print("ISOLATED_PROFILE", number, process.pid, profile, flush=True)
        first = browsers[0]
        regular_handle = first.current_window_handle
        private_handle = first.open(type="window", private=True)["handle"]
        first.switch_to_window(private_handle)
        first.navigate("https://example.com/")
        assert "Example Domain" in first.title
        print("PRIVATE_WINDOW_OPENED", private_handle, flush=True)
        wait_until(lambda: call("status")["browser_count"] >= initial["browser_count"] + 2)
        preview = call("preview", config=config)
        print("PREVIEW", json.dumps(preview), flush=True)
        assert preview["config"] == config and preview["websites"] and not preview["closing"]
        started = time.monotonic()
        call("prepare", config=config)
        began = True
        status = wait_until(lambda: s if (s := call("status"))["active"] else None)
        assert status["active"]["config"] == config
        print("ACTIVE_ALL_PROFILES_ACKNOWLEDGED", json.dumps(status), flush=True)
        for browser, handle, label in (
            (first, regular_handle, "profile-0-normal"),
            (first, private_handle, "profile-0-private"),
            (browsers[1], browsers[1].current_window_handle, "profile-1-normal"),
        ):
            browser.switch_to_window(handle)
            assert (
                browser.get_url().startswith("moz-extension://")
                and "/blocked.html" in browser.get_url()
            )
            print("EXISTING_TAB_BLOCKED", label, flush=True)
            browser.navigate("https://example.org/")
            assert (
                browser.get_url().startswith("https://example.org/")
                and "Example Domain" in browser.title
            )
            browser.navigate("https://example.com/")
            assert (
                browser.get_url().startswith("moz-extension://")
                and "/blocked.html" in browser.get_url()
            )
            print("ALLOWED_HOST_AND_NEW_BLOCKED_NAVIGATION", label, flush=True)
        status = wait_until(
            lambda: (
                s
                if not (s := call("status"))["active"]
                and not s["starting"]
                and not s["release_pending"]
                else None
            ),
            85,
        )
        for browser in browsers:
            browser.navigate("https://example.com/")
            assert (
                browser.get_url().startswith("https://example.com/")
                and "Example Domain" in browser.title
            )
        print(
            "PASS: multi-profile and private-window enforcement and expiry",
            round(time.monotonic() - started, 3),
            json.dumps(status),
            flush=True,
        )
    finally:
        # Leave the root timer authoritative even when an assertion fails. Keep
        # test browsers alive through the original deadline/release if possible.
        if began:
            wait_until(lambda: not (s := call("status"))["active"] and not s["starting"], 85)
        for browser in browsers:
            try:
                browser.delete_session()
            except Exception as exc:
                print("Browser cleanup:", type(exc).__name__, flush=True)
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()


if __name__ == "__main__":
    main()
