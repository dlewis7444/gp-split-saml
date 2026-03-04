"""Main GTK3 application window."""

import logging
from importlib import resources

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GdkPixbuf, Pango

log = logging.getLogger("gp_split_saml")


class MainWindow(Gtk.ApplicationWindow):
    """Primary UI window with status display and controls."""

    def __init__(self, app, on_connect, on_disconnect):
        super().__init__(application=app, title="gp-split-saml")
        self.set_default_size(420, 580)
        self.get_style_context().add_class("main-window")

        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

        # Main vertical layout
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # --- Header ---
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_box.get_style_context().add_class("header-box")
        header_box.set_margin_top(12)
        header_box.set_margin_start(16)
        header_box.set_margin_end(16)

        # Tux icon
        try:
            ref = resources.files("gp_split_saml") / "data" / "icons" / "tux-born-to-route.svg"
            with resources.as_file(ref) as icon_path:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(icon_path), 64, 64, True
                )
                tux_image = Gtk.Image.new_from_pixbuf(pixbuf)
                header_box.pack_start(tux_image, False, False, 0)
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

        # --- Separator ---
        sep = Gtk.Separator()
        sep.get_style_context().add_class("themed-separator")
        vbox.pack_start(sep, False, False, 4)

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
        vbox.pack_start(self._connect_btn, False, False, 4)

        # --- Log Expander ---
        expander = Gtk.Expander(label="Logs")
        expander.get_style_context().add_class("log-expander")
        expander.set_margin_start(16)
        expander.set_margin_end(16)

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(150)
        log_scroll.set_max_content_height(200)
        log_scroll.get_style_context().add_class("log-frame")

        self._log_buffer = Gtk.TextBuffer()
        self._log_view = Gtk.TextView(buffer=self._log_buffer)
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._log_view.get_style_context().add_class("log-view")

        log_scroll.add(self._log_view)
        expander.add(log_scroll)
        vbox.pack_end(expander, True, True, 8)

        self.set_state("disconnected")

    def _on_connect_clicked(self, button):
        state = self._current_state
        if state == "connected":
            self._on_disconnect()
        else:
            self._on_connect()

    def set_state(self, state: str, **info):
        """Update UI state: 'connected', 'connecting', 'disconnected', 'error'."""
        self._current_state = state

        # Update status label
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

        # Update button
        self._connect_btn.get_style_context().remove_class("connect-button")
        self._connect_btn.get_style_context().remove_class("disconnect-button")

        if state == "connected":
            self._connect_btn.set_label("DISCONNECT")
            self._connect_btn.get_style_context().add_class("disconnect-button")
        elif state == "connecting":
            self._connect_btn.set_label("CONNECTING...")
            self._connect_btn.get_style_context().add_class("connect-button")
            self._connect_btn.set_sensitive(False)
        else:
            self._connect_btn.set_label("CONNECT")
            self._connect_btn.get_style_context().add_class("connect-button")
            self._connect_btn.set_sensitive(True)

        # Update info fields
        for key in ("gateway", "tunnel_ip", "uptime", "route"):
            if key in info:
                self._status_rows[key].set_text(str(info[key]))
            elif state == "disconnected":
                self._status_rows[key].set_text("—")

    def update_uptime(self, text: str):
        self._status_rows["uptime"].set_text(text)

    def append_log(self, text: str):
        """Append a line to the log view."""
        end = self._log_buffer.get_end_iter()
        self._log_buffer.insert(end, text + "\n")
        # Auto-scroll
        mark = self._log_buffer.get_insert()
        self._log_view.scroll_mark_onscreen(mark)

    def minimize_to_tray(self):
        """Hide window instead of destroying on close."""
        self.hide()
        return True  # Prevent default destroy
