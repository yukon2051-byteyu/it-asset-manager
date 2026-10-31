"""exports.py（CSV・Excel出力）と、機器の詳細表示に使う処理のテスト。"""
import io
import tempfile
import unittest
from pathlib import Path

import openpyxl
import pandas as pd

import audit
import auth
import devices
import exports
import licenses
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestExports(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame([
            {"資産番号": "PC-0001", "利用者": "鈴木 一郎", "IMEI": "012345678901234"},
            {"資産番号": "SP-0001", "利用者": "", "IMEI": "350000000000001"},
        ])

    def test_CSVは文字化け防止のBOM付きで日本語と先頭の0が残る(self):
        content = exports.to_csv_bytes(self.df)
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        back = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", dtype=str, keep_default_na=False)
        self.assertEqual(back.to_dict("records"), self.df.to_dict("records"))

    def test_Excelに同じ内容とシート名で出力される(self):
        content = exports.to_excel_bytes(self.df, "資産一覧")
        book = openpyxl.load_workbook(io.BytesIO(content))
        self.assertEqual(book.sheetnames, ["資産一覧"])
        back = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
        self.assertEqual(back.to_dict("records"), self.df.to_dict("records"))

    def test_Excelの列幅は中身に合わせて広がる(self):
        sheet = openpyxl.load_workbook(io.BytesIO(exports.to_excel_bytes(self.df))).active
        self.assertGreater(sheet.column_dimensions["C"].width, sheet.column_dimensions["A"].width)

    def test_空の表も出力できる(self):
        empty = pd.DataFrame(columns=["資産番号", "利用者"])
        self.assertTrue(exports.to_csv_bytes(empty).decode("utf-8-sig").startswith("資産番号,利用者"))
        self.assertGreater(len(exports.to_excel_bytes(empty)), 0)


class TestDeviceDetail(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def detail(self, asset_no):
        rows = devices.device_detail_rows(devices.get_device(asset_no, self.db_path))
        return {r["項目"]: r["値"] for r in rows}

    def test_PCの詳細にはOSが出てIMEIは出ない(self):
        detail = self.detail("PC-0001")
        self.assertEqual((detail["状態"], detail["利用者"], detail["OS"]), ("使用中", "鈴木 一郎", "Windows 11"))
        self.assertNotIn("IMEI", detail)

    def test_スマホの詳細にはIMEIと電話番号が出る(self):
        detail = self.detail("SP-0001")
        self.assertEqual((detail["IMEI"], detail["電話番号"]), ("350000000000001", "090-0000-0001"))
        self.assertNotIn("OS", detail)

    def test_利用者がいない機器はなしと表示する(self):
        self.assertEqual(self.detail("PC-0005")["利用者"], "なし")

    def test_PCに割り当てたライセンスがわかる(self):
        device = devices.get_device("PC-0001", self.db_path)
        self.assertEqual(licenses.list_device_licenses(device["id"], self.db_path), ["ウイルス対策ソフト"])
        pc5 = devices.get_device("PC-0005", self.db_path)
        self.assertEqual(licenses.list_device_licenses(pc5["id"], self.db_path), [])

    def test_履歴はその機器のものだけを正確に絞り込める(self):
        devices.change_status("PC-0005", "assign", 3, self.db_path, actor_id=1)
        devices.change_status("PC-0006", "repaired", None, self.db_path, actor_id=1)
        pc5 = devices.get_device("PC-0005", self.db_path)
        logs = audit.list_logs(target_type="device", target_id=pc5["id"], db_path=self.db_path)
        self.assertEqual([log["対象名"] for log in logs], ["PC-0005"])


if __name__ == "__main__":
    unittest.main()
