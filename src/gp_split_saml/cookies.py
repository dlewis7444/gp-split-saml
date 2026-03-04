"""SAML cookie storage — GNOME Keyring primary, file fallback."""

import json
import logging
from pathlib import Path

log = logging.getLogger("gp_split_saml")

FALLBACK_DIR = Path.home() / ".local" / "share" / "gp-split-saml"
FALLBACK_FILE = FALLBACK_DIR / "cookies.txt"

KEYRING_SCHEMA_ATTRS = {
    "application": "gp-split-saml",
    "type": "saml-cookie",
}


def _try_libsecret():
    """Try to import and use libsecret."""
    try:
        import gi
        gi.require_version("Secret", "1")
        from gi.repository import Secret
        return Secret
    except (ImportError, ValueError):
        return None


def store_cookie(gateway: str, cookie: str, cookie_name: str, username: str) -> None:
    """Store SAML cookie, preferring GNOME Keyring."""
    Secret = _try_libsecret()
    if Secret:
        try:
            schema = Secret.Schema.new(
                "com.github.dlewis7444.gp-split-saml",
                Secret.SchemaFlags.NONE,
                {
                    "application": Secret.SchemaAttributeType.STRING,
                    "type": Secret.SchemaAttributeType.STRING,
                    "gateway": Secret.SchemaAttributeType.STRING,
                },
            )
            data = json.dumps({
                "cookie": cookie,
                "cookie_name": cookie_name,
                "username": username,
            })
            attrs = {**KEYRING_SCHEMA_ATTRS, "gateway": gateway}
            Secret.password_store_sync(
                schema, attrs, Secret.COLLECTION_DEFAULT,
                f"gp-split-saml: {gateway}", data, None,
            )
            log.debug("Cookie stored in GNOME Keyring")
            return
        except Exception as e:
            log.debug("Keyring store failed, using file fallback: %s", e)

    # File fallback
    FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
    data = json.dumps({
        "gateway": gateway,
        "cookie": cookie,
        "cookie_name": cookie_name,
        "username": username,
    })
    FALLBACK_FILE.write_text(data)
    FALLBACK_FILE.chmod(0o600)
    log.debug("Cookie stored in %s", FALLBACK_FILE)


def load_cookie(gateway: str) -> dict | None:
    """Load stored cookie for gateway. Returns dict or None."""
    Secret = _try_libsecret()
    if Secret:
        try:
            schema = Secret.Schema.new(
                "com.github.dlewis7444.gp-split-saml",
                Secret.SchemaFlags.NONE,
                {
                    "application": Secret.SchemaAttributeType.STRING,
                    "type": Secret.SchemaAttributeType.STRING,
                    "gateway": Secret.SchemaAttributeType.STRING,
                },
            )
            attrs = {**KEYRING_SCHEMA_ATTRS, "gateway": gateway}
            secret = Secret.password_lookup_sync(schema, attrs, None)
            if secret:
                log.debug("Cookie loaded from GNOME Keyring")
                return json.loads(secret)
        except Exception as e:
            log.debug("Keyring lookup failed: %s", e)

    # File fallback
    if FALLBACK_FILE.exists():
        try:
            data = json.loads(FALLBACK_FILE.read_text())
            if data.get("gateway") == gateway:
                log.debug("Cookie loaded from %s", FALLBACK_FILE)
                return data
        except (json.JSONDecodeError, OSError) as e:
            log.debug("File cookie load failed: %s", e)

    return None


def clear_cookie(gateway: str) -> None:
    """Remove stored cookie for gateway."""
    Secret = _try_libsecret()
    if Secret:
        try:
            schema = Secret.Schema.new(
                "com.github.dlewis7444.gp-split-saml",
                Secret.SchemaFlags.NONE,
                {
                    "application": Secret.SchemaAttributeType.STRING,
                    "type": Secret.SchemaAttributeType.STRING,
                    "gateway": Secret.SchemaAttributeType.STRING,
                },
            )
            attrs = {**KEYRING_SCHEMA_ATTRS, "gateway": gateway}
            Secret.password_clear_sync(schema, attrs, None)
        except Exception:
            pass

    if FALLBACK_FILE.exists():
        try:
            FALLBACK_FILE.unlink()
        except OSError:
            pass
