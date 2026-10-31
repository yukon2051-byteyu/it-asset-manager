"""db.py のテスト。

実行方法（プロジェクトのフォルダで）:
    .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

import db


class TestDatabase(unittest.TestCase):
    def setUp(self):
        # 本番の assets.db を汚さないよう、テストごとに一時フォルダへ新しいDBを作る
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        # Windowsでは接続を閉じないと一時ファイルを削除できない
        self.conn.close()
        self.tmpdir.cleanup()

    # --- テスト用データを登録する補助関数（データはすべて架空） ---

    def add_employee(self, code="E0001", email="yamada@example.com", role="user"):
        cur = self.conn.execute(
            "INSERT INTO employees (employee_code, name, department, email, role, password_hash)"
            " VALUES (?, '山田 太郎', '営業部', ?, ?, 'dummy-hash')",
            (code, email, role),
        )
        return cur.lastrowid

    def add_device(self, asset_no="PC-0001", serial_no="SN0001", employee_id=None, status="in_stock"):
        cur = self.conn.execute(
            "INSERT INTO devices (asset_no, category, maker, model, serial_no, status, employee_id)"
            " VALUES (?, 'pc', 'サンプル電機', 'NB-1400', ?, ?, ?)",
            (asset_no, serial_no, status, employee_id),
        )
        return cur.lastrowid

    def add_license(self, quantity=3):
        cur = self.conn.execute(
            "INSERT INTO licenses (name, license_type, quantity) VALUES ('文書作成ソフト', 'user', ?)",
            (quantity,),
        )
        return cur.lastrowid

    def assign(self, license_id, employee_id=None, device_id=None, released_on=None):
        self.conn.execute(
            "INSERT INTO license_assignments (license_id, employee_id, device_id, assigned_on, released_on)"
            " VALUES (?, ?, ?, '2026-11-01', ?)",
            (license_id, employee_id, device_id, released_on),
        )

    # --- 正常系 ---

    def test_5つのテーブルが作られる(self):
        rows = self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        names = {row["name"] for row in rows}
        self.assertEqual(names, {"employees", "devices", "licenses", "license_assignments", "requests"})

    def test_init_dbを2回実行してもデータが消えない(self):
        self.add_employee()
        self.conn.commit()
        db.init_db(self.db_path)
        count = self.conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        self.assertEqual(count, 1)

    def test_機器を社員に割り当てて登録できる(self):
        emp_id = self.add_employee()
        self.add_device(employee_id=emp_id, status="in_use")
        row = self.conn.execute("SELECT * FROM devices WHERE asset_no = 'PC-0001'").fetchone()
        self.assertEqual(row["employee_id"], emp_id)
        self.assertEqual(row["status"], "in_use")

    def test_状態を指定しないと在庫になる(self):
        self.conn.execute(
            "INSERT INTO devices (asset_no, category, maker, model, serial_no)"
            " VALUES ('MN-0001', 'monitor', 'サンプル電機', 'MN-24', 'SN9001')"
        )
        row = self.conn.execute("SELECT status FROM devices").fetchone()
        self.assertEqual(row["status"], "in_stock")

    def test_ライセンスの残数を計算できる(self):
        lic_id = self.add_license(quantity=3)
        emp1 = self.add_employee("E0001", "a@example.com")
        emp2 = self.add_employee("E0002", "b@example.com")
        self.assign(lic_id, employee_id=emp1)
        self.assign(lic_id, employee_id=emp2, released_on="2026-11-10")  # 解除済みは数えない
        row = self.conn.execute(
            "SELECT l.quantity - COUNT(a.id) AS remaining"
            " FROM licenses l"
            " LEFT JOIN license_assignments a ON a.license_id = l.id AND a.released_on IS NULL"
            " WHERE l.id = ? GROUP BY l.id",
            (lic_id,),
        ).fetchone()
        self.assertEqual(row["remaining"], 2)

    # --- 異常系（入力ミスがエラーになるか） ---

    def test_社員番号の重複はエラー(self):
        self.add_employee("E0001", "a@example.com")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_employee("E0001", "b@example.com")

    def test_資産番号の重複はエラー(self):
        self.add_device("PC-0001", "SN0001")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_device("PC-0001", "SN0002")

    def test_シリアル番号の重複はエラー(self):
        self.add_device("PC-0001", "SN0001")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_device("PC-0002", "SN0001")

    def test_必須項目の空欄はエラー(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO employees (employee_code, name, department, email, role, password_hash)"
                " VALUES ('E0001', NULL, '営業部', 'a@example.com', 'user', 'dummy-hash')"
            )

    def test_決められていない権限はエラー(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_employee(role="superuser")

    def test_決められていない状態はエラー(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_device(status="broken")

    def test_存在しない社員への割当はエラー(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_device(employee_id=999)

    def test_保有数0のライセンスはエラー(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_license(quantity=0)

    def test_割当先が空欄のライセンス割当はエラー(self):
        lic_id = self.add_license()
        with self.assertRaises(sqlite3.IntegrityError):
            self.assign(lic_id)

    def test_社員とPCの両方へのライセンス割当はエラー(self):
        lic_id = self.add_license()
        emp_id = self.add_employee()
        dev_id = self.add_device()
        with self.assertRaises(sqlite3.IntegrityError):
            self.assign(lic_id, employee_id=emp_id, device_id=dev_id)


if __name__ == "__main__":
    unittest.main()
