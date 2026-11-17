"""SQL safety policy, enforced in the backend.

    PROD  -> read only. Only SELECT / WITH queries are allowed.
    UAT   -> INSERT / UPDATE / DELETE / MERGE and PL/SQL blocks are allowed,
             except when they change a table on the forbidden list
             (config/forbidden_tables.yaml).
    Both  -> DDL (CREATE, ALTER, DROP, TRUNCATE, GRANT ...), SELECT ... FOR UPDATE,
             more than one statement, and changes through database links are refused.

This check is a guard against mistakes in config/queries.yaml. It cannot see
inside triggers or stored procedures, so the database grants of the account
remain the real security boundary.
"""
import fnmatch
import re
from dataclasses import dataclass, field

import yaml

from backend import paths
from backend.errors import ServiceError

READ, DML, PLSQL, DDL, OTHER = "read", "dml", "plsql", "ddl", "other"

_READ_WORDS = {"SELECT", "WITH"}
_DML_WORDS = {"INSERT", "UPDATE", "DELETE", "MERGE"}
_PLSQL_WORDS = {"BEGIN", "DECLARE", "CALL"}
_DDL_WORDS = {
    "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT", "REVOKE", "RENAME", "COMMENT",
    "PURGE", "FLASHBACK", "ANALYZE", "AUDIT", "NOAUDIT", "ASSOCIATE", "DISASSOCIATE",
}

_IDENT = r'(?:"[^"]+"|[A-Za-z][A-Za-z0-9_$#]*)'
_NAME = rf'({_IDENT}(?:\s*\.\s*{_IDENT})?)(\s*@\s*[A-Za-z][A-Za-z0-9_$#.]*)?'
_NOT_A_TABLE = {"FROM", "ONLY", "INTO", "SET", "WHERE", "VALUES", "SELECT", "TABLE"}
_PATTERN_OK = re.compile(r"^[A-Za-z0-9_$#*?]+$")


@dataclass
class Statement:
    kind: str                       # read | dml | plsql | ddl | other
    keyword: str                    # first word, upper case
    targets: list[str] = field(default_factory=list)   # tables changed (DML only)


# ----- parsing --------------------------------------------------------------------

def _is_ident_char(ch: str) -> bool:
    return ch.isalnum() or ch in "_$#"


