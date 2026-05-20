"""GPSplitSAMLApp — GtkApplication orchestrator."""

import json
import logging
import signal
import socket
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

# Tunnel liveness probe — TCP-connect to the VPN DNS server every 10s. A
# completed handshake OR a RST (ConnectionRefused) both prove the tunnel
# passes packets; only a timeout means the data path is dead. 3 consecutive
# failures (~30s) triggers a silent auto-reconnect.
_PROBE_PORT = 53
_PROBE_TIMEOUT = 4
_PROBE_FAIL_THRESHOLD = 3
_SILENT_SAML_TIMEOUT = 30


def _probe_tunnel(host: str) -> bool:
    """End-to-end liveness probe via TCP to ``host:53``. Off-thread safe.

    Returns True (alive) if the handshake completes or the peer sends RST,
    False (dead) on timeout or unreachable. Empty host returns True so the
    feature degrades when vpn_dns is unconfigured.
    """
    if not host:
        return True
    try:
        with socket.create_connection((host, _PROBE_PORT), timeout=_PROBE_TIMEOUT):
            return True
    except ConnectionRefusedError:
        return True
    except (socket.timeout, OSError):
        return False


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
        # Tunnel-probe / auto-reconnect state (all mutated on the GTK main thread).
        self._probe_in_flight = False
        self._probe_failures = 0
        self._reconnect_pending = False
        self._reconnecting = False
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

    def _do_connect(self, silent: bool = False):
        """Start VPN connection — SAML on main thread, VPN in background.

        silent=True is used by the auto-reconnect path: SAML runs without a
        visible window (succeeds only if the IdP session is still valid) and
        notifications are suppressed except on failure.
        """
        if self._vpn.is_running:
            return

        self._window.set_state("connecting")
        self._tray.set_state("connecting")

        # SAML auth needs GTK main loop
        try:
            log.info("Starting SAML authentication... (silent=%s)", silent)
            result = perform_saml_auth(
                self._config.gateway,
                clientos="Windows",
                silent=silent,
                timeout=_SILENT_SAML_TIMEOUT if silent else None,
            )
        except Exception as e:
            log.error("SAML auth failed: %s", e)
            was_reconnect = self._reconnecting
            self._reconnecting = False
            self._reconnect_pending = False
            if was_reconnect:
                notify_error("VPN reconnect failed — click Reconnect")
            else:
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
        was_reconnect = self._reconnecting
        # Reconnect cycle (if any) completes successfully here.
        self._reconnecting = False
        self._reconnect_pending = False
        self._probe_failures = 0
        self._probe_in_flight = False

        if was_reconnect:
            log.info("VPN reconnected silently")
        else:
            log.info("VPN connected successfully")
        self._window.set_state(
            "connected",
            gateway=self._config.gateway,
            route=self._config.vpn_internal_route,
            tunnel_ip=tunnel_ip,
        )
        self._tray.set_state("connected")
        # Silent reconnect: no popup — user asked for icon-only feedback.
        if not was_reconnect:
            notify_connected(self._config.gateway)

        # Start health monitor (10s interval)
        self._health_timer = GLib.timeout_add_seconds(10, self._health_check)
        # Start uptime counter (1s interval)
        self._uptime_timer = GLib.timeout_add_seconds(1, self._update_uptime)

    def _on_connect_error(self, error_msg: str):
        was_reconnect = self._reconnecting
        self._reconnecting = False
        self._reconnect_pending = False
        self._window.set_state("error")
        self._tray.set_state("disconnected")
        if was_reconnect:
            notify_error("VPN reconnect failed — click Reconnect")
        else:
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
        """Disconnect VPN and restore network state.

        Called for both user-initiated disconnects and the teardown step of an
        auto-reconnect cycle. The two are distinguished by ``_reconnect_pending``:
        when False here, treat the click as user intent and cancel any queued
        reconnect so a stale ``_resume_reconnect`` becomes a no-op.
        """
        if not self._reconnect_pending:
            # User-initiated: override any in-flight reconnect cycle.
            self._reconnecting = False
        if self._disconnecting:
            return
        if not self._vpn.is_running and self._vpn.pid is None:
            return

        self._disconnecting = True
        self._stop_timers()
        self._window.set_state("disconnecting")
        self._tray.set_state("disconnecting")

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
        # Mid-reconnect teardown is internal — don't fire a popup.
        if not self._reconnect_pending:
            notify_disconnected()

        if self._reconnect_pending:
            self._reconnect_pending = False
            log.info("Teardown complete — resuming silent reconnect")
            # Schedule on the next main-loop iteration so the nested SAML loop
            # in _do_connect doesn't start inside this idle_add callback.
            GLib.idle_add(self._resume_reconnect)

    # ------------------------------------------------------------------ #
    # Timers / health                                                      #
    # ------------------------------------------------------------------ #

    def _health_check(self) -> bool:
        """Periodic check: openconnect alive AND tunnel actually passes packets."""
        # Belt-and-suspenders: the timer is removed by _stop_timers during a
        # disconnect/reconnect, but a stale tick could land here in the gap.
        if self._disconnecting or self._reconnecting:
            return True

        if not self._vpn.is_running:
            log.warning("openconnect process died unexpectedly")
            self._start_reconnect("openconnect process died")
            # Stop this timer — _on_connected starts a fresh one on success.
            return False

        if self._probe_in_flight:
            log.debug("Skipping probe tick — previous probe still running")
            return True

        self._probe_in_flight = True
        host = self._config.vpn_dns if self._config else ""
        threading.Thread(
            target=self._probe_background, args=(host,), daemon=True,
        ).start()
        return True

    def _probe_background(self, host: str) -> None:
        """Background thread — runs the socket probe, delivers verdict to main."""
        try:
            alive = _probe_tunnel(host)
        except Exception as e:
            # _probe_tunnel already catches the expected failures; anything
            # else is a programming error — treat conservatively as ALIVE so
            # a bug here cannot trigger a false teardown.
            log.error("Probe raised unexpectedly (treating as alive): %s", e)
            alive = True
        GLib.idle_add(self._on_probe_result, alive)

    def _on_probe_result(self, alive: bool) -> bool:
        """Main thread — interpret the probe verdict."""
        self._probe_in_flight = False

        # Drop stale verdicts: world may have changed during the 4s probe.
        if self._disconnecting or self._reconnecting or not self._vpn.is_running:
            return False

        if alive:
            if self._probe_failures:
                log.info("Tunnel probe recovered after %d failure(s)", self._probe_failures)
            self._probe_failures = 0
            return False

        self._probe_failures += 1
        log.warning(
            "Tunnel liveness probe failed (%d/%d)",
            self._probe_failures, _PROBE_FAIL_THRESHOLD,
        )
        if self._probe_failures >= _PROBE_FAIL_THRESHOLD:
            self._start_reconnect("tunnel unresponsive")
        return False  # one-shot idle_add

    def _start_reconnect(self, reason: str) -> None:
        """Main-thread entry point for an auto-reconnect cycle (silent)."""
        if self._reconnecting:
            return  # already underway
        if self._disconnecting:
            return  # user disconnect wins
        log.warning("Auto-reconnect triggered: %s", reason)
        self._reconnecting = True
        self._probe_failures = 0
        self._reconnect_pending = True
        # Per the user's spec: tray icon reflects the state, no popup yet.
        self._do_disconnect()

    def _resume_reconnect(self) -> bool:
        """Main thread — called via idle_add after teardown completes."""
        if not self._reconnecting:
            # User cancelled during teardown; abandon the reconnect.
            log.info("Reconnect cancelled before resume")
            return False
        self._do_connect(silent=True)
        return False  # one-shot

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
