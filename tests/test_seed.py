"""seed.py のテスト。"""
import tempfile
import unittest
from pathlib import Path

import auth
import db
import employees
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestSeed(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"

    def tearDown(self):
        self.tmpdir.cleanup()

    def count(self, table):
        conn = db.get_connection(self.db_path)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def test_決めた件数のデータが登録される(self):
        self.assertIsNotNone(seed.seed(self.db_path))
        self.assertEqual(self.count("employees"), 5)
        self.assertEqual(self.count("devices"), 10)
        self.assertEqual(self.count("licenses"), 3)
        self.assertEqual(self.count("license_assignments"), 10)

    def test_2回目の実行では二重登録しない(self):
        seed.seed(self.db_path)
        self.assertIsNone(seed.seed(self.db_path))
        self.assertEqual(self.count("employees"), 5)

    def test_返されたパスワードで全員ログインできる(self):
        passwords = seed.seed(self.db_path)
        self.assertEqual(len(passwords), 5)
        for code, password in passwords.items():
            self.assertEqual(len(password), 12)
            self.assertIsNotNone(auth.authenticate(code, password, self.db_path), code)

    def test_パスワードはそのまま保存されない(self):
        passwords = seed.seed(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            stored = [r[0] for r in conn.execute("SELECT password_hash FROM employees")]
        finally:
            conn.close()
        for password in passwords.values():
            self.assertNotIn(password, stored)
        self.assertTrue(all(s.startswith("pbkdf2_sha256$") for s in stored))

    def test_使用中と貸出中の機器には必ず利用者がいる(self):
        # 設計書 4.2 のルール（プログラム側で守る）をサンプルデータが満たしているか
        seed.seed(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM devices WHERE status IN ('in_use', 'lent') AND employee_id IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_ライセンスの割当数が保有数を超えない(self):
        seed.seed(self.db_path)
        conn = db.get_connection(self.db_path)
        try:
            over = conn.execute(
                "SELECT l.name FROM licenses l"
                " JOIN license_assignments a ON a.license_id = l.id AND a.released_on IS NULL"
                " GROUP BY l.id HAVING COUNT(a.id) > l.quantity"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(over, [])


class TestSampleEmployees(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def rows(self):
        conn = db.get_connection(self.db_path)
        try:
            return conn.execute("SELECT * FROM employees ORDER BY employee_code").fetchall()
        finally:
            conn.close()

    def test_合計100人になり社員番号とメールアドレスは重ならない(self):
        self.assertEqual(seed.add_sample_employees(self.db_path), 95)
        rows = self.rows()
        self.assertEqual([r["employee_code"] for r in rows], [f"E{n:04d}" for n in range(1, 101)])
        self.assertEqual(len({r["email"] for r in rows}), 100)
        self.assertEqual(len({r["name"] for r in rows}), 100)  # 氏名も重ならない

    def test_8つの部署に振り分けられ管理者は3人になる(self):
        seed.add_sample_employees(self.db_path)
        rows = self.rows()
        self.assertEqual(len({r["department"] for r in rows}), 8)
        admins = [r["employee_code"] for r in rows if r["role"] == "admin"]
        self.assertEqual(len(admins), 3)  # 佐藤さん＋情報システム部の2人

    def test_何度実行しても二重登録しない(self):
        seed.add_sample_employees(self.db_path)
        self.assertEqual(seed.add_sample_employees(self.db_path), 0)
        self.assertEqual(len(self.rows()), 100)

    def test_すでに登録した社員番号は飛ばす(self):
        employees.register_employee({"employee_code": "E0006", "name": "山田 太郎", "department": "人事部",
                                     "email": "yamada@example.com", "role": "user"}, self.db_path)
        self.assertEqual(seed.add_sample_employees(self.db_path), 94)
        e0006 = [r for r in self.rows() if r["employee_code"] == "E0006"][0]
        self.assertEqual(e0006["name"], "山田 太郎")  # 上書きされていない

    def test_追加した社員はパスワード未発行で再発行するとログインできる(self):
        seed.add_sample_employees(self.db_path)
        e0050 = [r for r in self.rows() if r["employee_code"] == "E0050"][0]
        self.assertIsNone(auth.authenticate("E0050", "未発行", self.db_path))
        self.assertIsNone(auth.authenticate("E0050", "", self.db_path))
        listed = {r["社員番号"]: r for r in employees.list_all_employees(self.db_path)}
        self.assertEqual((listed["E0001"]["パスワード"], listed["E0050"]["パスワード"]), ("発行済", "未発行"))

        password = employees.reset_password(e0050["id"], self.db_path)
        self.assertIsNotNone(auth.authenticate("E0050", password, self.db_path))

    def test_基本のサンプルがなくても追加できる(self):
        empty = Path(self.tmpdir.name) / "empty.db"
        self.assertEqual(seed.add_sample_employees(empty), 95)


if __name__ == "__main__":
    unittest.main()
