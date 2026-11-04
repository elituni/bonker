# Session animation

`show_animation` is a per-session/preset boolean, defaulting to true for older
records. When enabled, the main window closes only after root-confirmed activation.
Clicking the animation reopens it; reopening an active session leaves the window
open. When disabled, the timer window stays open and the indicator is suppressed.

The unprivileged notifier uses a GTK3/XWayland window measuring **132 × 132 logical
pixels**. The original GIF is drawn at a visible width of **92 logical pixels**,
with a fixed union alpha crop `(46, 30, 200, 170)` and a 4px right offset. The
original file and ten animation frames are unchanged. The purple progress ring
starts full and shrinks. Hover text displays remaining time.

The notifier interpolates service samples using Linux `CLOCK_BOOTTIME`. The root
service remains authoritative for deadlines, passwords, reasons and release.
Idle, pending start, animation-off and failed status hide the indicator. Dragging
moves it without opening the UI; placement is retained within the same session.
A new session begins at the primary monitor's bottom left.

GTK3 runs separately from the GTK4/libadwaita main UI. XWayland provides absolute
placement and keep-above requests; the window manager controls final placement.
Failure to initialize the animation does not stop service enforcement.

`tests/companion_smoke.py` uses fake session data for real rendering, frame,
progress, tooltip, placement and lifecycle checks. Its window-manager drag call
is mocked; physical pointer behavior requires human verification.

The bundled third-party GIF is `packaging/bonker-walk.gif` (200 × 178, ten frames).
Its original source is [Tenor](https://media.tenor.com/44i61Dw7UysAAAAj/hololive-walk.gif).
This project does not claim ownership or a redistribution license for the character
art. Confirm permission before publishing or redistributing the assets.
