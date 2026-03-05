"""SAML prelogin and WebKit2 login window.

Replaces gp-saml-gui with an MIT-licensed reimplementation
of the GlobalProtect SAML authentication flow.
"""

import logging
import ssl
import xml.etree.ElementTree as ET
from binascii import a2b_base64
from dataclasses import dataclass
from html.parser import HTMLParser
from operator import setitem
from urllib.parse import urlparse

import requests
import urllib3

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import Gtk, WebKit2, GLib

log = logging.getLogger("gp_split_saml")

COOKIE_FIELDS = ("prelogin-cookie", "portal-userauthcookie")
CLIENTOS_TO_OS = {"Linux": "linux-64", "Mac": "mac-intel", "Windows": "win"}


@dataclass
class SAMLResult:
    """Credentials returned from a successful SAML auth."""

    cookie: str
    cookie_name: str
    username: str
    server: str
    os: str


class CommentHtmlParser(HTMLParser):
    """Extract HTML comments that may contain SAML response XML."""

    def __init__(self):
        super().__init__()
        self.comments: list[str] = []

    def handle_comment(self, data: str) -> None:
        self.comments.append(data)


class TLSAdapter(requests.adapters.HTTPAdapter):
    """HTTPS adapter with legacy TLS compatibility.

    Enables weak ciphers (3DES, RC4) and unsafe renegotiation
    for older GlobalProtect servers.
    """

    def __init__(self, verify: bool = True):
        self._verify = verify
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.options |= 1 << 2  # OP_LEGACY_SERVER_CONNECT

        if not self._verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        self.poolmanager = urllib3.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
        )


class SAMLPrelogin:
    """Perform SAML prelogin to extract auth method and request."""

    def __init__(self, gateway: str, clientos: str = "Windows"):
        self.gateway = gateway
        self.clientos = clientos
        self._session = requests.Session()
        self._session.mount("https://", TLSAdapter(verify=False))
        self._session.headers["User-Agent"] = "PAN GlobalProtect"

    def execute(self) -> tuple[str, str | None, str | None]:
        """POST to prelogin endpoint, return (method, uri, html).

        Returns
        -------
        method : str
            "POST" or "REDIRECT"
        uri : str | None
            Redirect URI (if REDIRECT method)
        html : str | None
            HTML form content (if POST method)
        """
        endpoint = f"https://{self.gateway}/ssl-vpn/prelogin.esp"
        data = {
            "tmp": "tmp",
            "kerberos-support": "yes",
            "ipv6-support": "yes",
            "clientVer": 4100,
            "clientos": self.clientos,
        }

        log.info("SAML prelogin: %s", endpoint)
        res = self._session.post(endpoint, verify=False, data=data)
        xml = ET.fromstring(res.content)

        if xml.tag != "prelogin-response":
            raise RuntimeError("Not a GlobalProtect prelogin response")

        status = xml.find("status")
        if status is not None and status.text != "Success":
            msg = xml.find("msg")
            raise RuntimeError(f"Prelogin error: {msg.text if msg is not None else 'unknown'}")

        sam_el = xml.find("saml-auth-method")
        sr_el = xml.find("saml-request")
        if sam_el is None or sr_el is None:
            raise RuntimeError(
                "Prelogin response missing SAML tags. "
                "Try --clientos=Windows if using Linux."
            )

        method = sam_el.text
        decoded = a2b_base64(sr_el.text).decode()

        if method == "POST":
            return method, None, decoded
        elif method == "REDIRECT":
            return method, decoded, None
        else:
            raise RuntimeError(f"Unknown SAML method: {method}")


