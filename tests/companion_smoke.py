"""Real GTK3/XWayland rendering and handler tests with FAKE session data only."""

import argparse
import hashlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from focus_blocker.companion import Gdk, GLib, SessionAnimation  # noqa: E402


def settle(seconds=0.2):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", type=Path, required=True)
    args = parser.parse_args()
    args.capture_dir.mkdir(parents=True, exist_ok=True)
    clicked = []
    companion = SessionAnimation(lambda: clicked.append(True))
    try:
        companion.update({"active": None, "starting": True})
        assert not companion.window.get_visible()
        active = {"active": {"id": "FAKE-session", "duration": 60, "remaining": 60}}
        companion.update(active)
        settle(0.3)
        assert companion.window.get_visible()
        companion.update({"active": {**active["active"], "config": {"show_animation": False}}})
        assert not companion.window.get_visible() and companion.timer is None
        companion.update(active)
        settle(0.2)
        left, top, right, bottom = companion.content_bounds
        # Union of visible artwork is centered, regardless of transparent padding.
        assert abs((right - left) * companion.image_scale - companion.image_width) < 0.01
        assert abs((bottom - top) * companion.image_scale - companion.image_height) < 0.01
        frame = hashlib.sha256(companion.iterator.get_pixbuf().get_pixels()).hexdigest()
        settle(0.22)
        assert frame != hashlib.sha256(companion.iterator.get_pixbuf().get_pixels()).hexdigest()
        window = companion.window.get_window()
        scale = companion.window.get_scale_factor()
        work = companion.window.get_display().get_primary_monitor().get_workarea()
        position = companion.window.get_position()
        print(
            "Position:",
            tuple(position),
            "scale:",
            scale,
            "work area:",
            work.x,
            work.y,
            work.width,
            work.height,
        )
        assert abs(position[0] - (work.x + 16)) < 4
        assert abs(position[1] - (work.y + work.height - companion.height - 16)) < 4
        pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, companion.width, companion.height)
        assert pixbuf.savev(str(args.capture_dir / "animation.png"), "png", [], [])
        for remaining, name in [(60, "full"), (30, "half"), (0, "empty")]:
            companion.update({"active": {**active["active"], "remaining": remaining}})
            settle(0.1)
            pixbuf = Gdk.pixbuf_get_from_window(window, 0, 0, companion.width, companion.height)
            assert pixbuf.savev(str(args.capture_dir / (name + ".png")), "png", [], [])
            expected = f"{remaining // 60}:{remaining % 60:02} remaining"
            assert companion.area.get_tooltip_text() == expected
        companion.update(active)
        # Exercise click/drag dispatch without synthetic system input or portals.
        # GNOME pointer interaction itself remains a manual check.
        press = SimpleNamespace(button=1, x_root=90, y_root=200, time=1)
        motion = SimpleNamespace(
            x_root=130, y_root=240, time=2, state=Gdk.ModifierType.BUTTON1_MASK
        )
        with patch.object(companion.window, "begin_move_drag") as move_drag:
            companion.button_press(companion.area, press)
            companion.motion(companion.area, motion)
            companion.button_release(companion.area, press)
            move_drag.assert_called_once_with(1, 90, 200, 2)
        assert not clicked, "Dragging must not open the main window"
        companion.button_press(companion.area, press)
        companion.button_release(companion.area, press)
        assert len(clicked) == 1
        companion.window.move(position[0] + 120, position[1] - 120)
        settle()
        after = companion.window.get_position()
        assert abs(after[0] - position[0]) > 50
        companion.update(active)
        assert companion.window.get_position() == after, "Polling must retain placement"
        companion.update(None)
        assert not companion.window.get_visible() and companion.timer is None
        companion.update(active)
        settle()
        assert companion.window.get_visible()
        assert tuple(companion.window.get_position()) == tuple(after)
        companion.update(None)
        companion.update({"active": {"id": "new-FAKE-session"}})
        settle()
        assert tuple(companion.window.get_position()) == tuple(position)
        companion.update({"active": None, "release_pending": True})
        assert not companion.window.get_visible() and companion.timer is None
        print(
            "PASS: animation frames, bottom-left placement, click/drag handlers (mocked WM drag), programmatic movement, lifecycle. FAKE service; no enforcement."
        )
    finally:
        companion.close()


if __name__ == "__main__":
    main()
