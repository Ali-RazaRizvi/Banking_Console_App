"""Loads and validates the query registry.

Queries live in YAML files under config/queries/ (one file per feature), plus
config/queries.yaml if it exists. Each file:

    order: 10            # optional; lower numbers are shown first (default 100)
    queries:
      - id: ...
        ...

Table names can be written as {{role:TABLE}}, e.g. {{obbrn:SRV_TB_BC_TELLER_BATCH}}.
At run time this becomes OWNER.TABLE or OWNER.TABLE@DBLINK, using the owner
and DB link saved in the connection profile. Only validated identifiers are
substituted; parameter values are always bind variables.
"""
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from backend import paths, policy
from backend.errors import ServiceError
from backend.profiles import SCHEMA_ROLES

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
TEMPLATE_RE = re.compile(r"\{\{\s*([a-z]+)\s*:\s*([A-Za-z][A-Za-z0-9_$#]*)\s*\}\}")
BIND_RE = re.compile(r"(?<![:\w]):([A-Za-z][A-Za-z0-9_]*)")

PAGES = {"dashboard", "actions"}
TYPES = {"read", "write"}
DISPLAYS = {"table", "count"}
PARAM_TYPES = {"text", "date", "number"}

_ENTRY_KEYS = {
    "id", "title", "page", "group", "schema", "type", "sql", "params", "display",
    "max_rows", "confirm", "expect_rows", "disabled", "description",
}
_PARAM_KEYS = {"name", "label", "required", "type", "upper", "placeholder"}


def queries_dir() -> Path:
    return paths.config_dir() / "queries"


def _files() -> list[Path]:
    files: list[Path] = []
    folder = queries_dir()
    if folder.is_dir():
        files += sorted([*folder.glob("*.yaml"), *folder.glob("*.yml")], key=lambda p: p.name.lower())
    single = paths.config_dir() / "queries.yaml"
    if single.exists():
        files.append(single)
    return files


def expand_templates(sql: str, resolver: Callable[[str, str], str]) -> str:
    return TEMPLATE_RE.sub(lambda m: resolver(m.group(1), m.group(2)), sql)


def bind_names(sql: str) -> set[str]:
    """Bind variable names used in the SQL (outside strings and comments), lower case."""
    return {b.lower() for b in BIND_RE.findall(policy.strip_sql(sql))}