class SAMLLoginWindow:
    """WebKit2 browser window for interactive SAML login."""

    def __init__(self, uri: str | None, html: str | None):
        self.closed = False
        self.success = False
        self.saml_result: dict[str, str] = {}
        self._result: SAMLResult | None = None

        ctx = WebKit2.WebContext.get_default()
        ctx.set_tls_errors_policy(WebKit2.TLSErrorsPolicy.IGNORE)

        self.wview = WebKit2.WebView()
        settings = self.wview.get_settings()
        settings.set_user_agent("PAN GlobalProtect")
        self.wview.set_settings(settings)

        self.window = Gtk.Window()
        self.window.set_title("SAML Login")
        self.window.set_default_size(500, 660)
        self.window.add(self.wview)
        self.window.show_all()

        self.window.connect("delete-event", self._on_close)
        self.wview.connect("load-changed", self._on_load_changed)

        if html:
            self.wview.load_html(html, uri or "")
        elif uri:
            self.wview.load_uri(uri)

    def run(self) -> SAMLResult | None:
        """Run nested main loop, return result or None if cancelled."""
        self._loop = GLib.MainLoop()
        self._loop.run()
        return self._result

    def _on_close(self, window, event):
        self.closed = True
        self._loop.quit()

    def _on_load_changed(self, webview, event):
        if self.success:
            return
        if event != WebKit2.LoadEvent.FINISHED:
            return

        mr = webview.get_main_resource()
        rs = mr.get_response()
        if not rs:
            return

        h = rs.get_http_headers()
        if not h:
            return

        # Collect headers into dict
        d: dict[str, str] = {}
        h.foreach(lambda k, v: setitem(d, k.lower(), v))

        # Filter SAML-relevant headers
        fd = {
            name: v
            for name, v in d.items()
            if name.startswith("saml-") or name in COOKIE_FIELDS
        }

        if fd:
            log.debug("SAML headers: %s", fd)
            self.saml_result.update(fd, server=urlparse(mr.get_uri()).netloc)
            if self._check_done():
                return

        # Fallback: parse HTML comments for SAML data
        mr.get_data(None, self._response_callback)

    def _response_callback(self, resource, result):
        try:
            data = resource.get_data_finish(result)
        except GLib.Error:
            return

        content = data.decode("utf-8", errors="replace")
        parser = CommentHtmlParser()
        parser.feed(content)

        fd: dict[str, str] = {}
        for comment in parser.comments:
            try:
                root = ET.fromstring(f"<root>{comment}</root>")
                for elem in root:
                    if elem.tag.startswith("saml-") or elem.tag in COOKIE_FIELDS:
                        fd[elem.tag] = elem.text or ""
            except ET.ParseError:
                pass

        if fd:
            log.debug("SAML comment tags: %s", fd)
            self.saml_result.update(fd, server=urlparse(resource.get_uri()).netloc)

        if not self._check_done():
            GLib.timeout_add(1000, self._check_done)

    def _check_done(self) -> bool:
        if self.success:
            return True
        d = self.saml_result
        if "saml-username" in d and (
            "prelogin-cookie" in d or "portal-userauthcookie" in d
        ):
            # Determine cookie name and value
            for cn in COOKIE_FIELDS:
                cv = d.get(cn)
                if cv:
                    break
            else:
                return False

            os_code = CLIENTOS_TO_OS.get("Windows", "win")
            self._result = SAMLResult(
                cookie=cv,
                cookie_name=cn,
                username=d["saml-username"],
                server=d.get("server", ""),
                os=os_code,
            )
            self.success = True
            log.info("SAML auth successful for %s", self._result.username)
            self.window.close()
            self._loop.quit()
            return True
        return False


def perform_saml_auth(gateway: str, clientos: str = "Windows") -> SAMLResult:
    """Full SAML auth flow: prelogin + interactive browser login.

    Must be called from the GTK main thread.
    """
    prelogin = SAMLPrelogin(gateway, clientos)
    method, uri, html = prelogin.execute()
    log.info("SAML method: %s", method)

    login_window = SAMLLoginWindow(uri, html)
    result = login_window.run()

    if result is None:
        if login_window.closed:
            raise RuntimeError("Login window closed by user")
        raise RuntimeError("SAML login failed — no credentials received")

    return result
