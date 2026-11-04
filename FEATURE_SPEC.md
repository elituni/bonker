# Fedora Focus Blocker — Proposed Feature Specification

Status: Defaults and managed Firefox integration approved. Target: Fedora 44 with GNOME and preinstalled native Firefox. Initial implementation is in progress; see README.md and docs/HANDOFF.md for validation status. Brave integration is deferred.

This document formalizes the requested first version. The user accepted the proposed defaults on November 1, 2026 and subsequently selected administrator-managed Firefox integration. Existing “Proposed” labels identify the origin of those defaults; browser-specific implementation details still require validation.

## 1. Purpose and scope

Provide a local Fedora desktop application that restricts access to selected applications and websites for a fixed duration. Support whitelist and blacklist sessions, reusable presets, a three-minute correction window, and a password-protected early-stop flow with a permanent reason log.

Proposed scope: one configured Linux user account on one computer. Restrictions should resist ordinary attempts to quit the interface or restart the computer; they are not a guarantee against an administrator deliberately modifying the system. No cloud account or synchronization is required.

Scheduling, breaks, statistics, categories, goals, and streaks are deferred and must not be implemented in the first version.

November 2 amendment: the user authorized preparatory Firefox-side timed-break
support before signing, while keeping desktop break controls/allowances deferred.
See `docs/FIREFOX_PROTOCOL.md`. The user also selected a TickTick-style sidebar and
compact lists with a calmer ScreenZen-inspired focus screen. That visual pass is
implemented in source; `docs/UI_DIRECTION.md` records the current layout and the
outstanding Fedora rendering checks. The statistics follow-up is proposal-only in
`docs/FOCUS_STATISTICS_PROPOSAL.md` and requires feedback before implementation.

## 2. App organization

Use four primary destinations:

| Destination | Purpose |
| --- | --- |
| Session | Configure a session when idle; display the active session when running. |
| Presets | Create, inspect, edit, duplicate, load, and delete saved configurations. |
| Stop log | Read timestamped records of password-authorized early stops. |
| Settings | Configure the stop password and inspect blocking-service/browser readiness. |

First-run setup explains blocking coverage, configures the password, and checks required integrations. The password must be configured before starting a session so that the intended emergency-stop flow is available.

The Session destination contains the start form, the running-session view, and a secondary emergency-stop screen. The emergency-stop screen is reached through a small, visually subdued control on the running-session view. It remains labeled, keyboard accessible, and usable with assistive technology.

## 3. Session configuration

The start form contains:

- Optional preset selector, with a blank/custom configuration option.
- Duration input in hours and minutes.
- Whitelist or blacklist mode selector.
- Websites editor with primary rules and exceptions.
- Applications picker with search and selected-app list.
- A summary of effective restrictions, integration readiness, and applications that will be closed.
- A Start session action.

Duration requirements:

- Five minutes is the intended minimum, but no five-minute validation limit is required.
- Proposed: require a positive duration, in whole minutes. Thus one to four minutes remain valid.
- Six hours is an absolute maximum: accept 6:00 and reject anything longer in both the interface and authoritative session service.
- Proposed default duration: 25 minutes.

Only one session may run at a time. Starting a session creates an independent snapshot of its duration, mode, website rules, application rules, and optional source preset identity. Later preset changes cannot modify this snapshot.

Before starting, validate rules and integration readiness. Show which running applications must close and allow the user to return to the form to save work. After confirmation, activate restrictions and start the timer only when enforcement is ready. If activation fails, unwind partial changes and return to setup with a specific error; never display a successful session while silently missing required protection.

## 4. Timer and lifecycle

A session has these states: Draft → Starting → Active with correction window → Active and locked → Ended. It may end naturally, through correction-window cancellation, or through an authenticated early stop. Starting can also fail without creating an active session.

While active, display remaining time prominently, expected end time, mode, and a read-only summary of the rules. The timer keeps running on every screen, including password and reason entry.

Proposed lifecycle behavior:

- Closing the window does not stop blocking. Reopening returns to the current session.
- Sleep and time spent powered off count toward the session duration; this is elapsed time, not active computer-use time.
- An unexpired session resumes enforcement after reboot without requiring the interface to open. An already-expired session does not resume blocking.
- Reopening, restarting, or recovering the service never resets the duration or correction window.
- During a boot, elapsed time must not be extended by changing the displayed system clock. Persist sufficient timing information for reboot recovery; deliberate clock manipulation across reboots is outside the administrator-resistance guarantee.
- At expiry, remove only restrictions owned by this session, restore normal access, and show a completion notification. Do not relaunch closed apps or automatically reopen blocked pages.
- Recovery and cleanup must be repeatable without damaging unrelated system configuration. A stale browser rule must not keep blocking past its recorded session expiry.

