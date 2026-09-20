"""Logging setup for the Wall server: rotating file handler + token redaction."""

from __future__ import annotations

import logging
import logging.handlers
import pathlib

LOG_FILE_NAME = "wall.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


class RedactToken(logging.Filter):
    """Replace every occurrence of a secret token in a log message.

    An empty token disables the filter (pass-through no-op). ``filter`` always
    returns True: redaction must never drop a record.
    """

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if self.token:
            record.msg = record.getMessage().replace(self.token, "<redacted>")
            record.args = None
        return True


def setup_logging(
    log_dir: pathlib.Path, token: str, logger_name: str = "mc_wall.server"
) -> logging.Logger:
    log_dir = pathlib.Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    logger = logging.getLogger(logger_name)
    for handler in logger.handlers:
        if getattr(handler, "baseFilename", None) == str(log_path):
            # Idempotent per-path: never attach a second handler for this file.
            return logger
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RedactToken(token))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
