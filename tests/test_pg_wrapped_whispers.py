"""
Test PostgreSQL implementation of wrapped whispers.

Directly imports from database.pg_wrapped_whispers and verifies
that all SQL queries use %s placeholders (not ?) and that the
functions work correctly with a mocked PostgreSQL connection.
"""
import unittest
from unittest.mock import MagicMock, patch
from database.pg_wrapped_whispers import (
    create_inline_package,
    get_inline_package,
    delete_inline_package,
)


class TestPgInlinePackage(unittest.TestCase):
    """Verify PostgreSQL inline package functions use %s placeholders."""

    def setUp(self):
        self.patcher = patch(
            "database.pg_wrapped_whispers._pg_get_conn",
            autospec=True,
        )
        self.mock_get_conn = self.patcher.start()
        self.mock_conn = MagicMock()
        self.mock_get_conn.return_value.__enter__.return_value = self.mock_conn

    def tearDown(self):
        self.patcher.stop()

    def test_create_inline_package_uses_ps_placeholders(self):
        pkg_id = create_inline_package(10001, "cover_test", "char_test", "hello")
        self.assertIsNotNone(pkg_id)
        self.assertEqual(len(pkg_id), 8)
        self.assertTrue(pkg_id.isalnum())

        self.mock_conn.execute.assert_called_once()
        sql, params = self.mock_conn.execute.call_args[0]
        self.assertIn("%s", sql, "PostgreSQL query must use %%s placeholders")
        self.assertNotIn("?", sql, "PostgreSQL query must NOT use ? placeholders")
        self.assertEqual(params, (pkg_id, 10001, "cover_test", "char_test", "hello"))

    def test_get_inline_package_uses_ps_placeholders(self):
        self.mock_conn.execute.return_value.fetchone.return_value = None
        result = get_inline_package("PKG12345")
        self.assertIsNone(result)

        self.mock_conn.execute.assert_called_once()
        sql, params = self.mock_conn.execute.call_args[0]
        self.assertIn("%s", sql)
        self.assertNotIn("?", sql)
        self.assertEqual(params, ("PKG12345",))

    def test_delete_inline_package_uses_ps_placeholders(self):
        delete_inline_package("PKG12345")

        self.mock_conn.execute.assert_called_once()
        sql, params = self.mock_conn.execute.call_args[0]
        self.assertIn("%s", sql)
        self.assertNotIn("?", sql)
        self.assertEqual(params, ("PKG12345",))


class TestPgNoQuestionMarkInSource(unittest.TestCase):
    """Static analysis: scan pg_wrapped_whispers.py for ? in SQL strings."""

    def test_no_sqlite_placeholders_in_pg_file(self):
        import ast
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "database", "pg_wrapped_whispers.py",
        )
        with open(path) as f:
            tree = ast.parse(f.read())

        sql_strings = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value.strip().upper()
                if val.startswith("SELECT") or val.startswith("INSERT") or \
                   val.startswith("UPDATE") or val.startswith("DELETE") or \
                   val.startswith("CREATE"):
                    sql_strings.append(node.value)

        self.assertGreater(len(sql_strings), 0, "No SQL statements found")

        for sql in sql_strings:
            with self.subTest(sql=sql[:60]):
                self.assertNotIn(
                    "?", sql,
                    f"Found SQLite ? placeholder in pg_wrapped_whispers.py:\n{sql}",
                )


if __name__ == "__main__":
    unittest.main()
