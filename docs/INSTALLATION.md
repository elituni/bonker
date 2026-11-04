# Fedora installation

Target: Fedora 44 GNOME, Python 3.12+, native Firefox 140+, GTK4/libadwaita
(libadwaita 1.5+), GTK3 and XWayland. Internal package and service names remain
`focus_blocker` and `focus-blocker`.

## Dependencies and Firefox signing

From the repository directory, install distro dependencies:

```sh
sudo dnf install python3 python3-gobject python3-idna python3-cairo gtk3 gtk4 libadwaita xorg-x11-server-Xwayland firefox
```

Build the unsigned extension submission with `python3 scripts/build_extension.py`.
Submit `dist/focus-blocker-firefox-unsigned.zip` through the
[Mozilla Developer Hub](https://addons.mozilla.org/developers/) as **unlisted /
self-distributed**. See [reviewer notes](MOZILLA_REVIEW_NOTES.md). Keep AMO
credentials and the returned signed XPI outside Git. The XPI must have the same
version and add-on ID as `firefox/manifest.json`.

The unsigned ZIP and temporary `about:debugging` loading do not satisfy production
installation. Firefox verifies the actual signature. Editing extension source
requires a new extension version and a newly signed package.

## Preview and install

Preview files without changing the host:

```sh
python3 scripts/install.py --stage dist/install-preview --user "$USER" --signed-xpi /absolute/path/to/signed-extension.xpi
```

A preview without `--signed-xpi` deliberately contains no XPI and is not deployable.
Use a fresh staging directory, or remove the old staged image with
`python3 scripts/install.py --stage dist/install-preview --uninstall`.

Install with the intended ordinary desktop account:

```sh
sudo python3 scripts/install.py --user "$USER" --signed-xpi /absolute/path/to/signed-extension.xpi
```

The installer writes root-owned code under `/usr/local`, enables
`focus-blocker.service`, registers the native host, and merges Firefox policy.
It refuses existing installations and conflicting installation-specific Firefox
policies, and preserves unrelated policies. Do not bypass those checks.

Restart Firefox. Check `about:policies` for errors, verify the managed extension
is active and allowed in private windows, then open Bonker and set the initial
password. The managed policy also disables Firefox's Troubleshoot Mode UI and
profile refresh. These settings apply to the machine's native Firefox.

Sign out/in to start the notification helper, or run `focus-blocker-notifier` as
the configured ordinary user. Enforcement belongs to the service, independently
of the main window and notifier.

## Upgrade and retained data

Save any draft and wait until the service is idle, including completion of pending
browser release. Stage the new source and matching signed XPI first. Using the
new source directory, run:

```sh
sudo python3 scripts/install.py --uninstall
sudo python3 scripts/install.py --user "$USER" --signed-xpi /absolute/path/to/signed-extension.xpi
```

Normal removal uses idle/maintenance guards. It retains
`/var/lib/focus-blocker/state.sqlite3`, the account identity, password, presets
and permanent audit log. Reinstall for the same account. Never delete the database
to bypass an error. Reopen the main UI and restart only the notifier after saving
any draft. Desktop-only changes can reuse an unchanged matching signed XPI.

## Diagnostics and recovery

```sh
focus-blocker-cli status
systemctl status focus-blocker.service
journalctl -u focus-blocker.service -b
```

If activation failed after files were installed, resolve the journal/dependency
error and retry with `sudo python3 scripts/install.py --repair`. Repair validates
existing files and retries activation; it is not an upgrade or missing-file
replacement. Repeat `--uninstall` after interrupted removal, then reinstall.
An unreachable service does not prove that saved state is idle. Keep SELinux
enforcing and diagnose errors instead of disabling it.

For removal, wait for expiry or stop through the existing authorized flow, then
run `sudo python3 scripts/install.py --uninstall` and restart Firefox. Removal
restores only owned policy values that still match its receipt, preserving
unrelated changes and retained state.
