"""API keys: from ``WEPWAWET_<NAME>`` environment variables or ``secrets.toml`` in the configuration directory.

The file must be readable by its owner only (mode 0600); a looser file is refused, because
keys there are worth money. Values are never logged and never returned by the API.
"""

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path

KNOWN_SECRETS = {
    "globalping": "Globalping API token (raises the hourly measurement limit)",
    "ripe_atlas": "RIPE Atlas API key (measurements cost credits)",
    "ipinfo": "IPinfo token (download of the free IPinfo Lite database)",
    "maxmind_account_id": "MaxMind account ID (GeoLite2 download)",
    "maxmind_license_key": "MaxMind licence key (GeoLite2 download)",
    "peeringdb": "PeeringDB API key (higher rate limits)",
    "shodan": "Shodan API key (passive host lookup)",
    "censys": "Censys personal access token (passive host lookup)",
    "censys_org_id": "Censys organisation ID (needed by some Censys plans)",
}


class SecretsError(Exception):
    """The secrets file is unsafe or invalid."""


class Secrets:
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = {k: v for k, v in values.items() if v}

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def has(self, name: str) -> bool:
        return name in self._values

    def names(self) -> list[str]:
        return sorted(self._values)

    def __repr__(self) -> str:  # never print values
        return f"Secrets({', '.join(self.names())})"


def load_secrets(path: Path, environ: Mapping[str, str] | None = None) -> Secrets:
    env = os.environ if environ is None else environ
    values: dict[str, str] = {}
    if path.is_file():
        mode = path.stat().st_mode
        if os.name == "posix" and mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretsError(f"{path} is readable by other users. Run: chmod 600 {path}")
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise SecretsError(f"{path} is not valid TOML: {exc}") from exc
        keys = data.get("keys", {})
        if not isinstance(keys, dict):
            raise SecretsError(f'{path}: [keys] must be a table of name = "value" pairs')
        unknown = sorted(set(keys) - set(KNOWN_SECRETS))
        if unknown:
            raise SecretsError(f"{path}: unknown keys {', '.join(unknown)}; known: {', '.join(KNOWN_SECRETS)}")
        values.update({k: str(v).strip() for k, v in keys.items()})
    for name in KNOWN_SECRETS:
        value = env.get(f"WEPWAWET_{name.upper()}", "").strip()
        if value:
            values[name] = value
    return Secrets(values)
