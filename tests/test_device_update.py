"""devices.py の資産更新（get_device / update_device）のテスト。"""
import datetime
import tempfile
import unittest
from pathlib import Path

import auth
import db
import devices
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestUpdateDevice(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def get(self, asset_no):
        return devices.get_device(asset_no, self.db_path)

    def update(self, asset_no, **changes):
        """今の値をもとに、changes の項目だけ変えて更新する（画面で一部だけ書き換えるのと同じ）。"""
        devices.update_device(asset_no, {**self.get(asset_no), **changes}, self.db_path)

    def run_sql(self, sql):
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute(sql)
        conn.close()

    # --- get_device ---

    def test_機器1台の情報を取得できる(self):
        device = self.get("PC-0004")
        self.assertEqual(device["maker"], "テスト工業")
        self.assertEqual(device["user_name"], "伊藤 さくら")

    def test_日付はdate型で返る(self):
        self.update("PC-0005", purchase_date=datetime.date(2026, 4, 1))
        self.assertEqual(self.get("PC-0005")["purchase_date"], datetime.date(2026, 4, 1))

    def test_存在しない資産番号はNone(self):
        self.assertIsNone(self.get("PC-9999"))

    # --- 正常系 ---

    def test_設置場所を変更できる(self):
        self.update("PC-0005", location="本社4F")
        self.assertEqual(self.get("PC-0005")["location"], "本社4F")

    def test_更新日時が記録される(self):
        self.run_sql("UPDATE devices SET updated_at = '2000-01-01 00:00:00' WHERE asset_no = 'PC-0005'")
        self.update("PC-0005", location="本社4F")
        self.assertNotEqual(self.get("PC-0005")["updated_at"], "2000-01-01 00:00:00")

    def test_自分と同じシリアル番号やIMEIのままでも更新できる(self):
        # 重複チェックから自分自身を除けているか
        self.update("SP-0001", carrier="テスト通信")
        self.assertEqual(self.get("SP-0001")["carrier"], "テスト通信")

    def test_種別状態利用者は変わらない(self):
        before = self.get("PC-0001")
        self.update("PC-0001", category="monitor", status="disposed", employee_id=None, location="本社5F")
        after = self.get("PC-0001")
        self.assertEqual(after["category"], "pc")
        self.assertEqual(after["status"], "in_use")
        self.assertEqual(after["employee_id"], before["employee_id"])
        self.assertEqual(after["location"], "本社5F")

    def test_任意項目を空欄に戻せる(self):
        self.update("PC-0001", location="")
        self.assertIsNone(self.get("PC-0001")["location"])

    # --- 異常系 ---

    def test_必須項目を空欄にするとエラーで変更されない(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            self.update("PC-0001", maker="")
        self.assertIn("メーカーを入力してください。", ctx.exception.errors)
        self.assertEqual(self.get("PC-0001")["maker"], "サンプル電機")

    def test_ほかの機器と同じシリアル番号はエラー(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            self.update("PC-0001", serial_no="SN-PC-0002")
        self.assertIn("シリアル番号「SN-PC-0002」はすでに登録されています。", ctx.exception.errors)

    def test_ほかの機器と同じIMEIはエラー(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            self.update("SP-0001", imei="350000000000002")
        self.assertIn("IMEI「350000000000002」はすでに登録されています。", ctx.exception.errors)

    def test_存在しない資産番号はエラー(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            devices.update_device("PC-9999", {"maker": "x", "model": "x", "serial_no": "x"}, self.db_path)
        self.assertEqual(ctx.exception.errors, ["資産番号「PC-9999」の機器が見つかりません。"])

    def test_廃棄済の機器は変更できない(self):
        self.run_sql("UPDATE devices SET status = 'disposed' WHERE asset_no = 'PC-0006'")
        with self.assertRaises(devices.ValidationError) as ctx:
            self.update("PC-0006", location="倉庫")
        self.assertEqual(ctx.exception.errors, ["廃棄済の機器は変更できません。"])


if __name__ == "__main__":
    unittest.main()