No pause, duration change, or rule editing is available during an active session. Corrections require stopping and starting a new session.

## 5. Three-minute correction window

For the first 180 seconds after activation, show a prominent Stop and edit button beside a live countdown starting at 3:00.

Activating this control before the window ends immediately stops the session without a password or reason. Restore the session configuration to the start form, allowing edits and a fresh start. Restarting creates a new session and a new correction window.

At exactly 180 seconds, the normal stop control becomes unavailable and the session is locked. The service must check the deadline when handling the stop request; an outdated screen cannot extend the window. A session shorter than three minutes ends naturally before its correction window expires.

Correction-window cancellations do not require or create a reason entry in the permanent stop log.

## 6. Password-protected early stop

After the correction window, the small Emergency stop control opens a separate screen. Opening it does not stop or pause restrictions.

The flow is:

1. Enter the previously configured password.
2. If incorrect, remain blocked and show an error. Proposed: apply a short progressive delay to repeated failed attempts, without a permanent lockout.
3. If correct, show two choices: Continue session and Stop session with a reason.
4. Continuing returns to the running-session view and clears the temporary authorization.
5. Stopping requires a nonblank written reason and a final Stop session and record reason action.
6. Save the reason durably before removing restrictions. Return to the idle Session screen after stopping succeeds.

Typing the correct password alone must never end the session. Back, cancel, or closing the screen leaves the session running and clears temporary authorization. Authorization applies only to this session and this stop flow; it cannot carry over to another session.

If the timer expires during either step, end normally and dismiss the stop flow without creating an early-stop record. At submission, the service must recheck session state to resolve this race consistently.

If saving the reason fails, show the error and offer retry or Continue session; do not claim the session stopped. Natural expiry remains available and must not depend on log writes succeeding.

Store a salted password hash rather than a readable password. If no password is set, the user can set one freely without providing a current password. If a password is already set, changing it requires entering and verifying the current password. Password changes are available only while no session is active. Do not provide an unauthenticated reset that bypasses the current-password requirement. The service must enforce these rules, not just the settings screen.

## 7. Permanent stop log

The Stop log is a chronological, local, read-only record of password-authorized early-stop requests and their outcomes. Each record contains:

- Stable record and session identifiers.
- Timestamp, stored with an unambiguous time reference and displayed in local time.
- Session start and planned end time.
- Actual stop time, or pending/failed outcome if stopping did not complete.
- Planned duration and elapsed duration at the request.
- Mode and optional preset name as captured when starting.
- The exact submitted reason.

Write the reason before attempting to release restrictions, then record the outcome. Recovery must reconcile an interrupted operation so the log does not falsely claim successful release or lose the reason.

“Permanent” means no edit/delete controls, no automatic retention expiry, and persistence across app restarts, system reboots, and preset deletion. Store the log separately from presets. Proposed: support a simple export for backup and reading elsewhere. Local storage cannot promise survival after disk loss, administrator deletion, or a deliberate data wipe.

This is an accountability log, not the deferred focus-statistics feature. Do not collect browsing history, page contents, or passwords in it.

## 8. Blocking modes

One selected mode governs both website and application lists.

| Mode | Websites | Applications |
| --- | --- | --- |
| Blacklist | Allow by default; block matching rules except more-specific allowed exceptions. | Allow by default; block selected apps. |
| Whitelist | Block by default; allow matching rules except more-specific blocked exceptions. | Block user-facing apps by default; allow selected apps and essential system components. |

Website access and application access are separate gates: allowing a website does not allow a blocked browser, and allowing a browser does not exempt it from website rules.

An empty whitelist intentionally grants no ordinary websites/apps beyond required system components. Show this consequence explicitly before starting. Proposed: reject an entirely empty blacklist as having nothing to block.

## 9. Website matching and exceptions

Use hostname-based rules in the first version, not arbitrary URL substring matching.

Each rule has a hostname, an action, and an Include subdomains option, enabled by default. A rule for youtube.com then matches youtube.com, www.youtube.com, and music.youtube.com. It does not match notyoutube.com or youtube.com.example.org. Exact-host rules match only the named host.

