"""Policy tests. Run with:  .venv\\Scripts\\python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import policy  # noqa: E402
from backend.errors import ServiceError  # noqa: E402

FORBIDDEN = ["ACTB_DAILY_LOG", "STTM_DATES", "GLTB_*"]


def ok(sql, env="UAT"):
    return policy.check(sql, env, FORBIDDEN)


class ReadQueries(unittest.TestCase):
    def test_select_allowed_everywhere(self):
        for env in ("UAT", "PROD"):
            self.assertEqual(ok("SELECT * FROM sttm_dates", env).kind, "read")
            self.assertEqual(ok("WITH x AS (SELECT 1 FROM dual) SELECT * FROM x", env).kind, "read")

    def test_select_from_forbidden_table_is_fine(self):
        self.assertEqual(ok("SELECT * FROM actb_daily_log WHERE ROWNUM < 5", "UAT").kind, "read")

    def test_for_update_refused(self):
        with self.assertRaises(ServiceError):
            ok("SELECT * FROM smtb_user FOR UPDATE", "PROD")

    def test_keywords_in_strings_and_comments_ignored(self):
        ok("SELECT 'DELETE FROM x; DROP' AS t FROM dual -- ; update y", "PROD")
        ok("SELECT q'[it's; ok]' FROM dual /* ; */", "PROD")


class ProdIsReadOnly(unittest.TestCase):
    def test_dml_refused_on_prod(self):
        for sql in (
            "UPDATE smtb_user SET user_status = 'E' WHERE user_id = :u",
            "DELETE FROM smtb_current_users WHERE user_id = :u",
            "INSERT INTO cstb_debug_users (user_id) VALUES (:u)",
            "MERGE INTO t USING dual ON (1=1) WHEN MATCHED THEN UPDATE SET a = 1",
            "BEGIN pkg.proc(:u); END;",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(ServiceError, "PROD is read-only"):
                ok(sql, "PROD")


class UatForbiddenTables(unittest.TestCase):
    def test_allowed_dml(self):
        st = ok("UPDATE cstb_debug_users SET debug = 'Y' WHERE user_id = :u")
        self.assertEqual(st.targets, ["CSTB_DEBUG_USERS"])
        self.assertEqual(ok("DELETE FROM smtb_current_users WHERE user_id = :u").targets, ["SMTB_CURRENT_USERS"])
        self.assertEqual(ok("DELETE smtb_current_users WHERE user_id = :u").targets, ["SMTB_CURRENT_USERS"])

    def test_forbidden_targets(self):
        for sql in (
            "UPDATE sttm_dates SET today = SYSDATE",
            "update FLEXCUBE.sttm_dates set today = sysdate",
            'UPDATE "STTM_DATES" SET today = SYSDATE',
            "DELETE FROM actb_daily_log WHERE 1=1",
            "INSERT INTO gltb_gl_bal (gl_code) VALUES (:g)",
            "INSERT ALL INTO cstb_debug_users (user_id) VALUES (:u) INTO actb_daily_log (x) VALUES (1) SELECT * FROM dual",
            "MERGE INTO sttm_dates d USING dual ON (1=1) WHEN MATCHED THEN UPDATE SET today = SYSDATE",
            "UPDATE /*+ index(x) */ sttm_dates x SET today = SYSDATE",
        ):
            with self.subTest(sql=sql), self.assertRaisesRegex(ServiceError, "forbidden"):
                ok(sql)

    def test_unknown_target_refused(self):
        for sql in (
            "UPDATE (SELECT * FROM sttm_dates) SET today = SYSDATE",
            "DELETE FROM (SELECT * FROM actb_daily_log)",
        ):
            with self.subTest(sql=sql), self.assertRaises(ServiceError):
                ok(sql)

    def test_dblink_refused(self):
        with self.assertRaisesRegex(ServiceError, "database link"):
            ok("UPDATE cstb_debug_users@remote SET debug = 'Y'")

    def test_plsql_mentioning_forbidden_table_refused(self):
        with self.assertRaisesRegex(ServiceError, "STTM_DATES"):
            ok("BEGIN UPDATE sttm_dates SET today = SYSDATE; END;")
        ok("BEGIN pkg.reset_password(:u); END;")


class AlwaysRefused(unittest.TestCase):
    def test_ddl_and_others(self):
        for sql in ("DROP TABLE x", "TRUNCATE TABLE cstb_debug_users", "ALTER SESSION SET x = 1",
                    "GRANT SELECT ON x TO y", "LOCK TABLE x IN EXCLUSIVE MODE", "COMMIT"):
            for env in ("UAT", "PROD"):
                with self.subTest(sql=sql, env=env), self.assertRaises(ServiceError):
                    ok(sql, env)

    def test_multiple_statements(self):
        with self.assertRaisesRegex(ServiceError, "one SQL statement"):
            ok("SELECT 1 FROM dual; DELETE FROM cstb_debug_users", "UAT")
        with self.assertRaises(ServiceError):
            ok("DELETE FROM cstb_debug_users WHERE user_id = :u;", "UAT")

    def test_empty(self):
        with self.assertRaises(ServiceError):
            ok("   -- nothing\n", "UAT")


if __name__ == "__main__":
    unittest.main()
