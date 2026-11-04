"""Read-only app/process identity report. Never prepares sessions or sends signals."""

import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker.apps import Monitor, app_matches
from focus_blocker.rules import configuration

m = Monitor(os.getuid(), str(Path.home()))
m.refresh()
apps = list(m.apps.values())
selected = [a.id for a in apps if "chatgpt" in (a.name + " " + a.id).lower()]
config = configuration({"mode": "whitelist", "apps": selected, "rules": [], "minutes": 1})
targets = m.targets(config)
print(json.dumps({"selected": selected, "process_count": len(m.processes)}))
for p in m.processes.values():
    direct = [a.id for a in apps if app_matches(a, p, include_scope=False)]
    scoped = [a.id for a in apps if app_matches(a, p)]
    if any(s in p.exe.lower() for s in ("chatgpt", "codex")) or (scoped and not direct):
        print(
            json.dumps(
                {
                    "pid": p.pid,
                    "ppid": p.ppid,
                    "exe": p.exe,
                    "scope": p.group,
                    "direct": direct,
                    "scoped": scoped,
                    "would_close": targets.get(p.pid, {}).get("name"),
                }
            )
        )
for a in apps:
    if not a.visible or a.flatpak:
        continue
    try:
        with open(a.executable, "rb") as source:
            prefix = source.read(120)
        if prefix.startswith(b"#!"):
            print(
                json.dumps(
                    {
                        "launcher": asdict(a),
                        "interpreter": prefix.splitlines()[0].decode(errors="replace"),
                    }
                )
            )
    except OSError:
        pass
