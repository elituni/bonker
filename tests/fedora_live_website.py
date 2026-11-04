"""Opt-in real one-minute exact-example.com test; requires isolated managed Firefox.

Start Firefox with --headless --no-remote --marionette and a disposable profile.
User must authorize website enforcement and closing the preflight's Firefox helpers.
Never selects Codex, terminals or any app for blocking. No signing bypass is used.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker.client import call  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-authorized-website-test", action="store_true", required=True)
    parser.parse_args()
    from marionette_driver.marionette import Marionette

    config = {
        "minutes": 1,
        "mode": "blacklist",
        "rules": [{"host": "example.com", "action": "block", "subdomains": False}],
        "apps": [],
        "preset_name": None,
    }
    browser = Marionette(host="localhost", port=2828)
    browser.start_session()
    browser.timeout.page_load = 15
    browser.navigate("https://example.com/")
    assert browser.get_url().startswith("https://example.com/")
    assert "Example Domain" in browser.title
    print("BASELINE", browser.get_url(), browser.title, flush=True)
    preview = call("preview", config=config)
    print("PREVIEW", json.dumps(preview), flush=True)
    assert preview["config"] == config and preview["websites"]
    assert set(preview["closing"]) <= {"Firefox"}, "Unapproved application in preflight"
    if preview["closing"]:
        # The host review identified only Firefox's orphan crashhelper here.
        call("close_apps", config=config)
        time.sleep(1)
    assert not call("preview", config=config)["closing"]
    status = call("status")
    assert not status["active"] and not status["starting"] and status["firefox_connected"]
    started = time.monotonic()
    call("prepare", config=config)
    while not call("status")["active"]:
        assert time.monotonic() - started < 20, "Browser acknowledgement timed out"
        time.sleep(0.2)
    status = call("status")
    assert status["active"]["config"] == config
    print("ACTIVE", json.dumps(status), flush=True)
    # The page already open before activation must be swept too.
    assert "moz-extension://" in browser.get_url() and "/blocked.html" in browser.get_url()
    print("EXISTING_TAB_BLOCKED", browser.get_url(), flush=True)
    browser.navigate("https://example.org/")
    assert (
        browser.get_url().startswith("https://example.org/") and "Example Domain" in browser.title
    )
    print("UNRELATED_HOST_ALLOWED", browser.get_url(), flush=True)
    browser.navigate("https://example.com/")
    assert "moz-extension://" in browser.get_url() and "/blocked.html" in browser.get_url()
    print("NEW_NAVIGATION_BLOCKED", browser.get_url(), flush=True)
    from marionette_driver.errors import UnsupportedOperationException

    try:
        Path("docs/screenshots/fedora-2026-11-02/website-blocked.png").write_bytes(
            browser.screenshot(format="binary")
        )
    except UnsupportedOperationException as exc:
        print("Screenshot unavailable for privileged extension page:", str(exc), flush=True)
    while True:
        status = call("status")
        if not status["active"] and not status["starting"] and not status["release_pending"]:
            break
        assert time.monotonic() - started < 85, "Expiry/release acknowledgement timed out"
        time.sleep(0.5)
    browser.navigate("https://example.com/")
    assert (
        browser.get_url().startswith("https://example.com/") and "Example Domain" in browser.title
    )
    print(
        "PASS: existing tab and new navigation blocked, unrelated host allowed, expiry released",
        round(time.monotonic() - started, 3),
        json.dumps(status),
        flush=True,
    )
    browser.delete_session()


if __name__ == "__main__":
    main()
