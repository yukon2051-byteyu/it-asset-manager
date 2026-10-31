"""licenses.py のテスト。seed.py のサンプルデータを使う。"""
import tempfile
import unittest
from pathlib import Path

import db
import licenses
import seed


class TestListLicensesOfEmployee(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def names(self, employee_id):
        return [r["ライセンス名"] for r in licenses.list_licenses_of_employee(employee_id, self.db_path)]

    def test_社員本人に割り当てたライセンスが出る(self):
        # E0002（id=2）には文書作成ソフトとPDF編集ソフトを割り当てている
        self.assertEqual(self.names(2), ["PDF編集ソフト", "文書作成ソフト"])

    def test_ライセンスのない社員は空のリスト(self):
        # E0005（id=5）にはライセンスを割り当てていない
        self.assertEqual(self.names(5), [])

    def test_解除済みの割当は出ない(self):
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute(
                "UPDATE license_assignments SET released_on = '2026-10-31'"
                " WHERE employee_id = 2 AND license_id = (SELECT id FROM licenses WHERE name = 'PDF編集ソフト')"
            )
        conn.close()
        self.assertEqual(self.names(2), ["文書作成ソフト"])

    def test_期限のないライセンスは期限なしと表示される(self):
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute("UPDATE licenses SET expires_on = NULL WHERE name = '文書作成ソフト'")
        conn.close()
        row = [r for r in licenses.list_licenses_of_employee(2, self.db_path) if r["ライセンス名"] == "文書作成ソフト"][0]
        self.assertEqual(row["有効期限"], "期限なし")


if __name__ == "__main__":
    unittest.main()