Users may paste a hostname or full HTTP(S) URL. Parse and normalize it, then show the resulting hostname before saving. Make explicit that paths, query strings, and fragments are not part of this version's rule. Do not silently turn a pasted page-specific URL into a site-wide block without showing that effect. Normalize case and equivalent internationalized names; reject malformed entries and deduplicate equivalent rules. An exact www.example.com entry is distinct from example.com.

Rule precedence:

1. The most specific matching hostname wins.
2. For the same hostname, an exact-host match takes priority over an include-subdomains rule.
3. Reject contradictory duplicate rules with identical hostname and scope.
4. If no rule matches, use the selected mode's default.

In blacklist mode, the main editor contains Blocked websites and Allowed exceptions. In whitelist mode, it contains Allowed websites and Blocked exceptions. This keeps common configurations simple while supporting nested exceptions.

Example: block youtube.com including subdomains, then allow music.youtube.com including subdomains. The intended result is:

| Address | Result |
| --- | --- |
| youtube.com | Blocked |
| www.youtube.com | Blocked |
| music.youtube.com | Allowed |
| example.org | Allowed in blacklist mode |

The equivalent whitelist setup only needs music.youtube.com in the allowed list unless other YouTube hosts should also be allowed.

Provide a small Test address field in setup that reports Allowed/Blocked and the winning rule. Treat HTTP and HTTPS consistently. Apply policy again after redirects, and cover already-open blocked pages when a session starts, not only future navigation. Blocked pages should stop active playback and display a neutral explanation with remaining time and the matching rule.

Proposed coverage: website rules govern normal web pages and embedded web documents in supported browser profiles. Images, scripts, media, and API calls used by allowed pages may need other hostnames. Allowing supporting resources is not permission to visit those hostnames as pages. Shared login flows may require explicit additional page exceptions.

This distinction matters for YouTube Music: the hostname exception is straightforward, but complete login and playback behavior must be tested because services can share infrastructure. Do not promise that a domain exception alone proves the entire service works.

## 10. Application blocking and coverage

Discover installed desktop applications and present readable names and icons. Identify apps using stable package/application identifiers and executable identity, not display-name text alone. Proposed initial packaging coverage: native Fedora applications and Flatpak applications. Provide an explicit executable-selection path for standalone applications only where reliable matching is supported.

When starting, handle already-running blocked applications as well as later launch attempts. Warn about unsaved work before activation. Default to asking the user to save and close apps normally, then recheck. Signal-based termination is not a document-preserving close request: require a separate destructive confirmation before SIGTERM, and another explicit force-close-or-cancel choice before SIGKILL. During an active session, deny launches where supported or promptly terminate blocked app processes; do not represent a process monitor as execution-level prevention.

Whitelist mode must preserve the desktop shell, authentication, accessibility, networking, the blocker, and required OS services. Show these as protected system components, separate from ordinary allowed apps. Application blocking is scoped to user-facing applications, not every process on the machine.

Unknown applications, helper processes, interpreters, alternate packaging, and apps that can launch other programs require explicit classification and testing. In whitelist mode, unrecognized user-facing apps should remain blocked; required helpers should inherit the parent application's policy where attribution is reliable. Terminals and development tools can provide bypass routes if intentionally allowed.

## 11. Enforcement architecture and readiness

Proposed architecture: a desktop interface, a privileged background session/enforcement service, and browser integration for supported browsers. The background service owns timing, active rules, authorization, and the log. The interface cannot end a locked session just by changing its own local state.

Approved initial browser target: Firefox, with administrator-managed policies preventing ordinary extension disabling/removal. Keep the extension installed and enabled between sessions, with no website restrictions while idle. Preserve unrelated browser policies and document any system-wide effects. Normal uninstall must remove only configuration owned by this application and is available outside an active session.

Brave is installed on the user's machine, but its website integration is deferred. Under the existing unsupported-browser coverage requirement, Brave must be visibly listed as unavailable during sessions that enforce website restrictions. Do not silently treat Brave as protected by the Firefox extension. This browser restriction applies in either blocking mode and must appear in the start summary.

