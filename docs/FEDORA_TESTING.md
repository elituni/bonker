# Fedora 44 acceptance checks

These are outstanding target-platform checks, not claims of tests already run. Use a disposable Fedora 44 GNOME VM or a test account with saved work. Keep an administrator terminal available outside the configured account during initial validation.

## Installation and interface

1. Install the native Firefox package, distro Python/GObject dependencies, and a Mozilla-signed XPI using the installer.
2. Check the service is active, there are no SELinux denials, and only the configured user can read/control sessions through the socket.
3. Check `about:policies` for errors. The installed extension must be admin-managed, impossible to disable/remove through ordinary controls, and enabled in private windows.
4. Open the GTK interface. Check every screen at 100% and 200% display scaling, keyboard navigation, dark mode, text wrapping and the accessible emergency-stop control.
5. Check adaptive sidebar collapse, sidebar/stack selection, compact long-domain rows, focus details, transparent icons, and duration round trips (1, 60, 359, 360 minutes). Run `tests/gtk_smoke.py` under a display (or Xvfb) to catch basic GObject/API construction errors before manual UI inspection.
6. Confirm a second Linux account's Firefox remains unrestricted and receives no configured-user session information.

## Session and password behavior

- First password setup; incorrect/current password changes; confirmation mismatch; no changes while active.
- One-minute test session expires normally. The service accepts 360 minutes and rejects 361 without starting a six-hour test unnecessarily.
- Stop and edit before 180 seconds restores the exact draft; the same action at/after the boundary fails.
- Opening and closing the desktop app does not affect the session.
- Correct password alone never releases restrictions. Back to session clears the authorization. Whitespace-only reasons fail. A submitted reason is recorded before release.
- Let expiry occur while entering a password/reason. It must end normally without a false early-stop record.
- Verify log export, no edit/delete controls, and persistence after restart and preset deletion.
- Verify load/edit/start does not modify a preset; explicit update, rename, duplicate and delete work.

## Firefox and application enforcement

- Blacklist `youtube.com` including subdomains with `music.youtube.com` allowed. Test direct navigation, redirects, already-open tabs, back/forward cache, existing embedded video, login, playback and private windows.
- Test exact-host rules, nested exceptions, internationalized hostnames and misleading suffixes.
- Test whitelist mode with allowed Firefox and allowed sites; test an intentionally empty app/site whitelist.
- With two open Firefox profiles, both must acknowledge before the session activates. Open a new profile during a session and verify startup protection.
- Simulate a missing native host, extension failure and browser crash. Confirm detection, visible protection failure, and Firefox restriction without resetting the timer.
- Confirm Brave is closed/blocked during website sessions. Check common alternate browsers listed in the application picker.
- Test a native desktop app and a Flatpak app, their helpers, a later launch, GNOME D-Bus activation, and an unlisted application launched through GNOME. Verify essential GNOME/input/accessibility services remain available.
- Verify preflight asks you to save/close normally and Recheck. Confirm no signal is sent until the separate Terminate apps confirmation. Test the later explicit force-close/cancel decision using a disposable document.
- Measure the actual detection delay and record gaps; do not describe process termination as launch prevention.

## Recovery and uninstall

- Suspend/resume; restart the service; reboot before and after the deadline. The correction window must never renew.
- Verify early-stop release confirmation reconciles after a service crash.
- Stop an app-only session with an unintegrated Firefox profile open; the next session must remain available. Open another profile during a website-session release and verify it cannot stall the previous session's audit completion.
- Simulate a failed reason write: remain active, show a useful error, and still expire normally.
- With the service unavailable after an active session, verify cached browser restrictions expire instead of persisting indefinitely.
- Verify uninstall refuses an active session, preserves unrelated browser policy changes, retains the log, and allows a clean reinstall for the same user.
- Race a new Start action against uninstall: one operation must be refused; an active session must never be silently removed. Check failed service-stop recovery clears maintenance when the service is still reachable.
- Test failed activation followed by `--repair`, offline removal of a never-started idle installation, refusal with an active/unverifiable saved session, and repeated removal after an interrupted file/policy step. Confirm service startup and removal cannot hold the lifetime lock together. Test SELinux labeling; do not disable SELinux to mask failures.
- Confirm completion notifications wait for Firefox release, including correction-window cancellation.
- With many log entries, Load older and full export must work; a failed export must leave an existing destination intact.
- Rapidly leave/reopen password entry while verification is pending; late replies must not revive authorization.

Record platform versions, commands, outcomes and remaining gaps in a local validation report.
