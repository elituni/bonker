# Development

Use Python 3.12+ and Node 18+. Install the desktop dependencies from
[installation](INSTALLATION.md) for GTK rendering. Distro PyGObject/cairo must be
visible to the development environment:

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install idna ruff
```

Logic and static checks:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check focus_blocker scripts tests
.venv/bin/python -m ruff format --check focus_blocker scripts tests
.venv/bin/python -m compileall -q focus_blocker scripts tests
node tests/test_rules.js
node tests/test_extension.js
node tests/test_extension_failures.js
node tests/test_extension_breaks.js
node tests/test_presentation.js
node tests/test_extension_whitelist.js
```

On a graphical desktop, inert render/handler checks can run against fake service
state. Repeat at `GDK_SCALE=2` for 200% scaling:

```sh
GDK_BACKEND=x11 GDK_SCALE=1 .venv/bin/python tests/confirmation_smoke.py --capture-dir /tmp/bonker-confirmation
GDK_BACKEND=x11 GDK_SCALE=1 .venv/bin/python tests/gtk_smoke.py --render-only --theme dark --font-dpi 96 --capture-dir /tmp/bonker-ui
GDK_SCALE=1 .venv/bin/python tests/companion_smoke.py --capture-dir /tmp/bonker-animation
```

These checks render real GTK widgets and invoke in-process handlers. They do not
inject system input, contact the root service or start enforcement. Render-only
checks do not certify full keyboard/screen-reader navigation or physical dragging.
Opt-in `fedora_live_*` scripts require the isolated environment and recovery
arrangements in [the acceptance checklist](FEDORA_TESTING.md).

Build deliverables with:

```sh
.venv/bin/python scripts/build_extension.py
.venv/bin/python scripts/build_bundle.py
```

Outputs go to ignored `dist/`. The source bundle excludes local evidence,
handoffs, credentials, profiles and installed data. The unsigned extension ZIP
is a signing submission, not an installable signed release. See installation
instructions for signing.

The root service owns timer, password and release decisions. Preserve IPC peer
checks, stop-reason persistence, authorization revocation, preflight termination
confirmations, browser acknowledgement and guarded installation/recovery.

The extension builder uses an explicit `RELEASE_FILES` inventory in
`scripts/build_extension.py`. Add new release assets there deliberately. Extra
local files are excluded; symlinked required files/directories and missing assets
fail validation before an existing output is replaced.