Browser request APIs provide blocking/allowing rules and domain conditions; native messaging provides a way for extensions to communicate with a native application. These are building blocks, not a guarantee of complete enforcement. See [Mozilla domain-rule documentation](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/declarativeNetRequest/RuleCondition), [Chrome request-rule documentation](https://developer.chrome.com/docs/extensions/reference/api/declarativeNetRequest/), and [Mozilla native messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging).

A system service can be configured for restart after failures; persisted session recovery and expiry cleanup must still be implemented explicitly. See [systemd service documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).

Browser integration is proposed because it can distinguish page navigation from supporting resources. This is browser website blocking, not a device-wide network firewall. Native apps with embedded browsers, terminal network clients, and unsupported browsers need separate application restrictions. If all-network hostname enforcement is required, revise this architecture before implementation.

Before starting, show coverage for each allowed browser/profile, including private browsing. Do not silently leave an unsupported browser/profile available as a bypass: require working integration, block that browser for the session, or refuse to start. Keep these additional browser restrictions visible in the configuration summary. Exact support for detecting profiles, managing extensions, and responding to disabled integrations must be proven for the selected browser before claiming protection.

If required integration is lost mid-session, show a protection failure and restrict the affected browser while keeping the session timer running. Service recovery must preserve the original deadline. Do not claim indefinite fail-proof protection against root access, alternate operating systems, or arbitrary system modification.

## 12. Presets

A preset stores a unique display name, mode, website rules and exceptions, application selection, and proposed default duration. It does not contain the global stop password or historical records.

Support creating from the current draft, loading, renaming, editing, duplicating, and deleting. Loading always copies the preset into a session draft. Changing the copy does not update the saved preset. Show an Edited indicator if the draft differs from its source.

Saving changes back requires an explicit Update preset action. Save as new preset creates a separate preset. Starting a session must never implicitly save changes to a preset. Warn before replacing an unsaved draft with another preset, and confirm preset deletion. Missing/uninstalled applications remain visible as unresolved entries rather than silently disappearing.

## 13. Deferred features — not part of version one

- Scheduled sessions: associate times, days, and configurations; automatically start only when the app is open as requested. Later design must define missed starts, overlapping sessions, timezone changes, and interaction with an existing session.
- Breaks: before starting, configure at most x breaks of y minutes, optionally with at least z minutes between breaks. Later design must decide whether breaks consume session time and whether taking one affects other controls.
- Focus tracking: day/week/month summaries, history, goals, and streaks.
- Categories: classify sessions, such as hobby, academic, or work, with independent tracking, goals, and streaks.

Basic stable session identifiers and timestamps may support future extensions, but no scheduling controls, break controls, dashboards, categories, or goal systems should be built now.

## 14. Acceptance criteria

Before version one is considered complete, verify:

1. Six hours is accepted; longer and nonpositive durations are rejected by the service, including requests bypassing the UI.
2. Stop and edit works before 180 seconds and fails at or after the boundary without unlocking the session.
3. Reopening the app and restarting services do not renew the correction window.
4. A correct password alone never stops a session; a nonblank reason is persisted before release, with a consistent recorded outcome.
5. Expiry during password entry, reason submission, or log failure releases restrictions normally without a false early-stop record.
6. Preset edits in a session draft leave the saved preset unchanged; active rules remain independent of later preset edits.
7. Parent-domain rules, exact-host rules, nested exceptions, redirects, and misleading hostname suffixes behave as specified.
8. The YouTube/YouTube Music example works for navigation and is separately tested for login and playback.
9. Already-open blocked pages/apps and subsequent access attempts are handled within the documented enforcement method.
10. Supported browser profiles and private browsing are protected or explicitly made unavailable.
11. Sleep, reboot, service recovery, and normal expiry preserve the intended deadline and do not leave stale blocking behind.
12. Whitelist mode preserves essential desktop functions and denies unallowed user-facing apps within documented coverage.
13. Log records survive restarts and preset deletion and cannot be edited/deleted through the app.
14. Initial password setup requires no existing password; replacing an existing password requires the correct current password. An incorrect current password leaves the stored password unchanged, and an active session prevents password changes.

## 15. Decisions to confirm before implementation

- Target confirmed: Fedora 44, GNOME, preinstalled native Firefox.
- All running native Firefox profiles must acknowledge protection; private browsing is required to be covered.

The browser-page coverage, sleep/reboot semantics, preset-stored duration, and essential-system exceptions in whitelist mode have been accepted as defaults.

Firefox and administrator-managed extension policies are approved; Brave integration is deferred. macOS feasibility has been discussed, but a macOS implementation has not been requested. Shared session logic remains independent of GNOME and Linux process monitoring.
