# Firefox session and break protocol

The native helper relays the service's JSON response without changing its fields.
The current desktop service only emits `idle`, `starting`, and `active`. The
extension additionally supports `break`; desktop break controls, authorization,
allowances, spacing, application release, and accounting remain unimplemented.
No browser permission was added for break support.

## Messages and authority

The extension requests status through `org.focusblocker.bridge`, sending its
instance identifier, applied revision, private-window flag, and managed-install
flag. Only the administrator-authorized native connection supplies session state.
Popup/page messages can read status; they cannot request a break or end a session.

Existing states retain their current fields: `session_id`, `revision`, `phase`,
`config`, and `remaining` (seconds). `idle` has `config: null`. Other states carry
the complete website configuration and a remaining session duration from 0 to
21,600 seconds. Configurations are immutable within a revision.

A future service can authorize a break with:

```json
{
  "session_id": "session-uuid",
  "revision": "session-uuid:break-1",
  "phase": "break",
  "remaining": 1200,
  "break_remaining": 120,
  "resume_revision": "session-uuid:resume-1",
  "config": {
    "mode": "blacklist",
    "rules": [{"host": "youtube.com", "action": "block", "subdomains": true}]
  }
}
```

- `remaining` is time until the whole session ends, including the current break.
- `break_remaining` is a finite number from zero through `remaining`.
- `session_id`, `revision`, and `resume_revision` must be nonempty strings.
  The break revision and resume revision must differ.
- Each new break must use a fresh revision and fresh resume revision. Heartbeat
  replies for that break must keep those identifiers and decrease the durations.
  Do not reuse a break revision, change its rules, or extend it in place.
- The extension retains the rules while allowing website navigation during the
  break. It persists the break's wall-clock deadline separately from session
  expiry. Same-process monotonic deadlines prevent clock rollback from extending
  the break. Repeated replies for the current/last break cannot lengthen or
  reopen it. Native replies are serialized and obsolete ports are ignored.
- At break expiry it locally changes to `active` with `resume_revision`, even
  without a service reply. New navigation is restricted immediately when checked;
  a heartbeat (normally once per second while Firefox is running) sweeps open
  tabs and embedded frames. Cache/sweep failures are retried and never acknowledged
  as successful protection. A whole-session expiry takes precedence and releases
  restrictions instead.
- Restart restores the original persisted deadline, not a fresh break. Missing
  break-deadline metadata restores active restrictions. As with ordinary session
  recovery, an administrator changing the wall clock across browser restarts is
  outside the guarantee; the service must reconcile its authoritative state.
- The service may end a break early by sending `active` with the resume revision,
  or end the whole session with `idle`. A break does not automatically revisit
  previously blocked pages; users may navigate to them again.

## Requirements for the future desktop feature

Before emitting `break`, durably authorize it, enforce count/duration/spacing,
and record the deadline. Persist the resume revision so service restart knows
which acknowledgement to expect. After the break deadline, emit `active` with
that revision and the same configuration. Browser health checks must accept the
correct revision for each phase, including autonomous local resumption. Service
failure must not restart a break. Synchronize application monitoring with these
same transitions; the extension changes website restrictions only.

This protocol describes the total deadline, not how focus statistics should count
breaks. Any later pause-the-focus-clock option must still honor the six-hour
session limit. Do not send an unbounded or extended local break to implement it.

The browser status response adds `breakRemaining` (seconds, zero outside a break)
and reports `phase: "break"` while paused. Popup and blocked-page countdowns use
this field. They expose no break-start button.

## Verification

Run `node tests/test_extension_breaks.js`. The mocked suite covers authorization
message validation, repeated replies, local/offline expiry, existing tabs/frames,
restart, suspend-like clock advance, wall-clock rollback, early release,
session expiry, cache failures, failed enforcement retries, slow writes, and
break countdown text. Real Firefox/Fedora validation remains outstanding. Desktop
end-to-end break testing must wait until the service/UI feature is implemented.