def _validate_param(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ServiceError(f"{where}: each param must be a mapping")
    extra = set(raw) - _PARAM_KEYS
    if extra:
        raise ServiceError(f"{where}: unknown param field(s) {sorted(extra)}")
    name = str(raw.get("name") or "")
    if not PARAM_RE.match(name):
        raise ServiceError(f"{where}: param name '{name}' must be lower case letters, digits and _")
    ptype = str(raw.get("type") or "text")
    if ptype not in PARAM_TYPES:
        raise ServiceError(f"{where}: param '{name}' type must be one of {sorted(PARAM_TYPES)}")
    return {
        "name": name,
        "label": str(raw.get("label") or name),
        "required": bool(raw.get("required", True)),
        "type": ptype,
        "upper": bool(raw.get("upper", False)),
        "placeholder": str(raw.get("placeholder") or ""),
    }


def _validate(raw: Any, where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ServiceError(f"{where}: must be a mapping")
    extra = set(raw) - _ENTRY_KEYS
    if extra:
        raise ServiceError(f"{where}: unknown field(s) {sorted(extra)}")

    qid = str(raw.get("id") or "")
    if not ID_RE.match(qid):
        raise ServiceError(f"{where}: id '{qid}' must be lower case letters, digits and _")
    where = f"{where} ({qid})"

    def need(key: str, allowed: set[str] | None = None) -> str:
        value = raw.get(key)
        if value is None or str(value).strip() == "":
            raise ServiceError(f"{where}: '{key}' is required")
        value = str(value).strip()
        if allowed is not None and value not in allowed:
            raise ServiceError(f"{where}: '{key}' must be one of {sorted(allowed)}")
        return value

    entry: dict[str, Any] = {
        "id": qid,
        "title": need("title"),
        "page": need("page", PAGES),
        "group": need("group"),
        "schema": need("schema", set(SCHEMA_ROLES)),
        "type": need("type", TYPES),
        "sql": need("sql"),
        "display": str(raw.get("display") or "table"),
        "description": str(raw.get("description") or ""),
        "disabled": bool(raw.get("disabled", False)),
    }
    if entry["display"] not in DISPLAYS:
        raise ServiceError(f"{where}: 'display' must be one of {sorted(DISPLAYS)}")
    try:
        entry["max_rows"] = int(raw.get("max_rows") or 500)
    except (TypeError, ValueError):
        raise ServiceError(f"{where}: 'max_rows' must be a number") from None
    if not 1 <= entry["max_rows"] <= 5000:
        raise ServiceError(f"{where}: 'max_rows' must be between 1 and 5000")

    params_raw = raw.get("params") or []
    if not isinstance(params_raw, list):
        raise ServiceError(f"{where}: 'params' must be a list")
    entry["params"] = [_validate_param(p, where) for p in params_raw]
    names = [p["name"] for p in entry["params"]]
    if len(names) != len(set(names)):
        raise ServiceError(f"{where}: duplicate param names")

    for role, _table in TEMPLATE_RE.findall(entry["sql"]):
        if role not in SCHEMA_ROLES:
            raise ServiceError(f"{where}: unknown schema '{role}' in {{{{{role}:...}}}}")
    sql = expand_templates(entry["sql"], lambda _r, t: f"OWNER.{t}")
    if "{{" in sql or "}}" in sql:
        raise ServiceError(f"{where}: malformed {{{{schema:TABLE}}}} placeholder")

    st = policy.analyse(sql)
    if entry["type"] == "read":
        if st.kind != policy.READ:
            raise ServiceError(f"{where}: a 'read' query must start with SELECT or WITH")
        policy.check(sql, "PROD", [])            # rejects FOR UPDATE, PRAGMA ...
    else:
        if st.kind not in (policy.DML, policy.PLSQL):
            raise ServiceError(f"{where}: a 'write' entry must be INSERT, UPDATE, DELETE, MERGE or a PL/SQL block")
        if entry["display"] == "count":
            raise ServiceError(f"{where}: 'display: count' is only for read queries")
        entry["confirm"] = need("confirm")
        try:
            entry["expect_rows"] = int(raw.get("expect_rows") or 1)
        except (TypeError, ValueError):
            raise ServiceError(f"{where}: 'expect_rows' must be a number") from None
        if entry["expect_rows"] < 1:
            raise ServiceError(f"{where}: 'expect_rows' must be at least 1")

    used = bind_names(sql)
    declared = set(names)
    if used - declared:
        raise ServiceError(f"{where}: SQL uses :{', :'.join(sorted(used - declared))} but no param declares it")
    if declared - used:
        raise ServiceError(f"{where}: param(s) {sorted(declared - used)} are not used in the SQL")
    return entry


def load() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for file_index, path in enumerate(_files()):
        try:
            with path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ServiceError(f"{path.name} is not valid YAML: {exc}") from exc
        if data is None:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
            raise ServiceError(f"{path.name}: must contain a 'queries:' list")
        try:
            order = int(data.get("order", 100))
        except (TypeError, ValueError):
            raise ServiceError(f"{path.name}: 'order' must be a number") from None
        for pos, raw in enumerate(data["queries"]):
            entry = _validate(raw, f"{path.name}, entry {pos + 1}")
            if entry["id"] in seen:
                raise ServiceError(f"Duplicate id '{entry['id']}' in {seen[entry['id']]} and {path.name}")
            seen[entry["id"]] = path.name
            entry["source"] = path.name
            entry["_sort"] = (order, file_index, pos)
            entries.append(entry)
    entries.sort(key=lambda e: e["_sort"])
    for e in entries:
        del e["_sort"]
    return entries
