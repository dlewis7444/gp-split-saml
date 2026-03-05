"""Main GTK3 application window."""

import logging
from importlib import resources

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

from gp_split_saml.config import VPNConfig

log = logging.getLogger("gp_split_saml")


def _set_pointer_cursor(widget):
    """Show pointer cursor when hovering over a widget."""
    def on_enter(w, _event):
        top = w.get_toplevel().get_window()
        if top:
            top.set_cursor(Gdk.Cursor.new_from_name(w.get_display(), "pointer"))
        return False

    def on_leave(w, _event):
        top = w.get_toplevel().get_window()
        if top:
            top.set_cursor(None)
        return False

    widget.add_events(
        Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
    )
    widget.connect("enter-notify-event", on_enter)
    widget.connect("leave-notify-event", on_leave)


class MainWindow(Gtk.ApplicationWindow):
    """Primary UI window with status display and controls."""

    def __init__(self, app, on_connect, on_disconnect, on_quit, on_config_change,
                 config: VPNConfig):
        super().__init__(application=app, title="gp-split-saml")
        self.set_default_size(420, -1)
        self.set_resizable(True)
        self.get_style_context().add_class("main-window")

        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_quit = on_quit
        self._on_config_change = on_config_change
        self._config = config

        # Main vertical layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # --- Header ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.get_style_context().add_class("header-box")
        header_box.set_margin_top(12)
        header_box.set_margin_start(16)
        header_box.set_margin_end(16)

        try:
            ref = resources.files("gp_split_saml") / "data" / "icons" / "tux-born-to-route.svg"
            with resources.as_file(ref) as icon_path:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(icon_path), 64, 64, True
                )
                header_box.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
        except Exception as e:
            log.debug("Tux icon not loaded: %s", e)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_valign(Gtk.Align.CENTER)

        title_label = Gtk.Label(label="gp-split-saml")
        title_label.get_style_context().add_class("header-title")
        title_label.set_halign(Gtk.Align.START)
        title_box.pack_start(title_label, False, False, 0)

        motto_label = Gtk.Label(label="Born to route!")
        motto_label.get_style_context().add_class("header-motto")
        motto_label.set_halign(Gtk.Align.START)
        title_box.pack_start(motto_label, False, False, 0)

        header_box.pack_start(title_box, True, True, 0)
        vbox.pack_start(header_box, False, False, 0)

        # --- Config section (display/edit modes via Stack) ---
        self._config_stack = Gtk.Stack()
        self._config_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._config_stack.set_transition_duration(150)
        self._config_stack.set_homogeneous(False)  # Size to current child, not tallest

        # Display page
        display_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        display_box.set_margin_bottom(8)

        self._cfg_label1 = Gtk.Label()
        self._cfg_label1.get_style_context().add_class("config-info")
        self._cfg_label1.set_halign(Gtk.Align.CENTER)
        display_box.pack_start(self._cfg_label1, False, False, 0)

        self._cfg_label2 = Gtk.Label()
        self._cfg_label2.get_style_context().add_class("config-info")
        self._cfg_label2.set_halign(Gtk.Align.CENTER)
        display_box.pack_start(self._cfg_label2, False, False, 0)

        edit_link = Gtk.Button(label="✎ Edit for this session")
        edit_link.get_style_context().add_class("config-edit-link")
        edit_link.connect("clicked", self._show_edit_mode)
        _set_pointer_cursor(edit_link)
        display_box.pack_start(edit_link, False, False, 2)

        self._config_stack.add_named(display_box, "display")

        # Edit page
        edit_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        edit_box.set_margin_start(16)
        edit_box.set_margin_end(16)
        edit_box.set_margin_bottom(4)

        for label_text, attr, entry_attr in [
            ("Gateway", "gateway", "_edit_gateway"),
            ("Route", "vpn_internal_route", "_edit_route"),
            ("DNS", "vpn_dns", "_edit_dns"),
        ]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=label_text)
            lbl.get_style_context().add_class("config-edit-label")
            lbl.set_size_request(56, -1)
            lbl.set_halign(Gtk.Align.END)
            row.pack_start(lbl, False, False, 0)

            entry = Gtk.Entry()
            entry.set_text(getattr(config, attr))
            entry.get_style_context().add_class("config-entry")
            row.pack_start(entry, True, True, 0)
            setattr(self, entry_attr, entry)
            edit_box.pack_start(row, False, False, 0)

        note = Gtk.Label(label="Session only — to make permanent, edit your .env file")
        note.get_style_context().add_class("config-note")
        note.set_halign(Gtk.Align.CENTER)
        edit_box.pack_start(note, False, False, 2)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.CENTER)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("config-apply-btn")
        apply_btn.connect("clicked", self._apply_edit)
        _set_pointer_cursor(apply_btn)
        btn_row.pack_start(apply_btn, False, False, 0)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.get_style_context().add_class("config-cancel-btn")
        cancel_btn.connect("clicked", lambda _: self._show_display_mode())
        _set_pointer_cursor(cancel_btn)
        btn_row.pack_start(cancel_btn, False, False, 0)

        edit_box.pack_start(btn_row, False, False, 2)
        self._config_stack.add_named(edit_box, "edit")

        vbox.pack_start(self._config_stack, False, False, 0)
        self._update_config_labels()

        # --- Status Card ---
        self._status_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._status_card.get_style_context().add_class("status-card")

        self._status_rows = {}
        for field, label in [
            ("state", "Status"),
            ("gateway", "Gateway"),
            ("tunnel_ip", "Tunnel IP"),
            ("uptime", "Uptime"),
            ("route", "Route"),
        ]:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            lbl = Gtk.Label(label=label)
            lbl.get_style_context().add_class("status-label")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_size_request(80, -1)
            row.pack_start(lbl, False, False, 0)

            val = Gtk.Label(label="—")
            val.get_style_context().add_class("status-value")
            val.set_halign(Gtk.Align.START)
            val.set_ellipsize(Pango.EllipsizeMode.END)
            row.pack_start(val, True, True, 0)

            self._status_rows[field] = val
            self._status_card.pack_start(row, False, False, 0)

        vbox.pack_start(self._status_card, False, False, 0)

        # --- Connect/Disconnect Button ---
        self._connect_btn = Gtk.Button(label="CONNECT")
        self._connect_btn.get_style_context().add_class("connect-button")
        self._connect_btn.connect("clicked", self._on_connect_clicked)
        _set_pointer_cursor(self._connect_btn)
        vbox.pack_start(self._connect_btn, False, False, 4)

        # --- Exit Button ---
        exit_btn = Gtk.Button(label="EXIT")
        exit_btn.get_style_context().add_class("exit-button")
        exit_btn.connect("clicked", lambda _: self._on_quit())
        _set_pointer_cursor(exit_btn)
        vbox.pack_start(exit_btn, False, False, 0)

        # --- Log Expander ---
        self._log_expander = Gtk.Expander(label="Logs")
        self._log_expander.get_style_context().add_class("log-expander")
        self._log_expander.set_margin_start(16)
        self._log_expander.set_margin_end(16)
        self._log_expander.connect("notify::expanded", self._on_logs_toggled)
        _set_pointer_cursor(self._log_expander)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(180)
        log_scroll.get_style_context().add_class("log-frame")

        self._log_buffer = Gtk.TextBuffer()
        self._log_view = Gtk.TextView(buffer=self._log_buffer)
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_view.get_style_context().add_class("log-view")

        log_scroll.add(self._log_view)
        self._log_expander.add(log_scroll)
        vbox.pack_end(self._log_expander, False, False, 8)

        self.set_state("disconnected")

    # --- Config edit helpers ---

    def _update_config_labels(self):
        c = self._config
        self._cfg_label1.set_text(f"gateway  {c.gateway}")
        self._cfg_label2.set_text(f"route  {c.vpn_internal_route}  |  dns  {c.vpn_dns}")

    def _show_edit_mode(self, *_):
        self._edit_gateway.set_text(self._config.gateway)
        self._edit_route.set_text(self._config.vpn_internal_route)
        self._edit_dns.set_text(self._config.vpn_dns)
        self._config_stack.set_visible_child_name("edit")

    def _show_display_mode(self):
        self._config_stack.set_visible_child_name("display")

    def _apply_edit(self, *_):
        self._config.gateway = self._edit_gateway.get_text().strip()
        self._config.vpn_internal_route = self._edit_route.get_text().strip()
        self._config.vpn_dns = self._edit_dns.get_text().strip()
        self._on_config_change(self._config)
        self._update_config_labels()
        self._show_display_mode()

    # --- Logs expander resize ---

    def _on_logs_toggled(self, expander, _param):
        if not expander.get_expanded():
            # Shrink window to fit collapsed content
            GLib.idle_add(self._shrink_to_content)

    def _shrink_to_content(self):
        self.resize(420, 1)  # Fixed width; height 1 lets GTK snap to minimum
        return False

    # --- Main button ---

    def _on_connect_clicked(self, button):
        if self._current_state == "connected":
            self._on_disconnect()
        else:
            self._on_connect()

    def set_state(self, state: str, **info):
        self._current_state = state

        state_label = self._status_rows["state"]
        for cls in ("status-connected", "status-connecting", "status-disconnected", "status-error"):
            state_label.get_style_context().remove_class(cls)
        state_label.get_style_context().add_class(f"status-{state}")

        display = {
            "connected": "Connected",
            "connecting": "Connecting...",
            "disconnected": "Disconnected",
            "error": "Error",
        }
        state_label.set_text(display.get(state, state))

        self._connect_btn.get_style_context().remove_class("connect-button")
        self._connect_btn.get_style_context().remove_class("disconnect-button")

        if state == "connected":
            self._connect_btn.set_label("DISCONNECT")
            self._connect_btn.get_style_context().add_class("disconnect-button")
            self._connect_btn.set_sensitive(True)
        elif state == "connecting":
            self._connect_btn.set_label("CONNECTING...")
            self._connect_btn.get_style_context().add_class("connect-button")
            self._connect_btn.set_sensitive(False)
        else:
            self._connect_btn.set_label("CONNECT")
            self._connect_btn.get_style_context().add_class("connect-button")
            self._connect_btn.set_sensitive(True)

        for key in ("gateway", "tunnel_ip", "uptime", "route"):
            if key in info:
                self._status_rows[key].set_text(str(info[key]))
            elif state == "disconnected":
                self._status_rows[key].set_text("—")

    def update_uptime(self, text: str):
        self._status_rows["uptime"].set_text(text)

    def append_log(self, text: str):
        end = self._log_buffer.get_end_iter()
        self._log_buffer.insert(end, text + "\n")
        mark = self._log_buffer.get_insert()
        self._log_view.scroll_mark_onscreen(mark)
