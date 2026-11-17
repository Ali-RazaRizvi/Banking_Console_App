"""Registry and runner tests (no database needed)."""
import datetime as dt
import decimal
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import registry, runner  # noqa: E402
from backend.errors import ServiceError  # noqa: E402


def entry(**overrides):
    raw = {
        "id": "q1", "title": "Q", "page": "dashboard", "group": "G",
        "schema": "obbrn", "type": "read",
        "sql": "SELECT * FROM {{obbrn:SRV_TB_BC_TELLER_BATCH}} WHERE branch_code = :branch_code",
        "params": [{"name": "branch_code", "label": "Branch"}],
    }
    raw.update(overrides)
    return registry._validate(raw, "test")


class FakeManager:
    def __init__(self, connected, objects):
        self._connected = set(connected)
        self._profile = {"alias": "X", "env_type": "UAT", "schemas": {}, "objects": objects}

    def profile(self):
        return self._profile

    def connected_roles(self):
        return self._connected

    def env_type(self):
        return "UAT"


class RealQueryFiles(unittest.TestCase):
    def test_shipped_files_load(self):
        entries = registry.load()
        ids = [e["id"] for e in entries]
        for name in ("branch", "vault", "teller"):
            self.assertIn(f"open_{name}_batch_count", ids)
            self.assertIn(f"open_{name}_batches", ids)
            self.assertIn(f"{name}_batches_by_branch", ids)
        groups = list(dict.fromkeys(e["group"] for e in entries if e["page"] == "dashboard"))
        self.assertEqual(groups[:4], ["Open Batches", "Branch Batch", "Vault Batch", "Teller Batch"])


class Validation(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(entry()["params"][0]["required"], True)

    def test_undeclared_bind(self):
        with self.assertRaisesRegex(ServiceError, ":branch_code"):
            entry(params=[])

    def test_unused_param(self):
        with self.assertRaisesRegex(ServiceError, "not used"):
            entry(sql="SELECT 1 FROM dual")

    def test_colon_in_string_is_not_a_bind(self):
        entry(sql="SELECT TO_CHAR(SYSDATE, 'HH24:MI') FROM dual", params=[])

    def test_read_must_be_select(self):
        with self.assertRaisesRegex(ServiceError, "SELECT or WITH"):
            entry(sql="DELETE FROM x WHERE a = :branch_code")

    def test_unknown_field_rejected(self):
        with self.assertRaisesRegex(ServiceError, "unknown field"):
            entry(colour="red")

    def test_bad_template_schema(self):
        with self.assertRaisesRegex(ServiceError, "unknown schema"):
            entry(sql="SELECT * FROM {{xyz:T}} WHERE a = :branch_code")

    def test_write_needs_confirm(self):
        with self.assertRaisesRegex(ServiceError, "confirm"):
            entry(type="write", sql="DELETE FROM t WHERE a = :branch_code")


class Routing(unittest.TestCase):
    def test_via_dblink_from_fcubs(self):
        m = FakeManager({"fcubs"}, {"obbrn": {"owner": "OBTRANSACTION", "dblink": "OBBUAT"}})
        role, sql = runner.plan(entry(), m)
        self.assertEqual(role, "fcubs")
        self.assertIn("OBTRANSACTION.SRV_TB_BC_TELLER_BATCH@OBBUAT", sql)

    def test_direct_connection_wins(self):
        m = FakeManager({"fcubs", "obbrn"}, {"obbrn": {"owner": "OBTRANSACTION", "dblink": "OBBUAT"}})
        role, sql = runner.plan(entry(), m)
        self.assertEqual(role, "obbrn")
        self.assertIn("OBTRANSACTION.SRV_TB_BC_TELLER_BATCH ", sql + " ")
        self.assertNotIn("@", sql)

    def test_not_reachable(self):
        m = FakeManager({"fcubs"}, {})
        ok, reason = runner.availability(entry(), m)
        self.assertFalse(ok)
        self.assertIn("not connected", reason)


class Binds(unittest.TestCase):
    def test_required_and_optional(self):
        e = entry(
            sql="SELECT * FROM t WHERE b = :branch_code AND (:d IS NULL OR TRUNC(x) = :d)",
            params=[{"name": "branch_code", "label": "Branch"},
                    {"name": "d", "label": "Date", "type": "date", "required": False}],
        )
        with self.assertRaisesRegex(ServiceError, "required"):
            runner.build_binds(e, {})
        b = runner.build_binds(e, {"branch_code": " 005 ", "d": ""})
        self.assertEqual(b, {"branch_code": "005", "d": None})
        b = runner.build_binds(e, {"branch_code": "005", "d": "29-Oct-2026"})
        self.assertEqual(b["d"], dt.datetime(2026, 10, 29))
        b = runner.build_binds(e, {"branch_code": "005", "d": "2026-10-29"})
        self.assertEqual(b["d"], dt.datetime(2026, 10, 29))
        with self.assertRaisesRegex(ServiceError, "date"):
            runner.build_binds(e, {"branch_code": "005", "d": "yesterday"})


class Conversion(unittest.TestCase):
    def test_values(self):
        self.assertEqual(runner.to_plain(dt.datetime(2026, 10, 29)), "29-OCT-2026")
        self.assertEqual(runner.to_plain(dt.datetime(2026, 10, 29, 14, 5, 0)), "29-OCT-2026 14:05:00")
        self.assertEqual(runner.to_plain(decimal.Decimal("5")), 5)
        self.assertEqual(runner.to_plain(decimal.Decimal("5.5")), 5.5)
        self.assertEqual(runner.to_plain(b"abc"), "<3 bytes>")
        self.assertIsNone(runner.to_plain(None))


if __name__ == "__main__":
    unittest.main()
