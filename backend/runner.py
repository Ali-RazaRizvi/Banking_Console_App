"""Runs registry entries against the active connection."""
import datetime as dt
import decimal
from typing import Any

from backend import policy, registry
from backend.errors import ServiceError
from backend.profiles import SCHEMA_ROLES

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d/%m/%Y")
_MAX_TEXT = 4000
_MAX_PARAM = 200


# ----- where does a query run? ----------------------------------------------------

def plan(entry: dict[str, Any], manager) -> tuple[str, str]:
    """Return (connection role to run on, SQL with {{role:TABLE}} expanded)."""
    profile = manager.profile()
    if profile is None:
        raise ServiceError("Not connected")
    connected = manager.connected_roles()
    objects = profile.get("objects") or {}

    def owner_of(role: str) -> str | None:
        return (objects.get(role) or {}).get("owner")

    def link_of(role: str) -> str | None:
        return (objects.get(role) or {}).get("dblink")

    target = entry["schema"]
    if target in connected:
        conn_role = target
    elif link_of(target) and "fcubs" in connected:
        conn_role = "fcubs"
    else:
        label = SCHEMA_ROLES.get(target, target)
        raise ServiceError(f"{label} is not connected, and no DB link to it is set in the connection")

    def resolve(role: str, table: str) -> str:
        if role not in SCHEMA_ROLES:
            raise ServiceError(f"Unknown schema '{role}'")
        owner = owner_of(role)
        name = f"{owner}.{table}" if owner else table
        if role != conn_role:
            link = link_of(role)
            if not link:
                raise ServiceError(
                    f"This query needs {SCHEMA_ROLES[role]} tables from the {SCHEMA_ROLES[conn_role]} "
                    "connection, but no DB link is set"
                )
            name = f"{name}@{link}"
        return name

    return conn_role, registry.expand_templates(entry["sql"], resolve)


def availability(entry: dict[str, Any], manager) -> tuple[bool, str | None]:
    if entry.get("disabled"):
        return False, "Disabled in the query file"
    try:
        plan(entry, manager)
    except ServiceError as exc:
        return False, str(exc)
    return True, None


# ----- parameters -----------------------------------------------------------------

def _parse_date(value: str, label: str) -> dt.datetime:
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ServiceError(f"'{label}' must be a date like 2026-10-29 or 29-OCT-2026")


def build_binds(entry: dict[str, Any], params: dict[str, Any] | None) -> dict[str, Any]:
    params = params or {}
    binds: dict[str, Any] = {}
    for p in entry["params"]:
        raw = params.get(p["name"])
        value = "" if raw is None else str(raw).strip()
        if len(value) > _MAX_PARAM:
            raise ServiceError(f"'{p['label']}' is too long")
        if not value:
            if p["required"]:
                raise ServiceError(f"'{p['label']}' is required")
            binds[p["name"]] = None
            continue
        if p["type"] == "date":
            binds[p["name"]] = _parse_date(value, p["label"])
        elif p["type"] == "number":
            try:
                binds[p["name"]] = int(value)
            except ValueError:
                try:
                    binds[p["name"]] = float(value)
                except ValueError:
                    raise ServiceError(f"'{p['label']}' must be a number") from None
        else:
            binds[p["name"]] = value.upper() if p["upper"] else value
    return binds


# ----- result conversion ----------------------------------------------------------

def _fmt_datetime(value: dt.datetime | dt.date) -> str:
    text = f"{value.day:02d}-{_MONTHS[value.month - 1]}-{value.year:04d}"
    if isinstance(value, dt.datetime) and (value.hour, value.minute, value.second) != (0, 0, 0):
        text += f" {value.hour:02d}:{value.minute:02d}:{value.second:02d}"
    return text


def to_plain(value: Any) -> Any:
    """Convert a DB value to str / int / float / bool / None."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "..."
    if isinstance(value, (dt.datetime, dt.date)):
        return _fmt_datetime(value)
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if hasattr(value, "read"):                       # LOB, in case fetch_lobs is on
        return to_plain(value.read())
    return str(value)


# ----- run ------------------------------------------------------------------------

def run_read(entry: dict[str, Any], params: dict[str, Any] | None, manager) -> dict[str, Any]:
    if entry["type"] != "read":
        raise ServiceError("Not a read query")
    if entry.get("disabled"):
        raise ServiceError("This query is disabled")
    binds = build_binds(entry, params)
    conn_role, sql = plan(entry, manager)
    policy.check(sql, manager.env_type() or "PROD", [])
    max_rows = entry["max_rows"]

    with manager.acquire(conn_role) as conn:
        with conn.cursor() as cur:
            cur.arraysize = min(max_rows + 1, 1000)
            cur.prefetchrows = cur.arraysize + 1
            cur.execute(sql, binds)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return {
        "columns": columns,
        "rows": [[to_plain(v) for v in row] for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
