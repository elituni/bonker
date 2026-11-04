"""GTK4/libadwaita desktop client. All authoritative decisions live in the service."""

import concurrent.futures
import copy
import datetime as dt
import math
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .client import call, export_audit  # noqa: E402
from .rules import Invalid, configuration, hostname  # noqa: E402
from .ui_state import StopAuthorization, application_choices  # noqa: E402


def label(text, css=None):
    item = Gtk.Label(label=text, xalign=0, wrap=True)
    item.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if css:
        item.add_css_class(css)
    return item


def button(text, callback, css=None):
    item = Gtk.Button(label=text)
    item.connect("clicked", callback)
    if css:
        item.add_css_class(css)
    return item


def column(spacing=16):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def clear(box):
    while box.get_first_child():
        box.remove(box.get_first_child())


def timestamp(value):
    return dt.datetime.fromtimestamp(value).astimezone().strftime("%b %d, %Y · %I:%M:%S %p %Z")


class FocusWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Bonker", default_width=1040, default_height=780)
        self.set_size_request(640, 520)
        self.css = Gtk.CssProvider()
        self.css.load_from_string(
            """
            :root {
                --accent-bg-color: #9141ac;
                --accent-fg-color: #ffffff;
                --accent-color: #dc8add;
                --window-bg-color: #201925;
                --window-fg-color: #f4edf8;
                --view-bg-color: #201925;
                --view-fg-color: #f4edf8;
                --headerbar-bg-color: #201925;
                --headerbar-fg-color: #f4edf8;
                --sidebar-bg-color: #281f30;
                --sidebar-fg-color: #f4edf8;
                --sidebar-backdrop-color: #281f30;
                --card-bg-color: #33283d;
                --card-fg-color: #f4edf8;
                --dialog-bg-color: #2b2233;
                --dialog-fg-color: #f4edf8;
                --popover-bg-color: #33283d;
                --popover-fg-color: #f4edf8;
            }
            .focus-countdown { font-size: 64px; font-weight: 300; font-feature-settings: 'tnum'; }
            .focus-hero { padding: 24px 0; }
            .rule-row { padding: 8px 10px; }
            .focus-sidebar { padding: 8px; }
            .stop-reason { border: 2px solid #a88ab8; border-radius: 8px; }
            .stop-reason:focus-within { border-color: #dc8add; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.polling = False
        self.closed = False
        self.close_when_active = False
        self.status_generation = 0
        self.last = {}
        self.apps = []
        self.expanded_presets = set()
        self.preset_selection_updating = False
        self.preset_selection_current = 0
        self.rules = []
        self.selected = set()
        self.preset_name = None
        self.preset_snapshot = None
        self.authorization = StopAuthorization()
        self.presets = []
        self.log_records = []
        self.log_cursor = None
        self.log_snapshot = None
        self.log_generation = 0
        self.log_loading = False
        self.toast = Adw.ToastOverlay()
        self.set_content(self.toast)
        toolbar = Adw.ToolbarView()
        self.split = Adw.OverlaySplitView(min_sidebar_width=210, max_sidebar_width=240)
        self.toast.set_child(self.split)
        self.split.set_content(toolbar)
        self.stack = Adw.ViewStack()
        header = Adw.HeaderBar()
        self.page_title = Adw.WindowTitle(title="Session")
        header.set_title_widget(self.page_title)
        self.menu_button = Gtk.Button(label="Navigation", tooltip_text="Show navigation")
        self.menu_button.connect(
            "clicked", lambda _: self.split.set_show_sidebar(not self.split.get_show_sidebar())
        )
        self.menu_button.set_visible(False)
        self.split.connect(
            "notify::collapsed", lambda *_: self.menu_button.set_visible(self.split.get_collapsed())
        )
        header.pack_start(self.menu_button)
        toolbar.add_top_bar(header)
        toolbar.set_content(self.stack)
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 860sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.session = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_titled_with_icon(self.session, "session", "Session", "alarm-symbolic")
        self.setup = column()
        self.session.add_named(self.page(self.setup), "setup")
        self.build_setup()
        self.running = column(20)
        self.session.add_named(self.page(self.running), "running")
        self.build_running()
        self.emergency = column()
        self.session.add_named(self.page(self.emergency), "emergency")
        self.build_emergency()
        self.preset_box = column()
        self.stack.add_titled_with_icon(
            self.page(self.preset_box), "presets", "Presets", "view-list-symbolic"
        )
        self.log_box = column()
        self.stack.add_titled_with_icon(
            self.page(self.log_box), "log", "Stop log", "document-open-recent-symbolic"
        )
        self.settings = column()
        self.stack.add_titled_with_icon(
            self.page(self.settings), "settings", "Settings", "preferences-system-symbolic"
        )
        self.build_settings()
        self.build_sidebar()
        self.stack.connect("notify::visible-child-name", self.navigation)
        self.connect("close-request", self.on_close)
        self.request("apps", self.set_apps)
        self.refresh_presets()
        self.refresh_log()
        self.poll()
        self.timer_id = GLib.timeout_add_seconds(1, self.poll)

    def build_sidebar(self):
        sidebar = Adw.ToolbarView()
        heading = Adw.HeaderBar()
        brand = Gtk.Box(spacing=8)
        brand.append(Gtk.Image(icon_name="org.focusblocker.App", pixel_size=32))
        brand.append(label("Bonker", "heading"))
        heading.set_title_widget(brand)
        sidebar.add_top_bar(heading)
        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("navigation-sidebar")
        self.nav.add_css_class("focus-sidebar")
        self.nav_rows = {}
        self.destinations = {
            "session": ("Session", "alarm-symbolic"),
            "presets": ("Presets", "view-list-symbolic"),
            "log": ("Stop log", "document-open-recent-symbolic"),
            "settings": ("Settings", "preferences-system-symbolic"),
        }
        for name, (title, icon) in self.destinations.items():
            row = Gtk.ListBoxRow()
            row.destination = name
            content = Gtk.Box(
                spacing=12, margin_top=10, margin_bottom=10, margin_start=12, margin_end=12
            )
            content.append(Gtk.Image(icon_name=icon))
            content.append(label(title))
            row.set_child(content)
            self.nav.append(row)
            self.nav_rows[name] = row

        def selected(_, row):
            if row is not None:
                self.stack.set_visible_child_name(row.destination)

        def activated(_, row):
            selected(self.nav, row)
            if self.split.get_collapsed():
                self.split.set_show_sidebar(False)

        self.nav.connect("row-selected", selected)
        # Opening the overlay can focus/select its current row. Only explicit
        # activation (click/Enter) should dismiss it, not that focus transfer.
        self.nav.connect("row-activated", activated)
        self.nav.select_row(self.nav_rows["session"])
        sidebar.set_content(self.nav)
        self.split.set_sidebar(sidebar)

    def page(self, child):
        child.set_margin_top(28)
        child.set_margin_bottom(28)
        child.set_margin_start(24)
        child.set_margin_end(24)
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=540)
        clamp.set_child(child)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(clamp)
        return scroll

    def notice(self, text):
        self.toast.add_toast(Adw.Toast(title=text, timeout=6))

    def request(self, action, callback=None, error=None, work=None, **args):
        if self.closed:
            return
        future = self.pool.submit(work) if work else self.pool.submit(call, action, **args)

        def done(f):
            def deliver():
                if self.closed:
                    return False
                try:
                    result = f.result()
                except Exception as exc:
                    (error or self.notice)(str(exc))
                else:
                    if callback:
                        callback(result)
                return False

            GLib.idle_add(deliver)

        future.add_done_callback(done)

    def ask(self, heading, body, affirmative, callback, destructive=False):
        dialog = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("yes", affirmative)
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")
        dialog.set_response_appearance(
            "yes",
            Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
        )
        dialog.connect("response", lambda d, response: callback() if response == "yes" else None)
        dialog.present()

    def text_dialog(self, title, initial, callback):
        dialog = Adw.MessageDialog(transient_for=self, heading=title)
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_default_response("save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda d, response: callback(entry.get_text()) if response == "save" else None,
        )
        dialog.present()

    def build_setup(self):
        self.setup.append(label("Session Manager", "title-1"))
        group = Adw.PreferencesGroup(title="Session")
        self.setup.append(group)
        self.preset_dropdown = Gtk.DropDown.new_from_strings(["None"])
        self.preset_dropdown.connect("notify::selected", self.preset_selected)
        row = Adw.ActionRow(title="Load preset")
        row.add_suffix(self.preset_dropdown)
        group.add(row)
        self.hours = Gtk.SpinButton.new_with_range(0, 6, 1)
        self.minutes = Gtk.SpinButton.new_with_range(1, 59, 1)
        self.minutes.set_value(25)
        self.hours.set_tooltip_text("Hours")
        self.minutes.set_tooltip_text("Minutes")
        self.hours.connect(
            "value-changed",
            lambda *_: self.minutes.set_range(
                1 if self.hours.get_value_as_int() == 0 else 0,
                0 if self.hours.get_value_as_int() == 6 else 59,
            ),
        )
        row = Adw.ActionRow(title="Duration")
        duration = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        for widget in (self.hours, Gtk.Label(label="h"), self.minutes, Gtk.Label(label="min")):
            duration.append(widget)
        row.add_suffix(duration)
        group.add(row)
        self.mode = Gtk.DropDown.new_from_strings(["Blacklist", "Whitelist"])
        row = Adw.ActionRow(title="Blocking mode")
        row.add_suffix(self.mode)
        group.add(row)
        self.mode.connect("notify::selected", lambda *_: self.update_mode())
        self.show_animation = Gtk.Switch(active=True, valign=Gtk.Align.CENTER)
        row = Adw.ActionRow(title="Session animation", activatable_widget=self.show_animation)
        row.add_suffix(self.show_animation)
        group.add(row)
        self.website_title = label("Blocked websites and allowed exceptions", "title-3")
        self.setup.append(self.website_title)
        self.rule_list = column(6)
        self.setup.append(self.rule_list)
        self.setup.append(button("Add website rule", lambda _: self.rule_dialog()))
        testbox = Gtk.Box(spacing=8)
        self.test_address = Gtk.Entry(placeholder_text="Test address", hexpand=True)
        testbox.append(self.test_address)
        testbox.append(
            button(
                "Test",
                lambda _: self.request(
                    "test_address",
                    self.show_match,
                    config=self.config(),
                    address=self.test_address.get_text(),
                ),
            )
        )
        self.setup.append(testbox)
        self.match_result = label("")
        self.setup.append(self.match_result)
        self.app_title = label("Blocked applications", "title-3")
        self.setup.append(self.app_title)
        self.selected_title = label("Selected (0)", "dim-label")
        self.setup.append(self.selected_title)
        self.selected_apps = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=False,
            min_children_per_line=1,
            max_children_per_line=12,
            column_spacing=4,
            row_spacing=4,
            halign=Gtk.Align.START,
        )
        self.setup.append(self.selected_apps)
        self.search = Gtk.SearchEntry(placeholder_text="Search applications")
        self.search.connect("search-changed", lambda _: self.render_apps())
        self.setup.append(self.search)
        app_scroll = Gtk.ScrolledWindow(
            min_content_height=170, max_content_height=260, propagate_natural_height=True
        )
        self.app_list = column(4)
        app_scroll.set_child(self.app_list)
        self.setup.append(app_scroll)
        self.preset_status = label("Custom session", "dim-label")
        self.setup.append(self.preset_status)
        preset_actions = Gtk.Box(spacing=8)
        preset_actions.append(button("Save as new preset", lambda _: self.save_new()))
        self.update_preset = button("Update preset", lambda _: self.save_existing())
        preset_actions.append(self.update_preset)
        self.setup.append(preset_actions)
        self.start_button = button(
            "Review and start session",
            lambda _: self.request("preview", self.review_start, config=self.config()),
            "suggested-action",
        )
        self.start_button.add_css_class("pill")
        self.setup.append(self.start_button)
        self.readiness = label("Connecting to the local service…", "dim-label")
        self.setup.append(self.readiness)

    def update_mode(self):
        white = self.mode.get_selected() == 1
        self.website_title.set_text(
            "Allowed websites and blocked exceptions"
            if white
            else "Blocked websites and allowed exceptions"
        )
        self.app_title.set_text("Allowed applications" if white else "Blocked applications")

    def config(self):
        return {
            "minutes": self.hours.get_value_as_int() * 60 + self.minutes.get_value_as_int(),
            "mode": "whitelist" if self.mode.get_selected() else "blacklist",
            "rules": copy.deepcopy(self.rules),
            "apps": sorted(self.selected),
            "preset_name": self.preset_name,
            "show_animation": self.show_animation.get_active(),
        }

    def rule_dialog(self, index=None):
        existing = (
            self.rules[index]
            if index is not None
            else {
                "host": "",
                "action": "allow" if self.mode.get_selected() else "block",
                "subdomains": True,
            }
        )
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Website rule",
        )
        fields = column(12)
        host = Gtk.Entry(
            text=existing["host"], placeholder_text="youtube.com or https://music.youtube.com"
        )
        action = Gtk.DropDown.new_from_strings(["Block", "Allow"])
        action.set_selected(1 if existing["action"] == "allow" else 0)
        sub = Gtk.CheckButton(label="Include subdomains", active=existing["subdomains"])
        fields.append(host)
        fields.append(action)
        fields.append(sub)
        dialog.set_extra_child(fields)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Use hostname rule")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def save(_, response):
            if response != "save":
                return
            try:
                rule = {
                    "host": hostname(host.get_text()),
                    "action": "allow" if action.get_selected() else "block",
                    "subdomains": sub.get_active(),
                }
            except Invalid as exc:
                self.notice(str(exc))
                return
            if index is None:
                self.rules.append(rule)
            else:
                self.rules[index] = rule
            self.render_rules()

        dialog.connect("response", save)
        dialog.present()

    def render_rules(self):
        clear(self.rule_list)
        for index, rule in enumerate(self.rules):
            row = Gtk.Box(spacing=8)
            row.add_css_class("rule-row")
            row.add_css_class("card")
            text = column(2)
            text.set_hexpand(True)
            text.append(label(rule["host"], "heading"))
            text.append(
                label(
                    f"{rule['action'].capitalize()} · "
                    + ("Includes subdomains" if rule["subdomains"] else "Exact host"),
                    "dim-label",
                )
            )
            row.append(text)
            row.append(button("Edit", lambda _, i=index: self.rule_dialog(i)))
            row.append(button("Remove", lambda _, i=index: self.remove_rule(i)))
            self.rule_list.append(row)

    def remove_rule(self, index):
        self.rules.pop(index)
        self.render_rules()

    def show_match(self, result):
        rule = result["rule"]
        self.match_result.set_text(
            ("Allowed" if result["allowed"] else "Blocked")
            + " — "
            + (rule["host"] if rule else "mode default")
        )

    def set_apps(self, apps):
        self.apps = apps
        self.render_apps()

    def render_apps(self):
        clear(self.app_list)
        self.render_selected_apps()
        query = self.search.get_text().casefold()
        available = {a["id"] for a in self.apps if not a.get("protected")}
        for missing in sorted(self.selected - available):
            row = Gtk.Box(spacing=8)
            row.append(label("Unavailable application: " + missing, "error"))
            row.append(
                button(
                    "Remove", lambda _, a=missing: (self.selected.discard(a), self.render_apps())
                )
            )
            self.app_list.append(row)
        for app in application_choices(self.apps, self.selected):
            if query not in (app["name"] + " " + " ".join(app["aliases"])).casefold():
                continue
            row = Gtk.Box(spacing=8)
            image = self.app_icon(app)
            row.append(image)
            check = Gtk.CheckButton(
                label=app["name"] + (" · Flatpak" if app["flatpak"] else ""),
                active=bool(app["aliases"] & self.selected),
            )
            check.set_tooltip_text(app["id"])
            check.connect(
                "toggled",
                lambda check, entry=app: self.toggle_app(entry, check.get_active()),
            )
            row.append(check)
            self.app_list.append(row)

    def toggle_app(self, app, enabled):
        self.selected.difference_update(app["aliases"])
        if enabled:
            self.selected.add(app["id"])
        self.render_selected_apps()

    @staticmethod
    def app_icon(app):
        icon = app.get("icon") or "application-x-executable"
        image = (
            Gtk.Image.new_from_file(icon)
            if icon.startswith("/")
            else Gtk.Image.new_from_icon_name(icon)
        )
        image.set_pixel_size(24)
        return image

    def render_selected_apps(self):
        clear(self.selected_apps)
        choices = [
            app
            for app in application_choices(self.apps, self.selected)
            if app["aliases"] & self.selected
        ]
        covered = set().union(*(app["aliases"] for app in choices))
        choices.extend(
            {"name": "Unavailable: " + item, "aliases": {item}, "icon": "dialog-warning-symbolic"}
            for item in sorted(self.selected - covered)
        )
        self.selected_title.set_text(f"Selected ({len(choices)})")
        self.selected_apps.set_visible(bool(choices))
        for app in choices:
            item = Gtk.Button(child=self.app_icon(app))
            item.set_tooltip_text(app["name"] + " · Remove")
            item.update_property([Gtk.AccessibleProperty.LABEL], ["Remove " + app["name"]])
            item.connect(
                "clicked", lambda _, aliases=app["aliases"]: self.remove_selected_apps(aliases)
            )
            self.selected_apps.insert(item, -1)

    def remove_selected_apps(self, aliases):
        self.selected.difference_update(aliases)
        self.render_apps()

    def review_start(self, preview):
        config = preview["config"]
        rules = "\n".join(
            f"• {r['action'].capitalize()} {r['host']} ({'with subdomains' if r['subdomains'] else 'exact'})"
            for r in config["rules"]
        )
        body = f"{config['minutes']} minutes · {config['mode'].capitalize()}\n\n{rules or 'No website entries.'}"
        if preview["websites"]:
            body += "\n\nBrave and unsupported browsers will be unavailable."
        if config["mode"] == "whitelist" and not config["apps"]:
            body += "\n\nNo ordinary applications are allowed. Essential desktop services remain available."
        if preview["closing"]:
            body += "\n\nSave your work. These applications must close:\n" + ", ".join(
                preview["closing"]
            )
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Save and close applications",
                body=body
                + "\n\nClose them normally to keep their save prompts. Recheck when ready. Terminating apps can discard unsaved work without a save prompt.",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("terminate", "Terminate apps…")
            dialog.add_response("recheck", "Recheck")
            dialog.set_close_response("cancel")
            dialog.set_default_response("recheck")
            dialog.set_response_appearance("terminate", Adw.ResponseAppearance.DESTRUCTIVE)

            def respond(_, response):
                if response == "recheck":
                    self.request("preview", self.review_start, config=config)
                elif response == "terminate":
                    self.ask(
                        "Terminate these applications?",
                        ", ".join(preview["closing"])
                        + "\n\nUnsaved work may be lost. Save and close normally if you need it.",
                        "Terminate apps",
                        lambda: self.request(
                            "close_apps",
                            lambda _: GLib.timeout_add_seconds(3, self.check_closed, config),
                            config=config,
                        ),
                        destructive=True,
                    )

            dialog.connect("response", respond)
            dialog.present()
        else:
            body += "\n\nYou can stop freely for the first 3 minutes. After that, your password and a reason are required."
            return self.confirm_start(body, config)

    def confirm_start(self, body, config):
        # Plain undecorated confirmation; standard buttons activate on release.
        dialog = Gtk.Window(
            title="Start session",
            transient_for=self,
            modal=True,
            decorated=False,
            resizable=False,
            default_width=440,
        )
        dialog.add_css_class("background")
        content = column(20)
        for side in ("top", "bottom", "start", "end"):
            getattr(content, "set_margin_" + side)(24)
        content.append(label("Start this session?", "title-2"))
        text = label(body)
        text.set_max_width_chars(48)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            max_content_height=400,
            propagate_natural_height=True,
        )
        scroll.set_child(text)
        content.append(scroll)
        actions = Gtk.Box(spacing=12, homogeneous=True)
        cancel = button("Cancel", lambda _: dialog.destroy())

        def start(_):
            dialog.destroy()
            self.begin(config)

        actions.append(cancel)
        actions.append(button("Start session", start, "suggested-action"))
        content.append(actions)
        dialog.set_child(content)
        dialog.set_default_widget(cancel)
        keys = Gtk.EventControllerKey()

        def key_pressed(_controller, keyval, _keycode, _state):
            if keyval == 0xFF1B:  # Escape; keep Gdk out of the public UI imports.
                dialog.destroy()
                return True
            return False

        keys.connect("key-pressed", key_pressed)
        dialog.add_controller(keys)
        dialog.present()
        cancel.grab_focus()
        return dialog

    def check_closed(self, config):
        def checked(preview):
            if preview["closing"]:
                self.ask(
                    "Some applications are still open",
                    ", ".join(preview["closing"])
                    + "\n\nForce closing may discard unsaved work. Cancel to save it first.",
                    "Force close",
                    lambda: self.request(
                        "close_apps",
                        lambda _: GLib.timeout_add_seconds(1, self.retry_start, config),
                        config=config,
                        force=True,
                    ),
                    destructive=True,
                )
            else:
                self.begin(config)

        self.request("preview", checked, config=config)
        return False

    def retry_start(self, config):
        self.begin(config)
        return False

    def begin(self, config):
        self.start_button.set_sensitive(False)

        def prepared(_):
            self.status_generation += 1
            self.close_when_active = config.get("show_animation", True)
            self.poll()

        self.request(
            "prepare",
            prepared,
            config=config,
            error=lambda msg: (self.start_button.set_sensitive(True), self.notice(msg)),
        )

    def build_running(self):
        hero = column(12)
        hero.add_css_class("focus-hero")
        heading = label("Session", "title-1")
        heading.set_xalign(0.5)
        hero.append(heading)
        self.countdown = label("00:00", "focus-countdown")
        self.countdown.set_xalign(0.5)
        self.countdown.add_css_class("numeric")
        hero.append(self.countdown)
        self.end_label = label("", "dim-label")
        self.end_label.set_xalign(0.5)
        hero.append(self.end_label)
        self.running.append(hero)
        self.running_summary = label("")
        self.running_summary.set_margin_top(12)
        details = Gtk.Expander(label="Session details")
        details.set_child(self.running_summary)
        self.running.append(details)
        self.grace_button = button("Stop and edit · 3:00", lambda _: self.cancel_session())
        self.running.append(self.grace_button)
        self.protection_label = label("", "error")
        self.running.append(self.protection_label)
        self.emergency_button = button("Emergency stop…", lambda _: self.open_emergency(), "flat")
        self.emergency_button.set_halign(Gtk.Align.END)
        self.running.append(self.emergency_button)

    def cancel_session(self):
        # An explicit Cancel/Stop-and-edit takes precedence over auto-closing,
        # even if an already-in-flight status reply reports activation.
        self.close_when_active = False
        self.request("cancel", self.restore_after_cancel)

    def restore_after_cancel(self, config):
        self.load_config(config)
        self.session.set_visible_child_name("setup")
        self.notice("Session stopped. Edit your configuration and restart when ready.")

    def build_emergency(self):
        self.emergency.append(label("Emergency stop", "title-1"))
        self.password_step = column()
        self.stop_password = Gtk.PasswordEntry(
            show_peek_icon=True, placeholder_text="Emergency-stop password"
        )
        self.password_step.append(self.stop_password)
        self.verify_button = button(
            "Verify password", lambda _: self.verify_stop(), "suggested-action"
        )
        self.password_step.append(self.verify_button)
        self.emergency.append(self.password_step)
        self.reason_step = column()
        self.reason_step.append(label("Reason for stopping", "title-3"))
        self.reason = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=12,
            left_margin=12,
            right_margin=12,
            bottom_margin=12,
        )
        scroll = Gtk.ScrolledWindow(min_content_height=140)
        scroll.add_css_class("stop-reason")
        scroll.set_child(self.reason)
        self.reason_step.append(scroll)
        self.reason_step.append(
            button(
                "Stop session and record reason",
                lambda _: self.submit_reason(),
                "destructive-action",
            )
        )
        self.emergency.append(self.reason_step)
        self.emergency.append(button("Back to session", lambda _: self.continue_session(), "flat"))

    def open_emergency(self):
        self.clear_authorization()
        self.verify_button.set_sensitive(True)
        self.stop_password.set_text("")
        self.password_step.set_visible(True)
        self.reason_step.set_visible(False)
        self.reason.get_buffer().set_text("")
        self.session.set_visible_child_name("emergency")

    def verify_stop(self):
        self.clear_authorization()
        generation = self.authorization.generation
        self.verify_button.set_sensitive(False)
        password = self.stop_password.get_text()
        self.stop_password.set_text("")

        def authorized(result):
            if not result or not self.authorization.matches(generation):
                return
            self.verify_button.set_sensitive(True)
            self.password_step.set_visible(False)
            self.reason_step.set_visible(True)

        def work():
            result = call("authorize", password=password)
            if not self.authorization.accept(generation, result["token"]):
                call("revoke", token=result["token"])
                return None
            return result

        def failed(message):
            if self.authorization.matches(generation):
                self.verify_button.set_sensitive(True)
                self.notice(message)

        self.request("authorize", authorized, error=failed, work=work)

    def clear_authorization(self):
        token = self.authorization.invalidate()
        if token:
            self.pool.submit(call, "revoke", token=token)

    def continue_session(self):
        self.clear_authorization()
        self.stop_password.set_text("")
        self.reason.get_buffer().set_text("")
        self.session.set_visible_child_name("running")

    def submit_reason(self):
        buf = self.reason.get_buffer()
        reason = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        self.request(
            "stop",
            lambda _: (self.continue_session(), self.refresh_log(), self.poll()),
            token=self.authorization.token,
            reason=reason,
        )

    def build_settings(self):
        self.settings.append(label("Settings", "title-1"))
        self.settings.append(label("Emergency-stop password", "title-3"))
        self.current_password = Gtk.PasswordEntry(
            show_peek_icon=True, placeholder_text="Current password (if one is set)"
        )
        self.new_password = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="New password")
        self.confirm_password = Gtk.PasswordEntry(
            show_peek_icon=True, placeholder_text="Confirm new password"
        )
        for field in (self.current_password, self.new_password, self.confirm_password):
            self.settings.append(field)
        self.password_button = button(
            "Save password", lambda _: self.save_password(), "suggested-action"
        )
        self.settings.append(self.password_button)
        self.settings.append(label("Protection status", "title-3"))
        self.settings_status = label("Connecting…")
        self.settings.append(self.settings_status)

    def save_password(self):
        if self.new_password.get_text() != self.confirm_password.get_text():
            self.notice("New passwords do not match.")
            return

        def saved(_):
            for field in (self.current_password, self.new_password, self.confirm_password):
                field.set_text("")
            self.notice("Password saved.")
            self.poll()

        self.request(
            "set_password",
            saved,
            new=self.new_password.get_text(),
            current=self.current_password.get_text(),
        )

    def refresh_presets(self):
        self.request("presets", self.render_presets)

    def render_presets(self, presets):
        self.presets = presets
        self.sync_preset_dropdown()
        clear(self.preset_box)
        self.preset_box.append(label("Presets", "title-1"))
        if not presets:
            self.preset_box.append(label("No presets."))
        for preset in presets:
            group = column(8)
            group.append(label(preset["name"], "title-3"))
            group.append(
                label(
                    f"{preset['config']['minutes']} minutes · {preset['config']['mode']} · {len(preset['config']['rules'])} website rules",
                    "dim-label",
                )
            )
            actions = Gtk.Box(spacing=6)
            actions.append(button("Load / edit", lambda _, p=preset: self.offer_load(p)))
            actions.append(
                button(
                    "Rename",
                    lambda _, p=preset: self.text_dialog(
                        "Rename preset",
                        p["name"],
                        lambda name: self.request(
                            "save_preset",
                            lambda _: self.refresh_presets(),
                            name=name,
                            original=p["name"],
                            config=p["config"],
                            overwrite=name == p["name"],
                        ),
                    ),
                )
            )
            actions.append(
                button(
                    "Duplicate",
                    lambda _, p=preset: self.text_dialog(
                        "Duplicate preset",
                        p["name"] + " copy",
                        lambda name: self.request(
                            "save_preset",
                            lambda _: self.refresh_presets(),
                            name=name,
                            config=p["config"],
                        ),
                    ),
                )
            )
            actions.append(
                button(
                    "Delete",
                    lambda _, p=preset: self.ask(
                        "Delete preset?",
                        p["name"] + " will be removed. Existing log entries remain.",
                        "Delete",
                        lambda: self.request(
                            "delete_preset", lambda _: self.refresh_presets(), name=p["name"]
                        ),
                        True,
                    ),
                )
            )
            group.append(actions)
            details = Gtk.Expander(label="Details")
            details.connect(
                "notify::expanded",
                lambda expander, _, p=preset: self.expand_preset(expander, p),
            )
            details.set_expanded(preset["name"] in self.expanded_presets)
            group.append(details)
            self.preset_box.append(group)

    def expand_preset(self, expander, preset):
        if expander.get_expanded():
            self.expanded_presets.add(preset["name"])
            expander.set_child(self.preset_details(preset["config"]))
        else:
            self.expanded_presets.discard(preset["name"])

    def preset_details(self, config):
        content = column(8)
        white = config["mode"] == "whitelist"
        content.append(
            label("Allowed applications" if white else "Blocked applications", "heading")
        )
        selected = set(config["apps"])
        choices = [a for a in application_choices(self.apps, selected) if a["aliases"] & selected]
        covered = set().union(*(a["aliases"] for a in choices))
        names = {a["id"]: a["name"] for a in self.apps}
        for app in choices:
            row = Gtk.Box(spacing=8)
            row.append(self.app_icon(app))
            row.append(label(app["name"] + (" · Flatpak" if app.get("flatpak") else "")))
            content.append(row)
        for app_id in sorted(selected - covered):
            content.append(
                label(names.get(app_id, app_id) + " · Unavailable or protected", "dim-label")
            )
        if not selected:
            content.append(label("None selected", "dim-label"))
        content.append(
            label(
                "Other applications blocked; system components remain available."
                if white
                else "Other applications allowed.",
                "dim-label",
            )
        )
        content.append(label("Website rules", "heading"))
        content.append(
            label("Other websites blocked." if white else "Other websites allowed.", "dim-label")
        )
        for rule in config["rules"]:
            content.append(
                label(
                    f"{rule['action'].capitalize()} · {rule['host']} · "
                    + ("Includes subdomains" if rule["subdomains"] else "Exact hostname")
                )
            )
        if not config["rules"]:
            content.append(label("No website rules", "dim-label"))
        content.append(
            label(
                "Session animation: " + ("On" if config.get("show_animation", True) else "Off"),
                "dim-label",
            )
        )
        return content

    def sync_preset_dropdown(self):
        self.preset_selection_updating = True
        try:
            names = [p["name"] for p in self.presets]
            model = self.preset_dropdown.get_model()
            labels = ["None"] + names
            if [model.get_string(i) for i in range(model.get_n_items())] != labels:
                self.preset_dropdown.set_model(Gtk.StringList.new(labels))
            self.preset_selection_current = (
                names.index(self.preset_name) + 1 if self.preset_name in names else 0
            )
            if self.preset_dropdown.get_selected() != self.preset_selection_current:
                self.preset_dropdown.set_selected(self.preset_selection_current)
        finally:
            self.preset_selection_updating = False

    def preset_selected(self, *_):
        if self.preset_selection_updating:
            return
        index = self.preset_dropdown.get_selected()
        if index == self.preset_selection_current:
            return
        self.preset_selection_current = index
        if index == 0:
            if self.last.get("active") or self.last.get("starting"):
                self.sync_preset_dropdown()
                return
            self.load_config(configuration({}))
            self.search.set_text("")
            self.test_address.set_text("")
            self.match_result.set_text("")
        elif index <= len(self.presets):
            self.offer_load(self.presets[index - 1])

    def offer_load(self, preset):
        self.sync_preset_dropdown()
        if self.last.get("active") or self.last.get("starting"):
            self.notice("Load presets after the current session ends.")
            return
        self.ask(
            "Load this preset?",
            "This replaces your current session draft with an editable copy of “"
            + preset["name"]
            + "”.",
            "Load copy",
            lambda: self.load_config(preset["config"], preset=True),
        )

    def load_config(self, config, preset=False):
        self.hours.set_value(config["minutes"] // 60)
        self.minutes.set_value(config["minutes"] % 60)
        self.mode.set_selected(1 if config["mode"] == "whitelist" else 0)
        self.rules = copy.deepcopy(config["rules"])
        self.selected = set(config["apps"])
        self.show_animation.set_active(config.get("show_animation", True))
        self.preset_name = config.get("preset_name")
        self.preset_snapshot = self.config() if preset else None
        self.sync_preset_dropdown()
        self.render_rules()
        self.render_apps()
        self.stack.set_visible_child_name("session")
        self.session.set_visible_child_name("setup")

    def save_new(self):
        self.text_dialog(
            "Save as new preset",
            "",
            lambda name: self.request(
                "save_preset", lambda _: self.preset_saved(name), name=name, config=self.config()
            ),
        )

    def save_existing(self):
        if self.preset_name:
            self.ask(
                "Update saved preset?",
                "Replace “" + self.preset_name + "” with this draft?",
                "Update preset",
                lambda: self.request(
                    "save_preset",
                    lambda _: self.preset_saved(self.preset_name),
                    name=self.preset_name,
                    config=self.config(),
                    overwrite=True,
                ),
            )

    def preset_saved(self, name):
        self.preset_name = name
        self.preset_snapshot = self.config()
        self.refresh_presets()
        self.notice("Preset saved.")

    def refresh_log(self):
        self.log_generation += 1
        self.log_records = []
        self.log_cursor = None
        self.log_snapshot = None
        self.log_loading = False
        self.load_log_page()

    def load_log_page(self):
        if self.log_loading:
            return
        generation = self.log_generation
        self.log_loading = True

        def received(page):
            if generation != self.log_generation:
                return
            self.log_loading = False
            self.log_cursor = page["next_before"]
            self.log_snapshot = page["through"]
            self.render_log(self.log_records + page["records"])

        def failed(message):
            if generation == self.log_generation:
                self.log_loading = False
                self.notice(message)

        self.request(
            "audit", received, error=failed, before=self.log_cursor, through=self.log_snapshot
        )

    def render_log(self, records):
        self.log_records = records
        clear(self.log_box)
        self.log_box.append(label("Stop log", "title-1"))
        self.log_box.append(button("Export log…", lambda _: self.export_log()))
        released = {r["session_id"]: r for r in records if r["event"] == "released"}
        reasons = [r for r in records if r["event"] == "release_authorized"]
        if not reasons:
            self.log_box.append(label("No early stops recorded."))
        for record in reasons:
            box = column(6)
            box.append(label(timestamp(record["timestamp"]), "title-3"))
            box.append(label(record["reason"]))
            outcome = released.get(record["session_id"])
            released_at = record.get("released_at") or (outcome["timestamp"] if outcome else None)
            detail = f"{record['mode']} · {record.get('preset_name') or 'Custom session'} · {int(record['elapsed']) // 60} of {record['duration'] // 60} minutes"
            detail += (
                "\nReleased: " + timestamp(released_at)
                if released_at is not None
                else "\nRelease requested; browser acknowledgement pending."
            )
            detail += (
                "\nStarted: "
                + timestamp(record["started_at"])
                + "\nPlanned end: "
                + timestamp(record["planned_end"])
            )
            box.append(label(detail, "dim-label"))
            self.log_box.append(box)
        if self.log_cursor is not None:
            self.log_box.append(button("Load older entries", lambda _: self.load_log_page()))

    def export_log(self):
        chooser = Gtk.FileChooserNative(
            title="Export stop log",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="Export",
            cancel_label="Cancel",
        )
        chooser.set_current_name("focus-blocker-stop-log.json")

        def selected(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                path = dialog.get_file().get_path()
                if path:
                    self.request(
                        "audit",
                        lambda _: self.notice("Full log exported."),
                        work=lambda: export_audit(path),
                    )
            dialog.destroy()

        chooser.connect("response", selected)
        chooser.show()

    def navigation(self, *_):
        name = self.stack.get_visible_child_name()
        self.page_title.set_title(self.destinations[name][0])
        if self.nav.get_selected_row() is not self.nav_rows[name]:
            self.nav.select_row(self.nav_rows[name])
        if name != "session" and self.session.get_visible_child_name() == "emergency":
            self.continue_session()
        if name == "log":
            self.refresh_log()
        elif name == "presets":
            self.refresh_presets()

    def poll(self):
        if self.closed:
            return False
        if self.polling:
            return True
        self.polling = True
        generation = self.status_generation

        def stale():
            if generation != self.status_generation:
                self.polling = False
                self.poll()
                return True
            return False

        def received(status):
            if not stale():
                self.render_status(status)

        def failed(message):
            if stale():
                return
            self.polling = False
            self.readiness.set_text(message)
            self.settings_status.set_text(message)
            self.protection_label.set_text(message)
            self.start_button.set_sensitive(False)

        self.request("status", received, error=failed)
        return True

    def render_status(self, status):
        self.polling = False
        self.last = status
        active = status["active"]
        starting = status["starting"]
        if self.close_when_active:
            if active and not (status.get("protection_error") or status.get("error")):
                self.close_when_active = False
                self.close()
                return
            if active or not starting:
                self.close_when_active = False
        self.password_button.set_sensitive(not active and not starting)
        self.start_button.set_sensitive(
            not active and not starting and status["password_set"] and not status.get("maintenance")
        )
        self.current_password.set_sensitive(status["password_set"])
        self.update_preset.set_sensitive(bool(self.preset_name))
        edited = self.preset_snapshot is not None and self.config() != self.preset_snapshot
        self.preset_status.set_text(
            (self.preset_name or "Custom session") + (" · Edited copy" if edited else "")
        )
        info = (
            "Password set" if status["password_set"] else "Set a stop password in Settings"
        ) + "\n"
        info += "Firefox policy: " + ("installed" if status["policy_ready"] else "not installed")
        info += " · Integration: " + (
            "connected" if status["firefox_connected"] else "open Firefox to connect"
        )
        if status.get("maintenance"):
            info += "\nAdministrator uninstall in progress; new sessions are disabled."
        self.readiness.set_text(info)
        self.settings_status.set_text(info)
        if active or starting:
            if self.session.get_visible_child_name() != "emergency":
                self.session.set_visible_child_name("running")
            self.protection_label.set_text(status.get("protection_error") or "")
            if starting:
                self.countdown.set_text("Preparing…")
                self.end_label.set_text("Waiting for Firefox to apply your rules.")
                self.grace_button.set_label("Cancel start")
                self.grace_button.set_visible(True)
                self.emergency_button.set_visible(False)
            else:
                seconds = math.ceil(active["remaining"])
                self.countdown.set_text(
                    f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"
                )
                self.end_label.set_text("Ends " + timestamp(active["ends_at"]))
                config = active["config"]
                rules = "\n".join(
                    f"{r['action'].capitalize()} {r['host']} · {'includes subdomains' if r['subdomains'] else 'exact host'}"
                    for r in config["rules"]
                )
                names = {app["id"]: app["name"] for app in self.apps}
                apps = ", ".join(names.get(a, a) for a in config["apps"]) or "None selected"
                apps_label = "Allowed apps" if config["mode"] == "whitelist" else "Blocked apps"
                self.running_summary.set_text(
                    f"{config['mode'].capitalize()} · {config.get('preset_name') or 'Custom session'}\n\n{rules}\n\n{apps_label}: {apps}"
                )
                grace = math.ceil(active["grace_remaining"])
                self.grace_button.set_visible(grace > 0)
                self.grace_button.set_label(f"Stop and edit · {grace // 60}:{grace % 60:02}")
                self.emergency_button.set_visible(grace <= 0)
        else:
            if self.session.get_visible_child_name() != "setup":
                self.continue_session()
                self.session.set_visible_child_name("setup")
            if status.get("error") and status.get("error") != getattr(self, "shown_error", None):
                self.shown_error = status["error"]
                self.notice(status["error"])

    def on_close(self, *_):
        self.clear_authorization()
        self.closed = True
        GLib.source_remove(self.timer_id)
        self.pool.shutdown(wait=False)
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.css)
        return False


class Application(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.focusblocker.App", flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

    def do_activate(self):
        self.get_style_manager().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        window = self.get_active_window()
        if window is None:
            window = FocusWindow(self)
        window.present()


def main():
    # Autostart covers login; also recover the unprivileged helper after a crash
    # or an installation performed without logging out.
    notifier = "/usr/local/bin/focus-blocker-notifier"
    if Path(notifier).is_file():
        try:
            Gio.Subprocess.new([notifier], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            pass  # The session and launcher remain usable without decoration.
    Application().run(None)


if __name__ == "__main__":
    main()
