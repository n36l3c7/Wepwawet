import logging
from pathlib import Path

import pytest

from wepwawet.logging_setup import KeyValueFormatter, redact, setup_logging


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("token=abc123", "token=***"),
        ("GET /auth?token=abc123&x=1", "GET /auth?token=***&x=1"),
        ("api_key: 'sk-live'", "api_key: '***'"),
        ("Authorization: Bearer xyz", "Authorization: ***"),
        ("no secrets here", "no secrets here"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_formatter_key_values() -> None:
    record = logging.LogRecord("wepwawet.x", logging.INFO, __file__, 1, "Local API ready", None, None)
    record.fields = {"url": "http://127.0.0.1:1", "note": "has space"}
    line = KeyValueFormatter().format(record)
    assert 'msg="Local API ready"' in line
    assert "url=http://127.0.0.1:1" in line
    assert 'note="has space"' in line


def test_log_file_never_contains_the_token(tmp_path: Path) -> None:
    log_file = tmp_path / "wepwawet.log"
    setup_logging("INFO", log_file)
    logging.getLogger("wepwawet.test").info("opening /auth?token=%s", "super-secret-value")
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "super-secret-value" not in content
    assert "token=***" in content
