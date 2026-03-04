"""openconnect subprocess manager."""

import logging
import os
import signal
import subprocess
import time
from importlib import resources
from pathlib import Path

log = logging.getLogger("gp_split_saml")

TUN_DEVICE = "tun0"
TUN_WAIT_SECONDS = 30
TUN_STABILIZE_SECONDS = 3


def _hipreport_path() -> str:
    """Locate the bundled hipreport.sh script."""
    ref = resources.files("gp_split_saml") / "data" / "hipreport.sh"
    with resources.as_file(ref) as p:
        path = str(p)
    # Ensure executable
    os.chmod(path, 0o755)
    return path


class VPNConnection:
    """Manage an openconnect GlobalProtect VPN subprocess."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._pid: int | None = None

    @property
    def pid(self) -> int | None:
        return self._pid

    @property
    def is_running(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def connect(
        self,
        server: str,
        cookie: str,
        username: str,
        os_flag: str = "win",
        usergroup: str = "gateway:prelogin-cookie",
    ) -> None:
        """Launch openconnect with SAML cookie."""
        if self.is_running:
            raise RuntimeError("VPN already connected")

        hip = _hipreport_path()

        cmd = [
            "sudo", "openconnect",
            "--protocol=gp",
            f"--user={username}",
            f"--os={os_flag}",
            f"--usergroup={usergroup}",
            "--passwd-on-stdin",
            f"--csd-wrapper={hip}",
            "-v",
            server,
        ]

        log.info("Starting openconnect: %s", " ".join(cmd[1:]))  # skip 'sudo' in log
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._pid = self._proc.pid

        # Send cookie on stdin
        if self._proc.stdin:
            self._proc.stdin.write(cookie.encode() + b"\n")
            self._proc.stdin.close()

    def wait_for_tunnel(self, timeout: int = TUN_WAIT_SECONDS) -> bool:
        """Wait for tun0 interface to appear."""
        log.info("Waiting for %s (timeout %ds)...", TUN_DEVICE, timeout)
        for i in range(timeout):
            if Path(f"/sys/class/net/{TUN_DEVICE}").exists():
                log.info("%s appeared after %ds, stabilizing...", TUN_DEVICE, i + 1)
                time.sleep(TUN_STABILIZE_SECONDS)
                return True
            time.sleep(1)

        log.error("%s did not appear within %ds", TUN_DEVICE, timeout)
        return False

    def disconnect(self) -> None:
        """Kill openconnect process."""
        if self._proc is None:
            return

        pid = self._pid
        log.info("Disconnecting VPN (PID %s)...", pid)

        try:
            subprocess.run(["sudo", "kill", str(pid)], check=False, timeout=5)
        except subprocess.TimeoutExpired:
            pass

        # Wait for graceful exit
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("openconnect didn't exit gracefully, sending SIGKILL")
            try:
                subprocess.run(
                    ["sudo", "kill", "-9", str(pid)], check=False, timeout=5
                )
                self._proc.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                log.error("Failed to kill openconnect PID %s", pid)

        self._proc = None
        self._pid = None
        log.info("VPN disconnected")

    def read_output_line(self) -> str | None:
        """Read a line from openconnect stdout (non-blocking)."""
        if self._proc and self._proc.stdout:
            try:
                line = self._proc.stdout.readline()
                if line:
                    return line.decode("utf-8", errors="replace").rstrip()
            except (OSError, ValueError):
                pass
        return None
