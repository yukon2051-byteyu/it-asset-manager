"""reports.py（集計レポート）のテスト。seed.py のサンプルデータを使う。

機器：PC 使用中3・貸出中1・在庫1・修理中1、モニター 使用中1・在庫1、スマホ 使用中1・在庫1（計10台）
利用中の機器の部署：営業部 4（鈴木3・田中1）、経理部 1（高橋）、総務部 1（伊藤）
"""
import datetime
import tempfile
import unittest
from pathlib import Path

import auth
import devices
import reports
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestReports(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_数字のタイルの台数(self):
        self.assertEqual(reports.device_summary(self.db_path),
                         {"total": 10, "in_use": 6, "in_stock": 3, "attention": 1, "disposed": 0})

    def test_廃棄済は総数に含めない(self):
        devices.change_status("PC-0005", "dispose", db_path=self.db_path)
        summary = reports.device_summary(self.db_path)
        self.assertEqual((summary["total"], summary["in_stock"], summary["disposed"]), (9, 2, 1))

    def test_種別と状態の組み合わせは0台も含めて全部そろう(self):
        rows = reports.devices_by_category_status(self.db_path)
        self.assertEqual(len(rows), 3 * 5)  # 種別3 × 状態5（廃棄済を除く）
        counts = {(r["種別"], r["状態"]): r["台数"] for r in rows}
        self.assertEqual(counts[("PC", "使用中")], 3)
        self.assertEqual(counts[("PC", "修理中")], 1)
        self.assertEqual(counts[("モニター", "貸出中")], 0)
        self.assertEqual(sum(counts.values()), 10)

    def test_部署ごとの台数は多い順(self):
        self.assertEqual(reports.devices_by_department(self.db_path),
                         [{"部署": "営業部", "台数": 4}, {"部署": "経理部", "台数": 1}, {"部署": "総務部", "台数": 1}])

    def test_返却すると部署の台数から外れる(self):
        devices.change_status("PC-0002", "return", db_path=self.db_path)  # 経理部の高橋さんが返却
        self.assertNotIn("経理部", [r["部署"] for r in reports.devices_by_department(self.db_path)])

    def test_ライセンスの利用率と注意(self):
        rows = {r["ライセンス名"]: r for r in reports.license_usage(datetime.date(2026, 11, 1), self.db_path)}
        self.assertEqual(rows["PDF編集ソフト"]["利用率（%）"], 100.0)
        self.assertEqual(rows["文書作成ソフト"]["利用率（%）"], 80.0)
        self.assertEqual(rows["ウイルス対策ソフト"]["利用率（%）"], 40.0)
        self.assertEqual(rows["PDF編集ソフト"]["注意"], "期限間近（あと19日）・残数なし")


if __name__ == "__main__":
    unittest.main()
