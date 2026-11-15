"""Settings changed from the window: ``settings.toml`` and the keys in ``secrets.toml``.

``settings.toml`` sits in the configuration directory and is layered over ``config.toml``
(environment variables still win), so a hand-written ``config.toml`` is never rewritten. Only
the options listed in ``EDITABLE`` and ``providers.<name>.enabled`` can be set from the window;
everything else stays in ``config.toml``.

API keys are written to ``secrets.toml`` with mode 0600. Their values are never read back:
the window only learns whether a key is set and where it comes from.
"""

import os
import stat
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wepwawet.config import SETTINGS_FILE, Config, ConfigError, load_config
from wepwawet.core.secrets import KNOWN_SECRETS
from wepwawet.paths import AppPaths

SECRETS_FILE = "secrets.toml"
MAX_KEY_LENGTH = 1024

# Options the window may change, by section. Restart-only ones are applied at the next start.
EDITABLE: dict[str, tuple[str, ...]] = {
    "map": ("basemap",),
    "gui": ("rendering",),
    "logging": ("level",),
    "measurements": (
        "enabled",
        "sweep_probes",
        "zoom_probes",
        "local_probes",
        "landmarks",
        "max_tests",
        "packets",
        "tcp_fallback_port",
        "calibrate",
        "calibration_anchors",
        "traceroute_probes",
        "access_correction",
    ),
}
RESTART_REQUIRED = ("gui.rendering",)
ENV_OPTIONS = {
    "map.basemap": "WEPWAWET_BASEMAP",
    "gui.rendering": "WEPWAWET_RENDERING",
    "logging.level": "WEPWAWET_LOG_LEVEL",
}


class SettingsError(Exception):
    """A setting or a key is invalid, or a file cannot be written."""


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SettingsError(f"{path} is not valid TOML: {exc}") from exc


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
            raise SettingsError("values cannot contain control characters")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise SettingsError(f"unsupported value type {type(value).__name__}")


def dump_toml(data: Mapping[str, Any], header: str = "") -> str:
    """TOML for tables of scalars, with one level of sub-tables (``[providers.<name>]``)."""
    lines = [header] if header else []
    for section, values in data.items():
        if not isinstance(values, Mapping):
            raise SettingsError(f"[{section}] must be a table")
        scalars = {k: v for k, v in values.items() if not isinstance(v, Mapping)}
        tables = {k: v for k, v in values.items() if isinstance(v, Mapping)}
        if scalars:
            lines += ["", f"[{section}]"] + [f"{k} = {_toml_value(v)}" for k, v in scalars.items()]
        for name, table in tables.items():
            lines += ["", f"[{section}.{name}]"] + [f"{k} = {_toml_value(v)}" for k, v in table.items()]
    return "\n".join(lines).lstrip("\n") + "\n"


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` atomically, readable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SettingsError(f"Cannot write {path}: {exc}") from exc


def _clean_update(update: Mapping[str, Any], provider_names: set[str]) -> dict[str, Any]:
    """Keep only editable options; reject anything else instead of ignoring it silently."""
    clean: dict[str, Any] = {}
    for section, values in update.items():
        if not isinstance(values, Mapping):
            raise SettingsError(f"{section} must be an object")
        if section == "providers":
            for name, options in values.items():
                if name not in provider_names:
                    raise SettingsError(f"unknown source {name}")
                if not isinstance(options, Mapping) or set(options) - {"enabled"}:
                    raise SettingsError(f"only providers.{name}.enabled can be changed from the window")
                if not isinstance(options.get("enabled"), bool):
                    raise SettingsError(f"providers.{name}.enabled must be true or false")
                clean.setdefault("providers", {})[name] = {"enabled": options["enabled"]}
            continue
        allowed = EDITABLE.get(section)
        if allowed is None:
            raise SettingsError(f"[{section}] cannot be changed from the window")
        for key, value in values.items():
            if key not in allowed:
                raise SettingsError(f"{section}.{key} cannot be changed from the window")
            clean.setdefault(section, {})[key] = value
    return clean


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class SettingsStore:
    """Reads and writes the window's settings and keys for one set of application paths."""

    def __init__(self, paths: AppPaths, environ: Mapping[str, str] | None = None) -> None:
        self.paths = paths
        self._environ = environ

    @property
    def environ(self) -> dict[str, str]:
        return dict(os.environ if self._environ is None else self._environ)

    @property
    def settings_file(self) -> Path:
        return self.paths.config_dir / SETTINGS_FILE

    @property
    def secrets_file(self) -> Path:
        return self.paths.config_dir / SECRETS_FILE

    def update(self, update: Mapping[str, Any], provider_names: set[str]) -> Config:
        """Merge ``update`` into ``settings.toml`` after checking the whole configuration still loads."""
        clean = _clean_update(update, provider_names)
        merged = _merge(_read_toml(self.settings_file), clean)
        text = dump_toml(merged, "# Written by the Wepwawet settings panel; layered over config.toml.")
        # Validate before writing: a rejected value must not leave a broken file behind.
        check = self.paths.config_dir / f".{SETTINGS_FILE}.check"
        _write_private(check, text)
        try:
            config = load_config(self.paths, self.environ, settings_file=check)
        except ConfigError as exc:
            raise SettingsError(str(exc)) from exc
        finally:
            check.unlink(missing_ok=True)
        _write_private(self.settings_file, text)
        return config

    def set_key(self, name: str, value: str | None) -> None:
        """Store ``value`` for key ``name`` in ``secrets.toml``, or remove it when ``value`` is None."""
        if name not in KNOWN_SECRETS:
            raise SettingsError(f"unknown key {name}")
        data = _read_toml(self.secrets_file)
        keys = data.get("keys", {})
        if not isinstance(keys, dict):
            raise SettingsError(f"{self.secrets_file}: [keys] must be a table")
        if value is None:
            keys.pop(name, None)
        else:
            value = value.strip()
            if not value or len(value) > MAX_KEY_LENGTH:
                raise SettingsError(f"the key must be 1 to {MAX_KEY_LENGTH} characters long")
            if any(c.isspace() for c in value):
                raise SettingsError("the key cannot contain spaces")
            keys[name] = value
        data["keys"] = keys
        _write_private(self.secrets_file, dump_toml(data))

    def key_status(self) -> list[dict[str, Any]]:
        """Each known key with where its value comes from: ``env``, ``file`` or None. Never the value."""
        try:
            stored = _read_toml(self.secrets_file).get("keys", {})
        except SettingsError:
            stored = {}
        env = self.environ
        rows = []
        for name, description in KNOWN_SECRETS.items():
            if env.get(f"WEPWAWET_{name.upper()}", "").strip():
                source: str | None = "env"
            elif isinstance(stored, dict) and str(stored.get(name, "")).strip():
                source = "file"
            else:
                source = None
            rows.append(
                {"name": name, "description": description, "source": source, "variable": f"WEPWAWET_{name.upper()}"}
            )
        return rows

    def env_overrides(self) -> list[str]:
        """Options fixed by an environment variable, which the window cannot change."""
        env = self.environ
        return [option for option, variable in ENV_OPTIONS.items() if env.get(variable, "").strip()]


def editable_values(config: Config, provider_names: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {
        section: {key: getattr(getattr(config, section), key) for key in keys} for section, keys in EDITABLE.items()
    }
    values["providers"] = {name: {"enabled": config.provider(name).enabled} for name in provider_names}
    return values
