"""Saved connection profiles (alias + UAT/PROD + per-schema connection details).

Stored in config/profiles.yaml (ignored by git). Passwords are never stored.
"""
import os
import re
from typing import Any

import yaml

from backend import paths
from backend.errors import ServiceError

SCHEMA_ROLES: dict[str, str] = {
    "fcubs": "FLEXCUBE (FCUBS)",
    "obbrn": "OBBRN",
    "obpm": "OBPM",
}
ENV_TYPES = ("UAT", "PROD")
DEFAULT_OWNERS: dict[str, str] = {"obbrn": "OBTRANSACTION"}
_FORBIDDEN_KEYS = {"password", "passwd", "pwd"}
_OWNER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
_DBLINK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#.]{0,127}$")


def profiles_file():
    return paths.config_dir() / "profiles.yaml"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize(profile: dict[str, Any]) -> dict[str, Any]:
    """Validate a profile and return a clean copy. Schema sections left empty are dropped."""
    if not isinstance(profile, dict):
        raise ServiceError("Connection details are missing")
    alias = _text(profile.get("alias"))
    if not alias:
        raise ServiceError("Alias is required")
    if len(alias) > 40:
        raise ServiceError("Alias must be 40 characters or fewer")
    env_type = _text(profile.get("env_type")).upper()
    if env_type not in ENV_TYPES:
        raise ServiceError("Choose UAT or PROD")

    schemas_in = profile.get("schemas") or {}
    clean: dict[str, dict[str, Any]] = {}
    for role, cfg in schemas_in.items():
        if role not in SCHEMA_ROLES:
            raise ServiceError(f"Unknown schema '{role}'")
        label = SCHEMA_ROLES[role]
        cfg = cfg or {}
        if _FORBIDDEN_KEYS & {str(k).lower() for k in cfg}:
            raise ServiceError(f"{label}: passwords must not be stored")
        host, user = _text(cfg.get("host")), _text(cfg.get("user"))
        sid, service, dsn = _text(cfg.get("sid")), _text(cfg.get("service")), _text(cfg.get("dsn"))
        if not any((host, user, sid, service, dsn)):
            continue                                   # section left empty: skipped
        if not user:
            raise ServiceError(f"{label}: user is required")
        port = 1521
        if not dsn:
            if not host:
                raise ServiceError(f"{label}: host is required")
            try:
                port = int(float(cfg.get("port") or 1521))
            except (TypeError, ValueError):
                raise ServiceError(f"{label}: port must be a number") from None
            if not 1 <= port <= 65535:
                raise ServiceError(f"{label}: port must be between 1 and 65535")
            if bool(sid) == bool(service):
                raise ServiceError(f"{label}: give either a SID or a service name")
        clean[role] = {
            "host": host or None,
            "port": port,
            "sid": sid or None,
            "service": service or None,
            "dsn": dsn or None,
            "user": user,
        }
    if not clean:
        raise ServiceError("Fill in the connection details for at least one schema")

    # Table owner and DB link per schema, used to expand {{role:TABLE}} in queries.
    objects_in = profile.get("objects") or {}
    objects: dict[str, dict[str, str | None]] = {}
    for role, obj in objects_in.items():
        if role not in SCHEMA_ROLES:
            raise ServiceError(f"Unknown schema '{role}'")
        obj = obj or {}
        owner = _text(obj.get("owner")).upper()
        dblink = _text(obj.get("dblink")).upper()
        label = SCHEMA_ROLES[role]
        if owner and not _OWNER_RE.match(owner):
            raise ServiceError(f"{label}: table owner '{owner}' is not a valid name")
        if dblink and not _DBLINK_RE.match(dblink):
            raise ServiceError(f"{label}: DB link '{dblink}' is not a valid name")
        if role == "fcubs" and dblink:
            raise ServiceError("A DB link is only used for OBBRN / OBPM (reached from FLEXCUBE)")
        if owner or dblink:
            objects[role] = {"owner": owner or None, "dblink": dblink or None}

    return {"alias": alias, "env_type": env_type, "schemas": clean, "objects": objects}


def describe_target(cfg: dict[str, Any]) -> str:
    if cfg.get("dsn"):
        dsn = str(cfg["dsn"])
        return dsn if len(dsn) <= 60 else dsn[:57] + "..."
    name = cfg.get("service") or f"SID {cfg.get('sid')}"
    return f"{cfg['host']}:{cfg['port']}/{name}"


def list_profiles() -> list[dict[str, Any]]:
    path = profiles_file()
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ServiceError(f"{path.name} is not valid YAML: {exc}") from exc
    items = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    result = []
    for item in items:
        try:
            result.append(normalize(item))
        except ServiceError as exc:
            raise ServiceError(f"{path.name}: {exc}") from exc
    return sorted(result, key=lambda p: p["alias"].lower())


def _write(items: list[dict[str, Any]]) -> None:
    path = profiles_file()
    tmp = path.with_suffix(".tmp")
    header = "# Saved connections. Written by the app. Passwords are never stored here.\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.safe_dump({"profiles": items}, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, path)


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    clean = normalize(profile)
    items = [p for p in list_profiles() if p["alias"].lower() != clean["alias"].lower()]
    items.append(clean)
    _write(sorted(items, key=lambda p: p["alias"].lower()))
    return clean


def delete_profile(alias: str) -> None:
    items = list_profiles()
    kept = [p for p in items if p["alias"].lower() != _text(alias).lower()]
    if len(kept) != len(items):
        _write(kept)