def strip_sql(sql: str) -> str:
    """Remove comments and replace string literals with '' so keywords inside
    strings or comments cannot fool the checks. Quoted identifiers are kept."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if c == "-" and nxt == "-":
            j = sql.find("\n", i)
            i = n if j < 0 else j
            out.append(" ")
            continue
        if c == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            if j < 0:
                raise ServiceError("SQL has an unclosed /* comment")
            i = j + 2
            out.append(" ")
            continue
        if c in "qQ" and nxt == "'":
            prev = sql[i - 1] if i > 0 else ""
            prev2 = sql[i - 2] if i > 1 else ""
            standalone = not _is_ident_char(prev) or (prev in "nN" and not _is_ident_char(prev2))
            if standalone:
                if i + 2 >= n:
                    raise ServiceError("SQL has an unclosed q'...' string")
                open_ch = sql[i + 2]
                close_ch = {"[": "]", "{": "}", "(": ")", "<": ">"}.get(open_ch, open_ch)
                j = sql.find(close_ch + "'", i + 3)
                if j < 0:
                    raise ServiceError("SQL has an unclosed q'...' string")
                i = j + 2
                out.append("''")
                continue
        if c == "'":
            j = i + 1
            while True:
                k = sql.find("'", j)
                if k < 0:
                    raise ServiceError("SQL has an unclosed string literal")
                if k + 1 < n and sql[k + 1] == "'":
                    j = k + 2
                    continue
                break
            i = k + 1
            out.append("''")
            continue
        if c == '"':
            k = sql.find('"', i + 1)
            if k < 0:
                raise ServiceError("SQL has an unclosed quoted identifier")
            out.append(sql[i:k + 1])
            i = k + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _table_name(raw: str) -> str:
    """'flexcube.actb_daily_log' -> 'ACTB_DAILY_LOG'; '"MyTab"' -> 'MyTab'."""
    last = re.split(r"\s*\.\s*", raw.strip())[-1]
    return last[1:-1] if last.startswith('"') else last.upper()


def _dml_targets(keyword: str, text: str) -> list[str]:
    unknown = ServiceError(
        f"Could not work out which table this {keyword} changes. "
        "Write it as a plain statement on a named table (no inline views)."
    )
    matches: list[re.Match] = []
    if keyword == "UPDATE":
        m = re.match(rf"UPDATE\s+(?:ONLY\s*\(\s*)?{_NAME}", text, re.I)
        matches = [m] if m else []
    elif keyword == "DELETE":
        m = re.match(rf"DELETE\s+(?:FROM\s+)?(?:ONLY\s*\(\s*)?{_NAME}", text, re.I)
        matches = [m] if m else []
    elif keyword == "MERGE":
        m = re.match(rf"MERGE\s+INTO\s+{_NAME}", text, re.I)
        matches = [m] if m else []
    elif keyword == "INSERT":
        if re.search(r"\bINTO\s*\(", text, re.I):
            raise unknown
        matches = list(re.finditer(rf"\bINTO\s+{_NAME}", text, re.I))
    if not matches:
        raise unknown

    targets: list[str] = []
    for m in matches:
        if m.group(2):
            raise ServiceError("Changes through a database link (@...) are not allowed")
        name = _table_name(m.group(1))
        if name.upper() in _NOT_A_TABLE:
            raise unknown
        if name not in targets:
            targets.append(name)
    return targets


def analyse(sql: str) -> Statement:
    text = strip_sql(sql or "").strip()
    if not text:
        raise ServiceError("SQL is empty")
    m = re.match(r"\(*\s*([A-Za-z]+)", text)
    keyword = m.group(1).upper() if m else ""

    if keyword in _READ_WORDS:
        kind = READ
    elif keyword in _DML_WORDS:
        kind = DML
    elif keyword in _PLSQL_WORDS:
        kind = PLSQL
    elif keyword in _DDL_WORDS:
        kind = DDL
    else:
        kind = OTHER

    if kind != PLSQL and ";" in text:
        raise ServiceError("Only one SQL statement is allowed, with no ';' at the end")
    if kind == PLSQL and re.search(r"^\s*/\s*$", text, re.M):
        raise ServiceError("A PL/SQL block must not contain a '/' line")

    targets = _dml_targets(keyword, text) if kind == DML else []
    return Statement(kind=kind, keyword=keyword, targets=targets)


# ----- forbidden tables -----------------------------------------------------------

def load_forbidden_tables() -> list[str]:
    """Read config/forbidden_tables.yaml. Missing or broken file -> error (fail closed)."""
    path = paths.config_dir() / "forbidden_tables.yaml"
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise ServiceError(f"{path.name} is missing; changes on UAT are blocked until it exists") from None
    except yaml.YAMLError as exc:
        raise ServiceError(f"{path.name} is not valid YAML: {exc}") from exc
    items = data.get("forbidden_tables") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ServiceError(f"{path.name} must contain a 'forbidden_tables:' list")
    patterns: list[str] = []
    for item in items:
        p = str(item).strip()
        if not _PATTERN_OK.match(p):
            raise ServiceError(f"{path.name}: '{p}' is not a valid table name or pattern")
        patterns.append(p.upper())
    return patterns


def is_forbidden(table: str, patterns: list[str]) -> bool:
    name = table.upper()
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


# ----- the check ------------------------------------------------------------------

def check(sql: str, env_type: str, forbidden: list[str] | None = None) -> Statement:
    """Raise ServiceError if the statement is not allowed in this environment type."""
    st = analyse(sql)

    if st.kind == READ:
        if re.search(r"\bFOR\s+UPDATE\b", strip_sql(sql), re.I):
            raise ServiceError("SELECT ... FOR UPDATE locks rows and is not allowed")
        if re.search(r"\bPRAGMA\b", strip_sql(sql), re.I):
            raise ServiceError("PRAGMA is not allowed in a query")
        return st

    if st.kind == DDL:
        raise ServiceError(f"{st.keyword} statements (DDL) are not allowed in this tool")
    if st.kind == OTHER:
        raise ServiceError(f"'{st.keyword or sql.strip()[:20]}' statements are not allowed in this tool")

    if env_type == "PROD":
        raise ServiceError(f"PROD is read-only: {st.keyword} is not allowed")
    if env_type != "UAT":
        raise ServiceError(f"Unknown environment type '{env_type}'")

    if forbidden is None:
        forbidden = load_forbidden_tables()

    if st.kind == DML:
        bad = [t for t in st.targets if is_forbidden(t, forbidden)]
        if bad:
            raise ServiceError(f"Changes to {', '.join(bad)} are forbidden (see forbidden_tables.yaml)")
    elif st.kind == PLSQL:
        idents = {_table_name(i) for i in re.findall(_IDENT, strip_sql(sql))}
        bad = sorted(t for t in idents if is_forbidden(t, forbidden))
        if bad:
            raise ServiceError(f"This PL/SQL block mentions forbidden table(s): {', '.join(bad)}")
    return st
