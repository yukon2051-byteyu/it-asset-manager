"""devices.py の資産登録（register_device）のテスト。"""
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


def pc_input(**changes):
    """正しく登録できるPCの入力。changes で一部の項目だけ変えられる。"""
    data = {"category": "pc", "maker": "サンプル電機", "model": "NB-1600", "serial_no": "SN-NEW-0001",
            "os": "Windows 11", "purchase_date": datetime.date(2026, 11, 1),
            "warranty_end": datetime.date(2029, 10, 31)}
    data.update(changes)
    return data


class TestRegisterDevice(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def get_device(self, asset_no):
        conn = db.get_connection(self.db_path)
        try:
            return conn.execute("SELECT * FROM devices WHERE asset_no = ?", (asset_no,)).fetchone()
        finally:
            conn.close()

    def count_devices(self):
        conn = db.get_connection(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
        finally:
            conn.close()

    def assert_error(self, data, expected_message):
        """登録がエラーになり、期待したメッセージが含まれ、何も登録されないことを確かめる。"""
        with self.assertRaises(devices.ValidationError) as ctx:
            devices.register_device(data, self.db_path)
        self.assertIn(expected_message, ctx.exception.errors)
        self.assertEqual(self.count_devices(), 10)

    # --- 正常系 ---

    def test_PCを登録すると次の番号で在庫として登録される(self):
        asset_no = devices.register_device(pc_input(), self.db_path)
        self.assertEqual(asset_no, "PC-0007")  # サンプルには PC-0006 まである
        row = self.get_device(asset_no)
        self.assertEqual(row["status"], "in_stock")
        self.assertIsNone(row["employee_id"])
        self.assertEqual(row["purchase_date"], "2026-11-01")

    def test_種別ごとに番号が振られる(self):
        data = {"category": "monitor", "maker": "テスト工業", "model": "MN-32", "serial_no": "SN-NEW-MN", "screen_size": 32}
        self.assertEqual(devices.register_device(data, self.db_path), "MN-0003")

    def test_機器が1台もない種別は0001から始まる(self):
        empty_db = Path(self.tmpdir.name) / "empty.db"
        db.init_db(empty_db)
        self.assertEqual(devices.register_device(pc_input(), empty_db), "PC-0001")

    def test_続けて登録すると番号が1つずつ増える(self):
        first = devices.register_device(pc_input(serial_no="SN-A"), self.db_path)
        second = devices.register_device(pc_input(serial_no="SN-B"), self.db_path)
        self.assertEqual((first, second), ("PC-0007", "PC-0008"))

    def test_前後の空白は取り除かれ空欄はNoneになる(self):
        asset_no = devices.register_device(pc_input(maker="  サンプル電機  ", location="   "), self.db_path)
        row = self.get_device(asset_no)
        self.assertEqual(row["maker"], "サンプル電機")
        self.assertIsNone(row["location"])

    def test_種別に関係ない項目は保存されない(self):
        asset_no = devices.register_device(pc_input(phone_number="090-0000-9999"), self.db_path)
        self.assertIsNone(self.get_device(asset_no)["phone_number"])

    def test_任意項目が空欄でも登録できる(self):
        data = {"category": "smartphone", "maker": "架空モバイル", "model": "KM-20", "serial_no": "SN-NEW-SP"}
        self.assertEqual(devices.register_device(data, self.db_path), "SP-0003")

    # --- 異常系（入力ミス） ---

    def test_必須項目が空欄ならエラー(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            devices.register_device(pc_input(maker="", model="   ", serial_no=None), self.db_path)
        self.assertEqual(
            ctx.exception.errors,
            ["メーカーを入力してください。", "型番を入力してください。", "シリアル番号を入力してください。"],
        )

    def test_決められていない種別はエラー(self):
        self.assert_error(pc_input(category="printer"), "種別を選んでください。")

    def test_シリアル番号の重複はエラー(self):
        self.assert_error(pc_input(serial_no="SN-PC-0001"), "シリアル番号「SN-PC-0001」はすでに登録されています。")

    def test_IMEIの重複はエラー(self):
        data = {"category": "smartphone", "maker": "架空モバイル", "model": "KM-20", "serial_no": "SN-NEW-SP",
                "imei": "350000000000001"}
        self.assert_error(data, "IMEI「350000000000001」はすでに登録されています。")

    def test_IMEIが15桁の半角数字でなければエラー(self):
        base = {"category": "smartphone", "maker": "架空モバイル", "model": "KM-20", "serial_no": "SN-NEW-SP"}
        for imei in ["12345", "35000000000000A", "３５００００００００００００９"]:  # 桁不足・英字・全角数字
            with self.subTest(imei=imei):
                self.assert_error({**base, "imei": imei}, "IMEIは15桁の半角数字で入力してください。")

    def test_保証期限が購入日より前ならエラー(self):
        data = pc_input(purchase_date=datetime.date(2026, 11, 1), warranty_end=datetime.date(2026, 10, 1))
        self.assert_error(data, "保証期限は購入日より後の日付にしてください。")

    def test_画面サイズが0ならエラー(self):
        data = {"category": "monitor", "maker": "テスト工業", "model": "MN-32", "serial_no": "SN-NEW-MN", "screen_size": 0}
        self.assert_error(data, "画面サイズは0より大きい値を入力してください。")


if __name__ == "__main__":
    unittest.main()
