"""employees.py のテスト。"""
import tempfile
import unittest
from pathlib import Path

import db
import employees
import seed


class TestListEmployees(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_在籍中の社員が社員番号順に出る(self):
        seed.seed(self.db_path)
        codes = [e["employee_code"] for e in employees.list_employees(self.db_path)]
        self.assertEqual(codes, ["E0001", "E0002", "E0003", "E0004", "E0005"])

    def test_退職した社員は出ない(self):
        seed.seed(self.db_path)
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute("UPDATE employees SET is_active = 0 WHERE employee_code = 'E0003'")
        conn.close()
        codes = [e["employee_code"] for e in employees.list_employees(self.db_path)]
        self.assertNotIn("E0003", codes)
        self.assertEqual(len(codes), 4)

    def test_社員がいなければ空のリスト(self):
        db.init_db(self.db_path)
        self.assertEqual(employees.list_employees(self.db_path), [])


if __name__ == "__main__":
    unittest.main()
