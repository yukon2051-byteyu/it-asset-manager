"""charts.py（集計レポートのグラフ）のテスト。グラフの設計図（Vega-Lite）の中身を確かめる。"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import auth
import charts
import reports
import seed

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000


class TestCharts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def specs(self):
        return {
            "status": charts.status_stack_chart(reports.devices_by_category_status(self.db_path)).to_dict(),
            "department": charts.department_chart(pd.DataFrame(reports.devices_by_department(self.db_path))).to_dict(),
            "license": charts.license_chart(pd.DataFrame(reports.license_usage(db_path=self.db_path))).to_dict(),
        }

    def test_見た目の設定が上書きされずに残っている(self):
        # configure(...) を最後に呼ぶと、軸や凡例の設定が消えてしまう不具合があった
        for name, spec in self.specs().items():
            with self.subTest(chart=name):
                config = spec["config"]
                self.assertEqual(config["background"], charts.SURFACE)
                self.assertEqual(config["axis"]["labelColor"], charts.MUTED)
                self.assertEqual(config["legend"]["orient"], "top")

    def test_状態の色は状態ごとに固定されている(self):
        # 0台の状態（紛失）があっても、色の割り当ては変わらない
        scale = self.specs()["status"]["encoding"]["color"]["scale"]
        self.assertEqual(scale["domain"], ["在庫", "使用中", "貸出中", "修理中", "紛失"])
        self.assertEqual(scale["range"], charts.STATUS_COLORS)

    def test_利用率の目盛りは0から100の5つ(self):
        x = self.specs()["license"]["encoding"]["x"]
        self.assertEqual(x["scale"]["domain"], [0, 100])
        self.assertEqual(x["axis"]["values"], [0, 25, 50, 75, 100])

    def test_どのグラフもマウスを乗せると値が出る(self):
        for name, spec in self.specs().items():
            with self.subTest(chart=name):
                self.assertIn("tooltip", spec["encoding"])


if __name__ == "__main__":
    unittest.main()
