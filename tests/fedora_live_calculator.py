"""Explicitly authorized, one-minute Calculator-only real-service acceptance test.

This starts real enforcement. Never invoke without the user's permission to close
Calculator and the agreed account/recovery arrangements. Does not test websites.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker.client import call  # noqa: E402

CONFIG = {
    "minutes": 1,
    "mode": "blacklist",
    "rules": [],
    "apps": ["org.gnome.Calculator.desktop"],
    "preset_name": None,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-authorized-calculator-test", action="store_true", required=True)
    parser.parse_args()
    status = call("status")
    assert status["active"] is None and not status["starting"]
    assert status["password_set"], "User must set the initial password in Settings"
    preview = call("preview", config=CONFIG)
    assert preview["config"] == CONFIG and preview["websites"] is False
    assert not preview["closing"], "Save/close Calculator normally before this test"
    print("PREVIEW", json.dumps(preview), flush=True)
    started = time.monotonic()
    call("prepare", config=CONFIG)
    while True:
        status = call("status")
        if status["active"]:
            assert status["active"]["config"] == CONFIG
            print("ACTIVE", json.dumps(status), flush=True)
            break
        assert time.monotonic() - started < 10, "Activation timed out"
        time.sleep(0.1)
    launched = time.monotonic()
    calculator = subprocess.Popen(["/usr/bin/gnome-calculator"])
    print("CALCULATOR_LAUNCHED", calculator.pid, flush=True)
    try:
        result = calculator.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # Do not imitate enforcement by killing the application from this test.
        print("FAIL: Calculator survived 10 seconds; awaiting original session expiry", flush=True)
        result = None
    elapsed = time.monotonic() - launched
    print("CALCULATOR_EXIT", result, "seconds_after_launch", round(elapsed, 3), flush=True)
    while True:
        status = call("status")
        if status["active"] is None and not status["starting"]:
            print(
                "EXPIRED",
                json.dumps(status),
                "elapsed",
                round(time.monotonic() - started, 3),
                flush=True,
            )
            break
        assert time.monotonic() - started < 75, (
            "Session exceeded one minute plus observation margin"
        )
        time.sleep(0.5)
    assert result in (-15, -9), f"Expected real service termination, got {result}"
    assert not status["release_pending"] and not status["protection_error"]
    after = subprocess.Popen(["/usr/bin/gnome-calculator"])
    time.sleep(3)
    assert after.poll() is None, "Calculator did not remain open after expiry"
    print("POST_EXPIRY_CALCULATOR_RUNNING", after.pid, flush=True)
    # This is the disposable Calculator instance created by this test after expiry.
    after.terminate()
    after.wait(timeout=5)
    print("PASS: real app-only termination, one-minute expiry and post-expiry launch", flush=True)


if __name__ == "__main__":
    main()
