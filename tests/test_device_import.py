"""device_import.py（CSV一括登録）のテスト。"""
import tempfile
import unittest
from pathlib import Path

import auth
import db
import device_import
import devices
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

SAMPLES = Path(__file__).parent.parent / "samples"
HEADER = "資産番号,種別,メーカー,型番,シリアル番号,利用者の社員番号,購入日,画面サイズ,IMEI"


def csv_bytes(*rows, header=HEADER, encoding="utf-8"):
    return "\r\n".join([header, *rows]).encode(encoding)


class TestDeviceImport(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def import_(self, content):
        return device_import.import_csv(content, self.db_path)

    def count_devices(self):
        return len(devices.list_devices(db_path=self.db_path))

    def assert_rejected(self, content, expected_message):
        """取り込みがエラーになり、期待したメッセージが含まれ、1件も登録されないことを確かめる。"""
        with self.assertRaises(devices.ValidationError) as ctx:
            self.import_(content)
        self.assertIn(expected_message, ctx.exception.errors)
        self.assertEqual(self.count_devices(), 10)

    # --- 正常系 ---

    def test_ひな形には全部の列名があり文字化け防止のBOMが付く(self):
        content = device_import.template_csv()
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(content.decode("utf-8-sig").strip().split(","), list(device_import.COLUMNS))

    def test_サンプルファイルを取り込める(self):
        asset_nos = self.import_((SAMPLES / "devices_import_sample.csv").read_bytes())
        self.assertEqual(asset_nos, ["PC-0101", "PC-0102", "MN-0003", "SP-0050"])
        self.assertEqual(self.count_devices(), 14)

    def test_台帳の資産番号を引き継ぎ空欄は指定番号の続きから振る(self):
        asset_nos = self.import_(csv_bytes(
            "PC-0101,PC,サンプル電機,NB-1,SN-A,,,,",
            ",PC,サンプル電機,NB-1,SN-B,,,,",
            ",モニター,テスト工業,MN-1,SN-C,,,24,",
        ))
        self.assertEqual(asset_nos, ["PC-0101", "PC-0102", "MN-0003"])

    def test_利用者の社員番号があれば使用中になる(self):
        self.import_(csv_bytes("PC-0101,PC,サンプル電機,NB-1,SN-A,E0003,,,", ",PC,サンプル電機,NB-1,SN-B,,,,"))
        with_user = devices.get_device("PC-0101", self.db_path)
        self.assertEqual((with_user["status"], with_user["user_name"]), ("in_use", "高橋 美咲"))
        self.assertEqual(devices.get_device("PC-0102", self.db_path)["status"], "in_stock")

    def test_ShiftJISのファイルも読める(self):
        self.import_(csv_bytes(",PC,サンプル電機,NB-1,SN-A,,,,", encoding="cp932"))
        self.assertEqual(self.count_devices(), 11)

    def test_スラッシュ区切りの日付も読める(self):
        self.import_(csv_bytes("PC-0101,PC,サンプル電機,NB-1,SN-A,,2025/4/1,,"))
        self.assertEqual(devices.get_device("PC-0101", self.db_path)["purchase_date"].isoformat(), "2025-04-01")

    def test_種別はコードでも受け付ける(self):
        self.assertEqual(self.import_(csv_bytes(",smartphone,架空モバイル,KM-1,SN-A,,,,")), ["SP-0003"])

    def test_IMEIの先頭の0が消えない(self):
        self.import_(csv_bytes("SP-0050,スマートフォン,架空モバイル,KM-1,SN-A,,,,012345678901234"))
        self.assertEqual(devices.get_device("SP-0050", self.db_path)["imei"], "012345678901234")

    def test_必須以外の列はなくてもよく余分な列は無視する(self):
        content = csv_bytes(",PC,サンプル電機,NB-1,SN-A,旧台帳のメモ", header="資産番号,種別,メーカー,型番,シリアル番号,旧管理番号")
        self.assertEqual(self.import_(content), ["PC-0007"])

    def test_確認用の一覧ではデータベースを変更しない(self):
        preview = device_import.preview_csv((SAMPLES / "devices_import_sample.csv").read_bytes(), self.db_path)
        self.assertEqual([r["資産番号"] for r in preview], ["PC-0101", "PC-0102", "MN-0003", "SP-0050"])
        self.assertEqual(preview[0]["利用者"], "高橋 美咲")
        self.assertEqual(self.count_devices(), 10)

    # --- 異常系（ファイル全体） ---

    def test_空のファイルはエラー(self):
        self.assert_rejected(b"", "ファイルが空です。")

    def test_列名だけのファイルはエラー(self):
        self.assert_rejected(device_import.template_csv(), "取り込む行がありません。")

    def test_必須の列がないとエラー(self):
        self.assert_rejected(csv_bytes("PC,サンプル電機", header="種別,メーカー"), "必須の列がありません：型番、シリアル番号")

    def test_読めない文字コードはエラー(self):
        self.assert_rejected(b"\x82", "文字コードを判別できません。UTF-8 または Shift_JIS で保存してください。")

    # --- 異常系（行ごとの誤り）。1件でも誤りがあれば全件取り込まない ---

    def test_誤りが1行でもあれば正しい行も取り込まない(self):
        self.assert_rejected(
            csv_bytes(",PC,サンプル電機,NB-1,SN-A,,,,", ",PC,,NB-1,SN-B,,,,"),
            "3行目：メーカーを入力してください。",
        )

    def test_誤りのサンプルファイルでは全部の誤りを行番号つきで返す(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            self.import_((SAMPLES / "devices_import_error_sample.csv").read_bytes())
        self.assertEqual(ctx.exception.errors, [
            "2行目：シリアル番号「SN-PC-0001」はすでに登録されています。",
            "3行目：種別「プリンター」は使えません（PC・モニター・スマートフォンのいずれか）。",
            "4行目：購入日「2025/2/30」は日付として読めません（例：2026-04-01）。",
            "4行目：利用者の社員番号「E9999」は、在籍中の社員にいません。",
            "4行目：メーカーを入力してください。",
        ])

    def test_登録済みの資産番号はエラー(self):
        self.assert_rejected(csv_bytes("PC-0001,PC,サンプル電機,NB-1,SN-A,,,,"), "2行目：資産番号「PC-0001」はすでに登録されています。")

    def test_種別と合わない資産番号の形式はエラー(self):
        self.assert_rejected(csv_bytes("MN-0101,PC,サンプル電機,NB-1,SN-A,,,,"), "2行目：資産番号「MN-0101」は「PC-0001」の形式にしてください。")

    def test_ファイル内のシリアル番号の重複はエラー(self):
        self.assert_rejected(
            csv_bytes(",PC,サンプル電機,NB-1,SN-A,,,,", ",PC,サンプル電機,NB-1,SN-A,,,,"),
            "3行目：シリアル番号「SN-A」がファイル内で重複しています。",
        )

    def test_ファイル内の資産番号の重複はエラー(self):
        self.assert_rejected(
            csv_bytes("PC-0101,PC,サンプル電機,NB-1,SN-A,,,,", "PC-0101,PC,サンプル電機,NB-1,SN-B,,,,"),
            "3行目：資産番号「PC-0101」がファイル内で重複しています。",
        )

    def test_退職した社員は利用者にできない(self):
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute("UPDATE employees SET is_active = 0 WHERE employee_code = 'E0003'")
        conn.close()
        self.assert_rejected(csv_bytes(",PC,サンプル電機,NB-1,SN-A,E0003,,,"), "2行目：利用者の社員番号「E0003」は、在籍中の社員にいません。")

    def test_数字でない画面サイズはエラー(self):
        self.assert_rejected(csv_bytes(",モニター,テスト工業,MN-1,SN-A,,,大きい,"), "2行目：画面サイズ「大きい」は数字ではありません。")


if __name__ == "__main__":
    unittest.main()
