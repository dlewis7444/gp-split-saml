"""GPSplitSAMLApp — GtkApplication orchestrator."""

import json
import logging
import signal
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gio

from gp_split_saml.config import load_config, VPNConfig
from gp_split_saml.log import UIHandler, setup_logging
from gp_split_saml.saml import perform_saml_auth
from gp_split_saml.vpn import VPNConnection
from gp_split_saml.network import NetworkState, NetworkManager, get_tunnel_ip, cleanup_stale_routes
from gp_split_saml.window import MainWindow
from gp_split_saml.tray import TrayIcon
from gp_split_saml.notify import notify_connected, notify_disconnected, notify_error
from gp_split_saml.cookies import store_cookie, load_cookie
from gp_split_saml.theme import load_css

log = logging.getLogger("gp_split_saml")

_STATE_FILE = Path.home() / ".local" / "share" / "gp-split-saml" / "vpn-state.json"


class GPSplitSAMLApp(Gtk.Application):
    """Main application orchestrator."""

    def __init__(self):
        super().__init__(
            application_id="com.github.dlewis7444.gp-split-saml",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self._window: MainWindow | None = None
        self._tray: TrayIcon | None = None
        self._vpn = VPNConnection()
        self._net_state: NetworkState | None = None
        self._net_mgr: NetworkManager | None = None
        self._config: VPNConfig | None = None
        self._health_timer: int | None = None
        self._uptime_timer: int | None = None
        self._connect_time: float | None = None
        self._disconnecting = False
        self._ui_handler = UIHandler()
        self._logger = setup_logging(self._ui_handler)

    def do_activate(self):
        load_css()

        try:
            self._config = load_config()
        except FileNotFoundError as e:
            log.error("%s", e)
            dialog = Gtk.MessageDialog(
                transient_for=None,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Configuration Error",
            )
            dialog.format_secondary_text(str(e))
            dialog.run()
            dialog.destroy()
            return

        self._recover_stale_vpn()

        self._window = MainWindow(
            self, self._do_connect, self._do_disconnect,
            self._do_quit, self._on_config_change, self._config,
        )
        self._window.connect("delete-event", self._on_window_close)
        self._ui_handler.set_callback(self._log_to_ui)
        self._window.show_all()

        self._tray = TrayIcon(
            on_connect=self._do_connect,
            on_disconnect=self._do_disconnect,
            on_show=self._show_window,
            on_quit=self._do_quit,
        )
        self._tray.set_state("disconnected")

        # Signal handlers for clean disconnect
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_signal)

        log.info("gp-split-saml started — gateway: %s", self._config.gateway)

    def _log_to_ui(self, msg: str):
        GLib.idle_add(self._window.append_log, msg)

    def _on_window_close(self, window, event):
        window.hide()
        return True  # Always minimize to tray

    def _show_window(self):
        if self._window:
            self._window.show_all()
            self._window.present()

    # ------------------------------------------------------------------ #
    # State file — persists session across crashes for recovery on restart #
    # ------------------------------------------------------------------ #

    def _write_state(self) -> None:
        """Write VPN session state to disk for crash recovery."""
        if not self._net_state:
            return
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps({
            "default_gateway": self._net_state.default_gateway,
            "default_device": self._net_state.default_device,
        }))
        log.debug("VPN state written to %s", _STATE_FILE)

    def _clear_state(self) -> None:
        try:
            _STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def _recover_stale_vpn(self) -> None:
        """On startup, clean up any VPN session left over from a previous crash."""
        if not _STATE_FILE.exists():
            return

        try:
            state = json.loads(_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read stale VPN state: %s", e)
            self._clear_state()
            return

        gateway = state.get("default_gateway", "")
        device = state.get("default_device", "")

        log.info("Stale VPN session detected — cleaning up...")

        # Find and terminate any running openconnect process
        result = subprocess.run(
            ["pgrep", "-x", "openconnect"], capture_output=True, text=True
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]

        if pids:
            log.info("Sending SIGTERM to openconnect PID(s): %s", ", ".join(pids))
            for pid in pids:
                subprocess.run(["sudo", "kill", pid], check=False, timeout=5)

            # Wait up to 15s for openconnect to run its own vpnc-script cleanup
            for _ in range(15):
                time.sleep(1)
                result = subprocess.run(
                    ["pgrep", "-x", "openconnect"], capture_output=True, text=True
                )
                if not result.stdout.strip():
                    log.info("openconnect exited cleanly")
                    break
            else:
                log.warning("openconnect did not exit after SIGTERM — force-killing")
                for pid in pids:
                    subprocess.run(["sudo", "kill", "-9", pid], check=False, timeout=5)
                time.sleep(1)

        # Clean up any routes openconnect left behind (covers SIGKILL case)
        if gateway and device:
            cleanup_stale_routes(gateway, device)

        self._clear_state()
        log.info("Stale VPN session cleaned up")

    # ------------------------------------------------------------------ #
    # Signal handling                                                      #
    # ------------------------------------------------------------------ #

    def _on_signal(self):
        """SIGINT/SIGTERM — runs on the GLib main loop, so blocking is safe."""
        log.info("Signal received, cleaning up...")
        self._stop_timers()
        if self._vpn.is_running:
            self._vpn.disconnect()
            if self._net_mgr and self._config:
                self._net_mgr.cleanup(self._config.vpn_internal_route)
        self._clear_state()
        self.quit()
        return False

    # ------------------------------------------------------------------ #
    # Connect flow                                                         #
    # ------------------------------------------------------------------ #

    def _do_connect(self):
        """Start VPN connection — SAML on main thread, VPN in background."""
        if self._vpn.is_running:
            return

        self._window.set_state("connecting")
        self._tray.set_state("connecting")

        # SAML auth needs GTK main loop
        try:
            log.info("Starting SAML authentication...")
            result = perform_saml_auth(self._config.gateway, clientos="Windows")
        except Exception as e:
            log.error("SAML auth failed: %s", e)
            notify_error(f"SAML auth failed: {e}")
            self._window.set_state("error")
            self._tray.set_state("disconnected")
            return

        # Store cookie for potential reuse
        store_cookie(
            self._config.gateway,
            result.cookie,
            result.cookie_name,
            result.username,
        )

        # VPN connect in background thread
        thread = threading.Thread(
            target=self._connect_background,
            args=(result,),
            daemon=True,
        )
        thread.start()

    def _connect_background(self, saml_result):
        """Background thread: capture state, connect, configure routes."""
        try:
            # Capture network state
            self._net_state = NetworkState()
            self._net_state.capture()
            self._net_mgr = NetworkManager(self._net_state)

            # Build usergroup
            usergroup = f"gateway:{saml_result.cookie_name}"

            # Launch openconnect
            self._vpn.connect(
                server=saml_result.server,
                cookie=saml_result.cookie,
                username=saml_result.username,
                os_flag=saml_result.os,
                usergroup=usergroup,
            )

            # Wait for tunnel
            if not self._vpn.wait_for_tunnel():
                raise RuntimeError("Tunnel interface did not appear")

            # Configure routes and DNS
            self._net_mgr.setup_routes(self._config.vpn_internal_route)
            self._net_mgr.setup_dns(
                self._config.vpn_dns,
                self._config.vpn_domains,
                self._config.home_dns,
                self._config.home_domain,
            )

            self._connect_time = time.time()
            tunnel_ip = get_tunnel_ip()

            # Persist session state for crash recovery
            self._write_state()

            # Update UI on main thread
            GLib.idle_add(self._on_connected, tunnel_ip)

        except Exception as e:
            log.error("Connection failed: %s", e)
            GLib.idle_add(self._on_connect_error, str(e))

    def _on_connected(self, tunnel_ip: str = ""):
        """Called on main thread after successful connection."""
        log.info("VPN connected successfully")
        self._window.set_state(
            "connected",
            gateway=self._config.gateway,
            route=self._config.vpn_internal_route,
            tunnel_ip=tunnel_ip,
        )
        self._tray.set_state("connected")
        notify_connected(self._config.gateway)

        # Start health monitor (10s interval)
        self._health_timer = GLib.timeout_add_seconds(10, self._health_check)
        # Start uptime counter (1s interval)
        self._uptime_timer = GLib.timeout_add_seconds(1, self._update_uptime)

    def _on_connect_error(self, error_msg: str):
        self._window.set_state("error")
        self._tray.set_state("disconnected")
        notify_error(error_msg)
        # Clean up partial connection
        if self._vpn.is_running:
            self._vpn.disconnect()

    # ------------------------------------------------------------------ #
    # Disconnect flow                                                      #
    # ------------------------------------------------------------------ #

    def _stop_timers(self) -> None:
        if self._health_timer:
            GLib.source_remove(self._health_timer)
            self._health_timer = None
        if self._uptime_timer:
            GLib.source_remove(self._uptime_timer)
            self._uptime_timer = None

    def _do_disconnect(self):
        """Disconnect VPN and restore network state."""
        if self._disconnecting:
            return
        if not self._vpn.is_running and self._vpn.pid is None:
            return

        self._disconnecting = True
        self._stop_timers()
        self._window.set_state("connecting")  # Show transitional state

        thread = threading.Thread(target=self._disconnect_background, daemon=True)
        thread.start()

    def _disconnect_background(self):
        try:
            self._vpn.disconnect()

            if self._net_mgr:
                self._net_mgr.cleanup(self._config.vpn_internal_route)

            GLib.idle_add(self._on_disconnected)
        except Exception as e:
            log.error("Disconnect error: %s", e)
            GLib.idle_add(self._on_disconnected)

    def _on_disconnected(self):
        log.info("VPN disconnected, network restored")
        self._disconnecting = False
        self._clear_state()
        self._window.set_state("disconnected")
        self._tray.set_state("disconnected")
        self._connect_time = None
        notify_disconnected()

    # ------------------------------------------------------------------ #
    # Timers / health                                                      #
    # ------------------------------------------------------------------ #

    def _health_check(self) -> bool:
        """Periodic check that openconnect is still running."""
        if not self._vpn.is_running:
            log.warning("openconnect process died unexpectedly")
            self._do_disconnect()
            notify_error("VPN connection lost")
            return False  # Stop timer
        return True  # Continue

    def _update_uptime(self) -> bool:
        if self._connect_time is None:
            return False
        elapsed = int(time.time() - self._connect_time)
        hours, rem = divmod(elapsed, 3600)
        minutes, seconds = divmod(rem, 60)
        self._window.update_uptime(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        return True

    # ------------------------------------------------------------------ #
    # Misc                                                                 #
    # ------------------------------------------------------------------ #

    def _on_config_change(self, config):
        """Accept session-only config edits from the UI."""
        self._config = config
        log.info(
            "Session config updated — gateway: %s  route: %s  dns: %s",
            config.gateway, config.vpn_internal_route, config.vpn_dns,
        )

    def _do_quit(self):
        if self._vpn.is_running:
            self._do_disconnect()
            # Give disconnect thread a moment to finish before GTK exits
            GLib.timeout_add(2000, self.quit)
        else:
            self.quit()
