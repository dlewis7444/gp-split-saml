"""Route and DNS manipulation for split tunneling.

Mirrors the logic in start_swa_gp.sh for route/DNS setup and cleanup.
"""

import logging
import re
import subprocess

log = logging.getLogger("gp_split_saml")

TUN_DEVICE = "tun0"


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run a command, logging it."""
    log.debug("$ %s", " ".join(cmd))
    return subprocess.run(
        cmd, capture_output=True, text=True, check=check, timeout=10
    )


def _sudo(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return _run(["sudo"] + cmd, check=check)


class NetworkState:
    """Captured network state before VPN connection."""

    def __init__(self):
        self.main_interface: str = ""
        self.default_gateway: str = ""
        self.default_device: str = ""
        self.default_route: str = ""
        self.orig_dns: str = ""
        self.orig_domain: str = ""

    def capture(self) -> None:
        """Capture current network state."""
        result = _run(["ip", "route", "show", "default"])
        lines = result.stdout.strip().splitlines()
        if not lines:
            raise RuntimeError("No default route found")

        first = lines[0]
        parts = first.split()

        # Parse: default via <gw> dev <dev> ...
        try:
            self.default_gateway = parts[parts.index("via") + 1]
        except (ValueError, IndexError):
            raise RuntimeError(f"Cannot parse gateway from: {first}")

        try:
            self.default_device = parts[parts.index("dev") + 1]
        except (ValueError, IndexError):
            raise RuntimeError(f"Cannot parse device from: {first}")

        self.main_interface = self.default_device
        self.default_route = first

        # Capture DNS
        result = _run(["resolvectl", "dns", self.main_interface])
        match = re.search(r":\s*(.+)", result.stdout)
        if match and "n/a" not in match.group(1):
            self.orig_dns = match.group(1).strip()

        # Capture domain
        result = _run(["resolvectl", "domain", self.main_interface])
        match = re.search(r":\s*(.+)", result.stdout)
        if match and "n/a" not in match.group(1):
            self.orig_domain = match.group(1).strip()

        log.info(
            "Network state: gw=%s dev=%s dns=%s domain=%s",
            self.default_gateway,
            self.default_device,
            self.orig_dns or "(auto)",
            self.orig_domain or "(auto)",
        )

        # Handle multiple default routes
        if len(lines) > 1:
            log.warning("Multiple default routes detected, removing extras")
            for extra in lines[1:]:
                _sudo(["ip", "route", "del"] + extra.split())


class NetworkManager:
    """Manage routes and DNS for split tunneling."""

    def __init__(self, state: NetworkState):
        self.state = state

    def setup_routes(self, vpn_route: str) -> None:
        """Configure split tunnel routes after tun0 is up."""
        gw = self.state.default_gateway
        dev = self.state.default_device

        # Delete all default routes
        result = _run(["ip", "route"])
        for line in result.stdout.splitlines():
            if line.startswith("default"):
                _sudo(["ip", "route", "del"] + line.split())

        # Restore home default route
        _sudo(["ip", "route", "add", "default", "via", gw, "dev", dev], check=False)

        # Add VPN internal route
        _sudo(["ip", "route", "add", vpn_route, "dev", TUN_DEVICE], check=False)

        log.info("Routes configured: default via %s, %s via %s", gw, vpn_route, TUN_DEVICE)

    def setup_dns(
        self,
        vpn_dns: str,
        vpn_domains: str,
        home_dns: str,
        home_domain: str,
    ) -> None:
        """Configure split DNS via resolvectl."""
        if vpn_dns:
            _sudo(["resolvectl", "dns", TUN_DEVICE] + vpn_dns.split())
        if vpn_domains:
            _sudo(["resolvectl", "domain", TUN_DEVICE] + vpn_domains.split())

        # Restore home DNS on main interface
        iface = self.state.main_interface
        effective_dns = home_dns or self.state.orig_dns
        effective_domain = home_domain or self.state.orig_domain

        if effective_dns:
            _sudo(["resolvectl", "dns", iface] + effective_dns.split())
        if effective_domain:
            _sudo(["resolvectl", "domain", iface] + effective_domain.split())

        log.info("DNS configured: tun0=%s, %s=%s", vpn_dns, iface, effective_dns)

    def cleanup(self, vpn_route: str) -> None:
        """Restore network state — mirrors shell script cleanup trap."""
        gw = self.state.default_gateway
        dev = self.state.default_device

        # Remove VPN route
        _sudo(["ip", "route", "del", vpn_route, "dev", TUN_DEVICE])

        # Remove proto-less routes via default gateway
        result = _run(["ip", "route", "show", "via", gw])
        for line in result.stdout.splitlines():
            if "proto " not in line:
                _sudo(["ip", "route", "del"] + line.split())

        # Fallback default route if NM hasn't restored
        result = _run(["ip", "route", "show", "default"])
        if not result.stdout.strip():
            _sudo(["ip", "route", "add", "default", "via", gw, "dev", dev])

        # Revert tun0 DNS
        _sudo(["resolvectl", "revert", TUN_DEVICE])

        # Restore main interface DNS
        iface = self.state.main_interface
        if self.state.orig_dns:
            _sudo(["resolvectl", "dns", iface] + self.state.orig_dns.split())
        if self.state.orig_domain:
            _sudo(["resolvectl", "domain", iface] + self.state.orig_domain.split())

        log.info("Network state restored")
