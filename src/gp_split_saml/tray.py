"""AppIndicator3 tray icon."""

import logging

log = logging.getLogger("gp_split_saml")

_indicator = None


def _try_appindicator():
    try:
        import gi
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3
        return AppIndicator3
    except (ImportError, ValueError):
        return None


class TrayIcon:
    """System tray icon with connect/disconnect menu."""

    def __init__(self, on_connect, on_disconnect, on_show, on_quit):
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_show = on_show
        self._on_quit = on_quit
        self._indicator = None
        self._connect_item = None
        self._disconnect_item = None

        AppIndicator3 = _try_appindicator()
        if AppIndicator3 is None:
            log.warning("AppIndicator3 not available, tray icon disabled")
            return

        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        self._indicator = AppIndicator3.Indicator.new(
            "gp-split-saml",
            "network-vpn-disconnected",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self._connect_item = Gtk.MenuItem(label="Connect")
        self._connect_item.connect("activate", lambda _: self._on_connect())
        menu.append(self._connect_item)

        self._disconnect_item = Gtk.MenuItem(label="Disconnect")
        self._disconnect_item.connect("activate", lambda _: self._on_disconnect())
        self._disconnect_item.set_sensitive(False)
        menu.append(self._disconnect_item)

        menu.append(Gtk.SeparatorMenuItem())

        show_item = Gtk.MenuItem(label="Show Window")
        show_item.connect("activate", lambda _: self._on_show())
        menu.append(show_item)

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: self._on_quit())
        menu.append(quit_item)

        menu.show_all()
        self._indicator.set_menu(menu)

    def set_state(self, state: str):
        """Update tray icon: 'connected', 'disconnected', or 'connecting'."""
        if self._indicator is None:
            return

        icons = {
            "connected": "network-vpn",
            "disconnected": "network-vpn-disconnected",
            "connecting": "network-vpn-acquiring",
        }
        self._indicator.set_icon_full(
            icons.get(state, "network-vpn-disconnected"), state
        )

        if self._connect_item and self._disconnect_item:
            is_connected = state == "connected"
            self._connect_item.set_sensitive(not is_connected)
            self._disconnect_item.set_sensitive(is_connected)
