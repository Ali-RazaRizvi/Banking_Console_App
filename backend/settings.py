"""Loads and validates config/app.yaml."""
from typing import Any

import yaml

from backend import paths
from backend.errors import ServiceError

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
    path = paths.config_dir() / "app.yaml"
    data: Any = {}
    if path.exists():
        try:
            with path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise ServiceError(f"app.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ServiceError("app.yaml must contain key: value settings")
    settings = {**_APP_DEFAULTS, **data}
    try:
        for key in ("width", "height", "call_timeout_seconds", "connect_timeout_seconds"):
            settings[key] = int(settings[key])
    except (TypeError, ValueError) as exc:
        raise ServiceError(f"app.yaml: width, height and timeouts must be numbers ({exc})") from exc
    settings["native"] = bool(settings["native"])
    return settings
