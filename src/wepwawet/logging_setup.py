"""Structured logging: ``key=value`` lines, with secrets redacted before they reach any handler."""

import logging
import re
from pathlib import Path
from typing import Any

# Values of these keys are replaced with "***" wherever they appear in a message.
_SECRET_PATTERN = re.compile(
    r"(?P<key>\b(?:token|api[_-]?key|key|password|secret|authorization|session)\b\s*[=:]\s*)"
    r"(?P<quote>[\"']?)(?P<value>(?:Bearer\s+)?[^\s\"'&,;]+)",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    return _SECRET_PATTERN.sub(lambda m: f"{m.group('key')}{m.group('quote')}***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


class KeyValueFormatter(logging.Formatter):
    """``ts=... level=... logger=... msg="..." key=value`` (extra fields passed via ``extra={"fields": {...}}``)."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [
            f"ts={self.formatTime(record, '%Y-%m-%dT%H:%M:%S')}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"msg={_quote(record.getMessage())}",
        ]
        fields: dict[str, Any] = getattr(record, "fields", {}) or {}
        parts.extend(f"{key}={_quote(redact(str(value)))}" for key, value in fields.items())
        if record.exc_info:
            parts.append(f"exc={_quote(self.formatException(record.exc_info))}")
        return " ".join(parts)


def _quote(value: str) -> str:
    if value and not any(ch in value for ch in ' "=\n'):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure the root logger once: stderr always, plus ``log_file`` when given."""
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.addFilter(RedactingFilter())
        handler.setFormatter(KeyValueFormatter())
        root.addHandler(handler)
    # HTTP client libraries log every request URL at INFO; some URLs carry API keys.
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
