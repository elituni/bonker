"""Read-only launcher coverage. Synthetic processes test identity, not enforcement."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker.apps import Monitor, Process, app_matches

m = Monitor(os.getuid(), str(Path.home()))
m.refresh()
for app in m.apps.values():
    if not app.visible or app.protected or app.flatpak:
        continue
    if not app.runtime and not any(t in app.id.lower() for t in ("discord", "firefox", "terminal")):
        continue
    checks = []
    for exe, script in app.runtime:
        args = [exe, script] if script else [exe]
        p = Process(999999, 0, "synthetic", exe, args, "/app.slice/unrelated.scope")
        assert app_matches(app, p, include_scope=False)
        checks.append({"exe": exe, "script": script})
    actual = [p for p in m.processes.values() if app_matches(app, p)]
    targets = m.targets({"mode": "whitelist", "apps": [app.id], "rules": []})
    print(
        json.dumps(
            {
                "app": app.id,
                "name": app.name,
                "runtime_identity_probes": checks,
                "running_matches": len(actual),
                "running_matches_in_close_list": sum(p.pid in targets for p in actual),
            }
        )
    )
