"""Desktop notifications via libnotify."""

import logging

log = logging.getLogger("gp_split_saml")

_initialized = False


def _ensure_init():
    global _initialized
    if _initialized:
        return True
    try:
        import gi
        gi.require_version("Notify", "0.7")
        from gi.repository import Notify
        Notify.init("gp-split-saml")
        _initialized = True
        return True
    except (ImportError, ValueError) as e:
        log.debug("libnotify unavailable: %s", e)
        return False


def _send(summary: str, body: str = "", icon: str = "network-vpn"):
    if not _ensure_init():
        return
    try:
        from gi.repository import Notify
        n = Notify.Notification.new(summary, body, icon)
        n.show()
    except Exception as e:
        log.debug("Notification failed: %s", e)


def notify_connected(gateway: str):
    _send("VPN Connected", f"Connected to {gateway}", "network-vpn")


def notify_disconnected():
    _send("VPN Disconnected", "Split tunnel routes restored", "network-offline")


def notify_error(message: str):
    _send("VPN Error", message, "dialog-error")
