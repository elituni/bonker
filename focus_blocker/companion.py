"""Decorative, unprivileged XWayland window; session state always comes from the service.

Import only in the separate notifier process, never in the GTK4 UI process.
"""

import math
import os
import time
from pathlib import Path

# GNOME Wayland does not expose absolute positioning or keep-above to clients.
os.environ["GDK_BACKEND"] = "x11"

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .ui_state import SessionProgress  # noqa: E402

PURPLE = (0x91 / 255, 0x41 / 255, 0xAC / 255)  # GNOME purple accent, #9141ac
# Union of alpha bounds across all ten frames of the supplied 200x178 GIF.
# A fixed union avoids making the character jitter as its pose changes. Only the
# draw transform changes; the original artwork and its animation stay intact.
CONTENT_BOUNDS = (46, 30, 200, 170)
CHARACTER_OFFSET_X = 4  # User preference: a little more clearance on the left.


def display_time():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def animation_path():
    local = Path(__file__).resolve().parents[1] / "packaging/bonker-walk.gif"
    return local if local.is_file() else Path("/usr/local/share/focus-blocker/bonker-walk.gif")


class SessionAnimation:
    def __init__(self, activate=lambda: None):
        if not Gtk.init_check()[0]:
            raise RuntimeError("Session animation requires an X11/XWayland display")
        self.animation = GdkPixbuf.PixbufAnimation.new_from_file(str(animation_path()))
        self.iterator = None
        self.timer = None
        self.activate = activate
        self.press = None
        self.dragged = False
        self.session_id = None
        self.saved_position = None
        self.progress = SessionProgress()
        self.window = Gtk.Window(title="Bonker session animation")
        self.window.set_wmclass("bonker-companion", "Bonker")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.window.set_keep_above(True)
        self.window.stick()
        self.window.set_app_paintable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual:
            self.window.set_visual(visual)
        self.width = self.height = 132
        self.content_bounds = (
            CONTENT_BOUNDS
            if (self.animation.get_width(), self.animation.get_height()) == (200, 178)
            else (0, 0, self.animation.get_width(), self.animation.get_height())
        )
        self.image_width = 92
        left, top, right, bottom = self.content_bounds
        self.image_scale = self.image_width / (right - left)
        self.image_height = (bottom - top) * self.image_scale
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.width, self.height)
        self.area.set_tooltip_text("Remaining time unavailable")
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.area.connect("button-press-event", self.button_press)
        self.area.connect("motion-notify-event", self.motion)
        self.area.connect("button-release-event", self.button_release)
        self.area.connect("draw", self.draw)
        self.window.add(self.area)
        self.window.connect("delete-event", lambda *_: True)

    def button_press(self, _widget, event):
        if event.button != 1:
            return False
        self.press = (int(event.x_root), int(event.y_root))
        self.dragged = False
        return True

    def motion(self, _widget, event):
        if self.press and not self.dragged and event.state & Gdk.ModifierType.BUTTON1_MASK:
            x, y = self.press
            threshold = Gtk.Settings.get_default().get_property("gtk-dnd-drag-threshold")
            if abs(event.x_root - x) > threshold or abs(event.y_root - y) > threshold:
                self.dragged = True
                self.window.begin_move_drag(1, x, y, event.time)
        return True

    def button_release(self, _widget, event):
        if event.button == 1 and self.press:
            clicked = not self.dragged
            self.press = None
            if clicked:
                self.activate()
            return True
        return False

    def draw(self, _widget, context):
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_rgba(0, 0, 0, 0)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)
        center = self.width / 2
        radius = center - 6
        context.set_line_width(4)
        context.set_source_rgba(*PURPLE, 0.22)
        context.arc(center, center, radius, 0, 2 * math.pi)
        context.stroke()
        fraction = self.progress.fraction(display_time())
        if fraction > 0:
            context.set_source_rgb(*PURPLE)
            context.set_line_cap(cairo.LINE_CAP_ROUND)
            context.arc(center, center, radius, -math.pi / 2, -math.pi / 2 + 2 * math.pi * fraction)
            context.stroke()
        if self.iterator is not None:
            frame = self.iterator.get_pixbuf()
            context.translate(
                (self.width - self.image_width) / 2 + CHARACTER_OFFSET_X,
                (self.height - self.image_height) / 2,
            )
            context.scale(self.image_scale, self.image_scale)
            context.translate(-self.content_bounds[0], -self.content_bounds[1])
            Gdk.cairo_set_source_pixbuf(context, frame, 0, 0)
            context.paint()
        return False

    def place(self):
        if not self.window.get_visible():
            return False
        display = self.window.get_display()
        if self.saved_position is not None:
            x, y = self.saved_position
            monitor = display.get_monitor_at_point(x, y)
            if monitor:
                work = monitor.get_workarea()
                x = max(work.x, min(x, work.x + work.width - self.width))
                y = max(work.y, min(y, work.y + work.height - self.height))
            self.window.move(x, y)
            return False
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor:
            work = monitor.get_workarea()
            self.window.move(work.x + 16, work.y + work.height - self.height - 16)
        return False

    def tick(self):
        self.iterator.advance(None)
        self.area.queue_draw()
        text = self.progress.tooltip(display_time())
        if text != self.area.get_tooltip_text():
            self.area.set_tooltip_text(text)
            self.area.trigger_tooltip_query()
        return True

    def update(self, status):
        # No inferred countdown: hide on idle, pending start, or connection failure.
        active = bool(
            status
            and status.get("active")
            and status["active"].get("config", {}).get("show_animation", True)
        )
        if active:
            self.progress.update(status["active"], display_time())
            self.area.set_tooltip_text(self.progress.tooltip(display_time()))
        if active and not self.window.get_visible():
            session_id = status["active"].get("id")
            if session_id != self.session_id:
                self.saved_position = None
                self.session_id = session_id
            self.iterator = self.animation.get_iter(None)
            self.window.show_all()
            self.timer = GLib.timeout_add(40, self.tick)
            GLib.idle_add(self.place)
        elif not active:
            self.hide()

    def hide(self):
        if self.window.get_visible():
            self.saved_position = tuple(self.window.get_position())
        self.window.hide()
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None
        self.iterator = None

    def close(self):
        self.hide()
        self.window.destroy()
