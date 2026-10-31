"""seed.py のテスト。"""
import tempfile
import unittest
from pathlib import Path

import auth
import db
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


if __name__ == "__main__":
    unittest.main()
