# Notes for an unlisted Mozilla signing submission

Bonker is a private Fedora 44/GNOME companion extension, intended for administrator-managed installation with a local desktop application. It is not a standalone browser blocker and has no remote service or account.

The extension source is readable JavaScript, HTML and CSS in the submitted ZIP. There is no minification, transpilation, generated JavaScript, third-party library or remote code loading. No build step is needed for extension source review.

Native host: `org.focusblocker.bridge`. Add-on ID: `focus-blocker@local.invalid`.

The desktop installer sets `ExtensionSettings` to force-install the signed XPI, enables private browsing, and sets `3rdparty.Extensions["focus-blocker@local.invalid"].localBridgeConsent` to true. Without admin installation and this explicit managed authorization, the extension remains inactive and sends nothing to the native helper.

The native messages contain a transient extension-instance identifier, applied session revision, and flags for private-browsing access and managed installation. They contain no visited URLs, page contents, passwords or stop reasons. The helper returns configured website rules, time remaining and session state. Rules/expiry are cached locally. No network/analytics backend is used. See `privacy.txt` in the extension for the full local-data disclosure; the manifest declares technicalAndInteraction as optional with no required personal data transmission.

The complete companion source is in the source bundle: `focus_blocker/`, `scripts/`, `packaging/`, and the README's Fedora setup instructions. The Fedora installer requires a signed XPI. Full enforcement must be tested on Fedora; temporary/manual browser loading intentionally shows inactive setup.

Use Linux as the supported platform for this submission. Brave, macOS and Firefox for Android are outside the current integration scope.

The extension also accepts timed breaks from the authorized native service and
restores the retained rules at the break deadline, including while disconnected.
It does not offer a browser-side break control. The current companion desktop
release does not yet issue breaks; this is preparation for that future feature.
See docs/FIREFOX_PROTOCOL.md and tests/test_extension_breaks.js in the companion
source bundle for the protocol and mocked validation. No extra permissions or
outbound data fields were introduced for breaks.

Current submission version: **0.1.2**, updating the already-signed 0.1.1 under the
same add-on ID. This update renames the extension to Bonker, lists configured
website rules in the popup, and shows the user's supplied `bonk.jpg` on the
existing internal redirect page. It adds no permissions, outbound data fields,
remote resources, or changes to native authorization/enforcement. The status
message used by extension pages now includes only website mode/rules, never
application selections or passwords/reasons. The supplied image is an ordinary
bundled JPEG; CSS scales it for display. Source remains directly readable.

Local web-ext 10.8.0 validation: zero errors, zero notices, one Android minimum
version warning for the data-consent key. The desktop minimum remains Firefox
140; this Linux-only companion does not support Firefox for Android.
