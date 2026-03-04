"""Dual file/UI logging handler."""

import logging
from pathlib import Path

LOG_DIR = Path.home() / ".local" / "share" / "gp-split-saml"
LOG_FILE = LOG_DIR / "vpn.log"


class UIHandler(logging.Handler):
    """Logging handler that forwards records to a GTK TextView callback."""

    def __init__(self, callback=None):
        super().__init__()
        self._callback = callback

    def set_callback(self, callback):
        self._callback = callback

    def emit(self, record):
        if self._callback:
            msg = self.format(record)
            try:
                self._callback(msg)
            except Exception:
                pass


def setup_logging(ui_handler: UIHandler | None = None) -> logging.Logger:
    """Configure dual file + UI logging."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("gp_split_saml")
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(message)s",
            datefmt="%H:%M:%S",
        )

        fh = logging.FileHandler(LOG_FILE, mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    if ui_handler:
        ui_fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
        ui_handler.setFormatter(ui_fmt)
        ui_handler.setLevel(logging.INFO)
        logger.addHandler(ui_handler)

    return logger
