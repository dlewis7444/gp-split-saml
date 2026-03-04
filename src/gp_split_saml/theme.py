"""CSS theme provider loader."""

import logging
from importlib import resources

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk

log = logging.getLogger("gp_split_saml")


def load_css():
    """Load the vivacious theme CSS into the default screen."""
    provider = Gtk.CssProvider()

    ref = resources.files("gp_split_saml") / "data" / "style.css"
    css_data = ref.read_text(encoding="utf-8")
    provider.load_from_data(css_data.encode())

    screen = Gdk.Screen.get_default()
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    log.debug("Theme CSS loaded")
