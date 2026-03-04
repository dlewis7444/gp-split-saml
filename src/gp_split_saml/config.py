"""VPN configuration from .env files."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VPNConfig:
    """VPN connection configuration parsed from .env file."""

    gateway: str = ""
    vpn_dns: str = ""
    vpn_domains: str = ""
    vpn_internal_route: str = "10.0.0.0/8"
    home_dns: str = ""
    home_domain: str = ""

    @property
    def vpn_domain_list(self) -> list[str]:
        return self.vpn_domains.split() if self.vpn_domains else []


def _find_env_file() -> Path | None:
    """Search for .env file in priority order."""
    candidates = []

    env_override = os.environ.get("GP_SPLIT_SAML_ENV")
    if env_override:
        candidates.append(Path(env_override))

    candidates.extend([
        Path.cwd() / ".env",
        Path.home() / ".config" / "gp-split-saml" / ".env",
        Path.home() / "VPN" / ".env",
    ])

    for p in candidates:
        if p.is_file():
            return p
    return None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE .env file, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_config(env_path: Path | None = None) -> VPNConfig:
    """Load VPN configuration from .env file."""
    path = env_path or _find_env_file()
    if path is None:
        raise FileNotFoundError(
            "No .env file found. Searched:\n"
            "  $GP_SPLIT_SAML_ENV\n"
            "  ./.env\n"
            "  ~/.config/gp-split-saml/.env\n"
            "  ~/VPN/.env"
        )

    env = _parse_env_file(path)

    return VPNConfig(
        gateway=env.get("VPN_GATEWAY", ""),
        vpn_dns=env.get("VPN_DNS", ""),
        vpn_domains=env.get("VPN_DOMAINS", ""),
        vpn_internal_route=env.get("VPN_INTERNAL_ROUTE", "10.0.0.0/8"),
        home_dns=env.get("HOME_DNS", ""),
        home_domain=env.get("HOME_DOMAIN", ""),
    )
