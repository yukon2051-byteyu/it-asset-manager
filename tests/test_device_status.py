"""devices.py の割当・返却・廃棄（available_actions / change_status）のテスト。

seed.py のサンプルデータ：
  PC-0001 使用中（E0002）・ウイルス対策ソフトを割当、PC-0004 貸出中（E0005）、
  PC-0005 在庫、PC-0006 修理中
"""
import tempfile
import unittest
from pathlib import Path

import auth
import db
import devices
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

TAKAHASHI = 3  # E0003 高橋 美咲 の id


class TestChangeStatus(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def get(self, asset_no):
        return devices.get_device(asset_no, self.db_path)

    def change(self, asset_no, action, employee_id=None):
        return devices.change_status(asset_no, action, employee_id, self.db_path)

    def assert_error(self, expected_message, asset_no, action, employee_id=None):
        """エラーになり、状態も利用者も変わらないことを確かめる。"""
        before = self.get(asset_no)
        with self.assertRaises(devices.ValidationError) as ctx:
            self.change(asset_no, action, employee_id)
        self.assertEqual(ctx.exception.errors, [expected_message])
        after = self.get(asset_no)
        self.assertEqual((after["status"], after["employee_id"]), (before["status"], before["employee_id"]))

    def run_sql(self, sql, params=()):
        conn = db.get_connection(self.db_path)
        with conn:
            rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows

    # --- できる操作の一覧 ---

    def test_状態ごとにできる操作が決まっている(self):
        self.assertEqual(devices.available_actions("in_stock"), ["assign", "dispose"])
        self.assertEqual(devices.available_actions("in_use"), ["return"])
        self.assertEqual(devices.available_actions("lent"), ["return"])
        self.assertEqual(devices.available_actions("repair"), ["dispose"])
        self.assertEqual(devices.available_actions("disposed"), [])

    # --- 割当 ---

    def test_在庫の機器を社員に割り当てると使用中になる(self):
        self.assertEqual(self.change("PC-0005", "assign", TAKAHASHI), "in_use")
        device = self.get("PC-0005")
        self.assertEqual((device["status"], device["employee_id"]), ("in_use", TAKAHASHI))

    def test_割当で社員を選ばないとエラー(self):
        self.assert_error("割り当てる社員を選んでください。", "PC-0005", "assign")

    def test_退職した社員には割り当てられない(self):
        self.run_sql("UPDATE employees SET is_active = 0 WHERE id = ?", (TAKAHASHI,))
        self.assert_error("在籍中の社員を選んでください。", "PC-0005", "assign", TAKAHASHI)

    def test_存在しない社員には割り当てられない(self):
        self.assert_error("在籍中の社員を選んでください。", "PC-0005", "assign", 999)

    def test_使用中の機器は割り当てられない(self):
        self.assert_error("使用中の機器は割当できません。", "PC-0001", "assign", TAKAHASHI)

    # --- 返却 ---

    def test_使用中の機器を返却すると在庫になり利用者が外れる(self):
        self.assertEqual(self.change("PC-0001", "return"), "in_stock")
        device = self.get("PC-0001")
        self.assertEqual((device["status"], device["employee_id"]), ("in_stock", None))

    def test_貸出中の機器も返却できる(self):
        self.assertEqual(self.change("PC-0004", "return"), "in_stock")
        self.assertIsNone(self.get("PC-0004")["employee_id"])

    def test_在庫の機器は返却できない(self):
        self.assert_error("在庫の機器は返却できません。", "PC-0005", "return")

    # --- 廃棄 ---

    def test_在庫と修理中の機器は廃棄できる(self):
        self.assertEqual(self.change("PC-0005", "dispose"), "disposed")
        self.assertEqual(self.change("PC-0006", "dispose"), "disposed")

    def test_使用中の機器はいきなり廃棄できない(self):
        self.assert_error("使用中の機器は廃棄できません。", "PC-0001", "dispose")

    def test_廃棄済の機器には何もできない(self):
        self.change("PC-0005", "dispose")
        self.assert_error("廃棄済の機器は割当できません。", "PC-0005", "assign", TAKAHASHI)

    def test_廃棄するとそのPCのライセンスだけが解除される(self):
        self.change("PC-0001", "return")
        self.change("PC-0001", "dispose")
        rows = self.run_sql(
            "SELECT d.asset_no, a.released_on FROM license_assignments a"
            " JOIN devices d ON d.id = a.device_id ORDER BY d.asset_no"
        )
        released = {r["asset_no"]: r["released_on"] for r in rows}
        self.assertIsNotNone(released["PC-0001"])
        self.assertIsNone(released["PC-0002"])  # ほかのPCのライセンスはそのまま

    def test_返却してもPCのライセンスは解除されない(self):
        # ライセンスはPCに割り当てているので、利用者が変わってもPCに残る
        self.change("PC-0001", "return")
        rows = self.run_sql(
            "SELECT a.released_on FROM license_assignments a JOIN devices d ON d.id = a.device_id"
            " WHERE d.asset_no = 'PC-0001'"
        )
        self.assertIsNone(rows[0]["released_on"])

    # --- その他の誤り ---

    def test_決められていない操作はエラー(self):
        self.assert_error("操作が正しくありません。", "PC-0005", "repair")

    def test_存在しない機器はエラー(self):
        with self.assertRaises(devices.ValidationError) as ctx:
            self.change("PC-9999", "return")
        self.assertEqual(ctx.exception.errors, ["資産番号「PC-9999」の機器が見つかりません。"])


if __name__ == "__main__":
    unittest.main()
