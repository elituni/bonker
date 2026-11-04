"""Headless Firefox renders of copied HTML with a FAKE status API.

Does not install/load an unsigned extension, modify Firefox policies, prepare a
session, or signal any existing process. Only our isolated browser is stopped.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from marionette_driver.marionette import Marionette

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from focus_blocker.client import call  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    args = parser.parse_args()
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    status = call("status")
    if status.get("active") or status.get("starting") or status.get("release_pending"):
        raise RuntimeError("Service must be idle before launching an isolated rendering browser")
    with tempfile.TemporaryDirectory(prefix="bonker-html-preview-") as temporary:
        root = Path(temporary)
        pages = root / "pages"
        shutil.copytree(ROOT / "firefox", pages)
        for name in ("popup.html", "blocked.html"):
            path = pages / name
            html = path.read_text().replace(
                '<script src="presentation.js">',
                '<script>window.__status={phase:"idle",connected:true};window.browser={runtime:{sendMessage:async()=>window.__status}};</script><script src="presentation.js">',
            )
            path.write_text(html)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        profile = root / "profile"
        profile.mkdir()
        prefs = {
            "ui.systemUsesDarkTheme": int(args.theme == "dark"),
            "marionette.port": port,
            "browser.shell.checkDefaultBrowser": False,
            "browser.startup.homepage": "about:blank",
            "browser.startup.page": 0,
            "browser.aboutwelcome.enabled": False,
            "browser.newtabpage.enabled": False,
            "toolkit.telemetry.reportingpolicy.firstRun": False,
        }
        (profile / "user.js").write_text(
            "\n".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k, v in prefs.items())
        )
        with (args.capture_dir / "firefox-render.log").open("w") as log:
            process = subprocess.Popen(
                [
                    "firefox",
                    "--headless",
                    "--no-remote",
                    "--profile",
                    str(profile),
                    "--marionette",
                    "about:blank",
                ],
                stdout=log,
                stderr=log,
                env={**os.environ, "MOZ_HEADLESS": "1"},
            )
            browser = Marionette(host="localhost", port=port)
            try:
                browser.start_session()
                browser.timeout.page_load = 15
                browser.set_window_rect(width=1050, height=900)

                def load(name):
                    browser.navigate((pages / name).as_uri() + "?host=example.com")
                    time.sleep(0.15)

                def show(data):
                    browser.execute_async_script(
                        "window.__status=arguments[0];window.refresh().then(()=>arguments[arguments.length-1](true));",
                        script_args=[data],
                        sandbox=None,
                    )

                def text(id):
                    return browser.execute_script(
                        "return document.getElementById(arguments[0]).textContent;",
                        script_args=[id],
                    )

                def capture(name):
                    time.sleep(0.15)
                    (args.capture_dir / (name + ".png")).write_bytes(
                        browser.screenshot(format="binary")
                    )

                rules = [
                    {"host": "youtube.com", "action": "block", "subdomains": True},
                    {"host": "music.youtube.com", "action": "allow", "subdomains": True},
                    {
                        "host": "a-very-long-hostname-for-layout-inspection.example.com",
                        "action": "block",
                        "subdomains": False,
                    },
                ]
                active = {
                    "phase": "active",
                    "connected": True,
                    "remaining": 1500,
                    "config": {"mode": "blacklist", "rules": rules},
                }
                for dark in (args.theme == "dark",):
                    suffix = "dark" if dark else "light"
                    load("popup.html")
                    assert (
                        browser.execute_script(
                            'return matchMedia("(prefers-color-scheme: dark)").matches;'
                        )
                        == dark
                    )
                    assert text("state") == "No bonks happening right now"
                    capture("popup-idle-" + suffix)
                    show(active)
                    assert "youtube.com" in text("blocked-rules") and "music.youtube.com" in text(
                        "allowed-rules"
                    )
                    capture("popup-active-" + suffix)
                    show({**active, "config": {"mode": "whitelist", "rules": [rules[1]]}})
                    assert text("default-rule") == "All other websites get bonked."
                    capture("popup-whitelist-" + suffix)
                    show({**active, "config": {"mode": "whitelist", "rules": []}})
                    assert text("default-rule") == "All websites get bonked."
                    load("blocked.html")
                    show(active)
                    assert (
                        browser.execute_script(
                            'return document.querySelector(".bonk-image").naturalWidth;'
                        )
                        == 900
                    )
                    assert (
                        browser.execute_script('return document.querySelector("h1").textContent;')
                        == "You've been bonked!"
                    )
                    assert browser.execute_script(
                        'return document.getElementById("timer") === null;'
                    )
                    assert browser.execute_script(
                        'return document.getElementById("status").hidden;'
                    )
                    capture("blocked-" + suffix)
                    show({"phase": "idle", "connected": True})
                    assert browser.execute_script(
                        'return document.getElementById("timer") === null;'
                    )
                    assert "over" in text("status")
                browser.set_window_rect(width=380, height=800)
                load("popup.html")
                show(active)
                assert browser.execute_script(
                    "return document.documentElement.scrollWidth <= innerWidth;"
                )
                capture("popup-narrow")
                # Host strings stay text, not executable HTML.
                show(
                    {
                        **active,
                        "config": {
                            "mode": "blacklist",
                            "rules": [
                                {
                                    "host": "<img src=x onerror=alert(1)>",
                                    "action": "block",
                                    "subdomains": False,
                                }
                            ],
                        },
                    }
                )
                assert (
                    browser.execute_script(
                        'return document.querySelectorAll("#blocked-rules img").length;'
                    )
                    == 0
                )
                browser.execute_async_script(
                    'browser.runtime.sendMessage=async()=>{throw new Error("fake disconnect")};window.refresh().then(()=>arguments[0](true));',
                    sandbox=None,
                )
                assert browser.execute_script(
                    'return document.getElementById("rules-section").hidden;'
                )
                print(
                    "PASS: actual Firefox HTML rendering, light/dark, idle/blacklist/whitelist/expiry, bundled image, narrow layout, text escaping and stale-list clearing. FAKE API; not extension enforcement/signing validation."
                )
            finally:
                try:
                    browser.delete_session()
                except Exception:
                    pass
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
