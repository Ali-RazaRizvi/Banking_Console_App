"""The only module the UI is allowed to call.

Plain synchronous Python; returns plain dicts and lists; raises only ServiceError.
"""
import threading
from typing import Any

from backend import db, policy, profiles, registry, runner, settings
from backend.errors import ServiceError

__all__ = [
    "ServiceError",
    "SCHEMA_ROLES",
    "ENV_TYPES",
    "load_app_settings",
    "list_profiles",
    "validate_profile",
    "save_profile",
    "delete_profile",
    "describe_target",
    "connect",
    "disconnect",
    "status",
    "forbidden_tables",
    "check_sql",
    "list_queries",
    "run_query",
    "reload_registry",
]

SCHEMA_ROLES = dict(profiles.SCHEMA_ROLES)
ENV_TYPES = tuple(profiles.ENV_TYPES)
DEFAULT_OWNERS = dict(profiles.DEFAULT_OWNERS)

_manager = db.ConnectionManager()
_registry_lock = threading.Lock()
_registry_entries: list[dict[str, Any]] | None = None


def load_app_settings() -> dict[str, Any]:
    return settings.load_app_settings()


# ----- saved connections ----------------------------------------------------------

def list_profiles() -> list[dict[str, Any]]:
    """[{alias, env_type, schemas: {role: {host, port, sid, service, dsn, user}}}]"""
    return profiles.list_profiles()


def validate_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return profiles.normalize(profile)


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return profiles.save_profile(profile)


def delete_profile(alias: str) -> None:
    profiles.delete_profile(alias)


def describe_target(schema_cfg: dict[str, Any]) -> str:
    return profiles.describe_target(schema_cfg)


# ----- connection -----------------------------------------------------------------

def connect(profile: dict[str, Any], passwords: dict[str, str], save: bool = False) -> dict[str, dict[str, Any]]:
    """Connect every schema in the profile that has a password.

    Returns {schema: {"ok": bool, "error": str | None, "skipped": bool}}.
    The profile becomes active if at least one schema connects; if `save`
    is true it is then saved (without passwords) for next time.
    """
    clean = profiles.normalize(profile)
    app = settings.load_app_settings()
    db.ensure_client_mode(app["oracle_client_dir"])
    results = _manager.connect(
        clean,
        passwords,
        connect_timeout=app["connect_timeout_seconds"],
        call_timeout=app["call_timeout_seconds"],
    )
    if save and any(r["ok"] for r in results.values()):
        profiles.save_profile(clean)
    return results


def disconnect() -> None:
    _manager.disconnect()


def status() -> dict[str, Any]:
    """{alias, env_type, is_production, allow_write, schemas: {name: bool}, errors: {name: str|None}}"""
    return _manager.status()


# ----- policy ---------------------------------------------------------------------

def forbidden_tables() -> list[str]:
    return policy.load_forbidden_tables()


def check_sql(sql: str, env_type: str | None = None) -> dict[str, Any]:
    """Check a statement against the policy of env_type (default: the connected one).
    Returns {kind, keyword, targets}; raises ServiceError if not allowed."""
    env = env_type or _manager.env_type()
    if env is None:
        raise ServiceError("Not connected")
    st = policy.check(sql, env)
    return {"kind": st.kind, "keyword": st.keyword, "targets": st.targets}


# ----- query registry -------------------------------------------------------------

def _entries() -> list[dict[str, Any]]:
    global _registry_entries
    with _registry_lock:
        if _registry_entries is None:
            _registry_entries = registry.load()
        return _registry_entries


def reload_registry() -> int:
    """Re-read config/queries/*.yaml. Returns the number of entries or raises ServiceError."""
    global _registry_entries
    entries = registry.load()
    with _registry_lock:
        _registry_entries = entries
    return len(entries)


def list_queries(page: str) -> list[dict[str, Any]]:
    """Entries for 'dashboard' or 'actions', without SQL, plus availability for the
    current connection: {..., "available": bool, "unavailable_reason": str | None}."""
    result = []
    for entry in _entries():
        if entry["page"] != page:
            continue
        item = {k: v for k, v in entry.items() if k != "sql"}
        item["params"] = [dict(p) for p in entry["params"]]
        item["available"], item["unavailable_reason"] = runner.availability(entry, _manager)
        result.append(item)
    return result


def run_query(query_id: str, params: dict[str, Any] | None = None, confirm_text: str | None = None) -> dict[str, Any]:
    """Reads: {columns, rows, row_count, truncated}."""
    entry = next((e for e in _entries() if e["id"] == query_id), None)
    if entry is None:
        raise ServiceError(f"Unknown query '{query_id}'")
    if entry["type"] == "write":
        raise ServiceError("Write actions are not enabled yet (coming in a later phase)")
    return runner.run_read(entry, params, _manager)
