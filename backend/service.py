"""The only module the UI is allowed to call.

Plain synchronous Python; returns plain dicts and lists.
"""
from typing import Any

import yaml

from backend import paths


class ServiceError(Exception):
    """The single error type raised to the UI."""


_APP_DEFAULTS: dict[str, Any] = {
    "title": "Support Console",
    "width": 1280,
    "height": 800,
    "native": True,
    "oracle_client_dir": None,
    "call_timeout_seconds": 60,
    "connect_timeout_seconds": 10,
}


def load_app_settings() -> dict[str, Any]:
    """Read config/app.yaml, filling in defaults for missing keys."""
    path = paths.config_dir() / "app.yaml"
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        data = {}
    except yaml.YAMLError as exc:
        raise ServiceError(f"config/app.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ServiceError("config/app.yaml must contain key: value settings")
    settings = {**_APP_DEFAULTS, **data}
    settings["width"] = int(settings["width"])
    settings["height"] = int(settings["height"])
    settings["native"] = bool(settings["native"])
    return settings
