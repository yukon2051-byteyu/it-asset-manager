"""devices.py のテスト。seed.py のサンプルデータを使う。"""
import tempfile
import unittest
from pathlib import Path

import auth
import devices
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestListDevices(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def asset_nos(self, rows):
        return [r["資産番号"] for r in rows]

    def test_絞り込みなしで全件が資産番号順に出る(self):
        rows = devices.list_devices(db_path=self.db_path)
        self.assertEqual(len(rows), 10)
        self.assertEqual(self.asset_nos(rows), sorted(self.asset_nos(rows)))

    def test_空のリストは絞り込みなしと同じ(self):
        rows = devices.list_devices([], [], db_path=self.db_path)
        self.assertEqual(len(rows), 10)

    def test_種別で絞り込める(self):
        rows = devices.list_devices(categories=["pc"], db_path=self.db_path)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["種別"] == "PC" for r in rows))

    def test_状態を複数選んで絞り込める(self):
        rows = devices.list_devices(statuses=["in_stock", "repair"], db_path=self.db_path)
        self.assertEqual(self.asset_nos(rows), ["MN-0002", "PC-0005", "PC-0006", "SP-0002"])

    def test_種別と状態の両方で絞り込める(self):
        rows = devices.list_devices(categories=["pc"], statuses=["in_stock"], db_path=self.db_path)
        self.assertEqual(self.asset_nos(rows), ["PC-0005"])

    def test_該当なしは空のリスト(self):
        rows = devices.list_devices(categories=["monitor"], statuses=["repair"], db_path=self.db_path)
        self.assertEqual(rows, [])

    def test_表示用に日本語へ変換される(self):
        row = devices.list_devices(categories=["pc"], statuses=["lent"], db_path=self.db_path)[0]
        self.assertEqual(row["状態"], "貸出中")
        self.assertEqual(row["利用者"], "伊藤 さくら")
        self.assertEqual(row["部署"], "総務部")

    def test_利用者で絞り込める(self):
        # seed.py では E0002（鈴木 一郎）は id=2 で、PC・モニター・スマホを1台ずつ利用している
        rows = devices.list_devices(employee_id=2, db_path=self.db_path)
        self.assertEqual(self.asset_nos(rows), ["MN-0001", "PC-0001", "SP-0001"])

    def test_機器を持たない社員は空のリスト(self):
        # E0001（佐藤 花子）は機器を利用していない
        rows = devices.list_devices(employee_id=1, db_path=self.db_path)
        self.assertEqual(rows, [])

    def test_利用者がいない機器は空文字で表示される(self):
        row = devices.list_devices(statuses=["repair"], db_path=self.db_path)[0]
        self.assertEqual(row["利用者"], "")


if __name__ == "__main__":
    unittest.main()
