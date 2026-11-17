"""Connection manager: one small connection pool per schema of the active environment.

Passwords are only passed to the driver; they are never stored by this module
or written anywhere. The driver keeps them in memory for the pool's lifetime.
"""
import copy
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import oracledb

from backend.errors import ServiceError

PROGRAM_NAME = "SupportConsole"   # shows in V$SESSION.PROGRAM for the DBA

oracledb.defaults.fetch_lobs = False   # CLOB -> str, BLOB -> bytes (no LOB objects)

# Session-control statement (not a query, so it lives here rather than in
# queries.yaml). On PROD every borrowed connection starts a read-only
# transaction, so the database itself rejects any change (ORA-01456).
_READ_ONLY_TXN = "SET TRANSACTION READ ONLY"

_thick_lock = threading.Lock()
_thick_dir: str | None = None


def ensure_client_mode(oracle_client_dir: str | None) -> None:
    """Switch to thick mode once, if an Oracle Client folder is configured."""
    global _thick_dir
    if not oracle_client_dir:
        return
    with _thick_lock:
        if _thick_dir is not None:
            return
        try:
            oracledb.init_oracle_client(lib_dir=str(oracle_client_dir))
        except oracledb.Error as exc:
            raise ServiceError(f"Could not load Oracle Client from '{oracle_client_dir}': {exc}") from exc
        _thick_dir = str(oracle_client_dir)


def oracle_message(exc: BaseException) -> str:
    """Keep the ORA-/DPY- code and text; add a hint for common setup problems."""
    text = str(exc).strip() or exc.__class__.__name__
    first = text.splitlines()[0]
    hints = {
        "DPY-3015": "This database uses an old password verifier. Set 'oracle_client_dir' in config/app.yaml to use thick mode.",
        "DPY-6005": "Could not reach the database. Check host, port, VPN and firewall.",
        "DPY-6001": "The listener does not know this service name. Check the service name.",
        "DPY-6003": "The listener does not know this SID. Check the SID.",
        "DPY-4011": "The connection was closed by the database or network.",
        "ORA-12514": "The listener does not know this service name. Check the service name.",
        "ORA-12505": "The listener does not know this SID. Check the SID.",
        "ORA-01017": "Wrong user name or password.",
        "ORA-28000": "This database account is locked.",
        "ORA-28001": "This database account's password has expired.",
        "ORA-00942": "Check the table name, the table owner in the connection settings, and the user's SELECT grant.",
        "ORA-02019": "The DB link was not found. Check the DB link name in the connection settings.",
        "ORA-12154": "The DB link's target could not be resolved on the database server.",
        "DPY-4024": "The query took longer than call_timeout_seconds (config/app.yaml).",
        "ORA-01013": "The query was cancelled (timeout).",
    }
    for code, hint in hints.items():
        if code in first:
            return f"{first}\n{hint}"
    return first


def _make_dsn(cfg: dict[str, Any]) -> str:
    if cfg.get("dsn"):
        return str(cfg["dsn"])
    if cfg.get("sid") and not cfg.get("service"):
        return oracledb.makedsn(cfg["host"], cfg["port"], sid=cfg["sid"])
    return oracledb.makedsn(cfg["host"], cfg["port"], service_name=cfg["service"])


class ConnectionManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._profile: dict[str, Any] | None = None     # alias, env_type, schemas (no passwords)
        self._pools: dict[str, oracledb.ConnectionPool] = {}
        self._errors: dict[str, str | None] = {}
        self._call_timeout_ms = 60_000

    # ----- connect / disconnect -------------------------------------------------

    def connect(
        self,
        profile: dict[str, Any],
        passwords: dict[str, str],
        *,
        connect_timeout: int,
        call_timeout: int,
    ) -> dict[str, dict[str, Any]]:
        self.disconnect()
        results: dict[str, dict[str, Any]] = {}
        pools: dict[str, oracledb.ConnectionPool] = {}

        for name, cfg in profile["schemas"].items():
            password = passwords.get(name) or ""
            if not password:
                results[name] = {"ok": False, "error": None, "skipped": True}
                continue
            pool = None
            try:
                pool = oracledb.create_pool(
                    user=cfg["user"],
                    password=password,
                    dsn=_make_dsn(cfg),
                    min=0,
                    max=4,
                    increment=1,
                    getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
                    wait_timeout=(connect_timeout + call_timeout) * 1000,
                    tcp_connect_timeout=connect_timeout,
                    retry_count=0,
                    expire_time=5,          # keep-alive probe (minutes) through firewalls
                    program=PROGRAM_NAME,
                )
                with pool.acquire() as conn:
                    conn.call_timeout = call_timeout * 1000
                    conn.ping()
                pools[name] = pool
                results[name] = {"ok": True, "error": None, "skipped": False}
            except Exception as exc:  # noqa: BLE001 - any failure is reported per schema
                if pool is not None:
                    try:
                        pool.close(force=True)
                    except Exception:  # noqa: BLE001
                        pass
                results[name] = {"ok": False, "error": oracle_message(exc), "skipped": False}

        with self._lock:
            if pools:
                self._profile = profile
                self._pools = pools
                self._call_timeout_ms = call_timeout * 1000
                self._errors = {n: r["error"] for n, r in results.items()}
        return results

    def disconnect(self) -> None:
        with self._lock:
            pools, self._pools = self._pools, {}
            self._profile = None
            self._errors = {}
        for pool in pools.values():
            try:
                pool.close(force=True)
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    # ----- state ----------------------------------------------------------------

    def env_type(self) -> str | None:
        with self._lock:
            return self._profile["env_type"] if self._profile else None

    def profile(self) -> dict[str, Any] | None:
        with self._lock:
            return copy.deepcopy(self._profile)

    def connected_roles(self) -> set[str]:
        with self._lock:
            return set(self._pools)

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._profile is None:
                return {
                    "alias": None,
                    "env_type": None,
                    "is_production": False,
                    "allow_write": False,
                    "schemas": {},
                    "links": {},
                    "errors": {},
                }
            prod = self._profile["env_type"] == "PROD"
            links = {}
            if "fcubs" in self._pools:
                for role, obj in (self._profile.get("objects") or {}).items():
                    if obj.get("dblink") and role not in self._pools:
                        links[role] = obj["dblink"]
            return {
                "alias": self._profile["alias"],
                "env_type": self._profile["env_type"],
                "is_production": prod,
                "allow_write": not prod,
                "schemas": {n: n in self._pools for n in self._profile["schemas"]},
                "links": links,              # {role: dblink} reached through the FCUBS connection
                "errors": dict(self._errors),
            }

    def db_user(self, schema: str) -> str | None:
        with self._lock:
            if self._profile is None or schema not in self._profile["schemas"]:
                return None
            return self._profile["schemas"][schema]["user"]

    @contextmanager
    def acquire(self, schema: str) -> Iterator[oracledb.Connection]:
        """Borrow a connection for one schema, with the call timeout applied.
        On PROD the connection is inside a read-only transaction."""
        with self._lock:
            pool = self._pools.get(schema)
            timeout = self._call_timeout_ms
            read_only = bool(self._profile) and self._profile["env_type"] == "PROD"
        if pool is None:
            raise ServiceError(f"Schema '{schema}' is not connected")
        try:
            conn = pool.acquire()
        except oracledb.Error as exc:
            raise ServiceError(oracle_message(exc)) from exc
        try:
            conn.call_timeout = timeout
            conn.rollback()                      # clean transaction state
            if read_only:
                with conn.cursor() as cur:
                    cur.execute(_READ_ONLY_TXN)
            yield conn
        except oracledb.Error as exc:
            raise ServiceError(oracle_message(exc)) from exc
        finally:
            try:
                conn.rollback()                  # never leave anything uncommitted in the pool
            except Exception:  # noqa: BLE001
                pass
            try:
                pool.release(conn)
            except Exception:  # noqa: BLE001
                pass
