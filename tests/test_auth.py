"""auth.py のテスト。"""
import tempfile
import unittest
from pathlib import Path

import auth
import db
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestPasswordHash(unittest.TestCase):
    def test_ハッシュに元のパスワードが含まれない(self):
        self.assertNotIn("sakura2026", auth.hash_password("sakura2026"))

    def test_同じパスワードでもソルトにより毎回違うハッシュになる(self):
        self.assertNotEqual(auth.hash_password("sakura2026"), auth.hash_password("sakura2026"))

    def test_正しいパスワードは一致する(self):
        stored = auth.hash_password("sakura2026")
        self.assertTrue(auth.verify_password("sakura2026", stored))

    def test_違うパスワードは一致しない(self):
        stored = auth.hash_password("sakura2026")
        self.assertFalse(auth.verify_password("sakura2025", stored))
        self.assertFalse(auth.verify_password("", stored))

    def test_形式の違う保存値はエラーにならず一致しない(self):
        self.assertFalse(auth.verify_password("sakura2026", "未設定"))
        self.assertFalse(auth.verify_password("sakura2026", "md5$1$00$00"))


class TestAuthenticate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.passwords = seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_正しい社員番号とパスワードでログインできる(self):
        user = auth.authenticate("E0001", self.passwords["E0001"], self.db_path)
        self.assertEqual(user["name"], "佐藤 花子")
        self.assertEqual(user["role"], "admin")
        self.assertNotIn("password_hash", user)  # 画面側にハッシュを渡していないか

    def test_パスワードが違うとログインできない(self):
        self.assertIsNone(auth.authenticate("E0001", "wrong-password", self.db_path))

    def test_他人のパスワードではログインできない(self):
        self.assertIsNone(auth.authenticate("E0001", self.passwords["E0002"], self.db_path))

    def test_存在しない社員番号ではログインできない(self):
        self.assertIsNone(auth.authenticate("E9999", self.passwords["E0001"], self.db_path))

    def test_空欄ではログインできない(self):
        self.assertIsNone(auth.authenticate("", "", self.db_path))

    def test_退職した社員はログインできない(self):
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute("UPDATE employees SET is_active = 0 WHERE employee_code = 'E0002'")
        conn.close()
        self.assertIsNone(auth.authenticate("E0002", self.passwords["E0002"], self.db_path))


if __name__ == "__main__":
    unittest.main()
