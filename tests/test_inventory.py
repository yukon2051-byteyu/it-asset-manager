"""inventory.py（棚卸）のテスト。

seed.py のサンプルデータで棚卸の対象になる機器（9台。修理中の PC-0006 は対象外）：
  鈴木（id=2）PC-0001・MN-0001・SP-0001 / 高橋（3）PC-0002 / 田中（4）PC-0003 / 伊藤（5）PC-0004（貸出中）
  在庫（IT管理者が確認）PC-0005・MN-0002・SP-0002
"""
import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

import audit
import auth
import db
import devices
import inventory
import seed
from devices import ValidationError

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

SATO, SUZUKI, TAKAHASHI, ITO = 1, 2, 3, 5
TODAY = datetime.date(2026, 11, 1)
DUE = datetime.date(2026, 11, 15)


class TestInventory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def start(self, name="2026年11月の棚卸", due=DUE):
        return inventory.start_inventory(name, due, SATO, TODAY, self.db_path)

    def items(self, inventory_id, **kwargs):
        return inventory.list_items(inventory_id, db_path=self.db_path, **kwargs)

    def item_id(self, inventory_id, asset_no):
        return next(i["id"] for i in self.items(inventory_id) if i["資産番号"] == asset_no)

    def answer(self, answers, actor_id, as_admin=False):
        return inventory.answer_items(answers, actor_id, as_admin, self.db_path)

    def answer_all(self, inventory_id, absent=()):
        """全件を回答する（absent に入れた資産番号だけ「ない」）。"""
        answers = [(i["id"], "absent" if i["資産番号"] in absent else "present", "所在不明" if i["資産番号"] in absent else "")
                   for i in self.items(inventory_id)]
        self.answer(answers, SATO, as_admin=True)

    def assert_error(self, expected_message, func, *args, **kwargs):
        with self.assertRaises(ValidationError) as ctx:
            func(*args, **kwargs)
        self.assertIn(expected_message, ctx.exception.errors)

    # --- 開始 ---

    def test_開始するとその時点の機器が対象になり修理中は除かれる(self):
        inventory_id, count = self.start()
        self.assertEqual(count, 9)
        assets = [i["資産番号"] for i in self.items(inventory_id)]
        self.assertNotIn("PC-0006", assets)
        by_asset = {i["資産番号"]: i for i in self.items(inventory_id)}
        self.assertEqual(by_asset["PC-0001"]["回答する人"], "E0002 鈴木 一郎")
        self.assertEqual(by_asset["PC-0005"]["回答する人"], "IT管理者（在庫）")
        self.assertEqual(by_asset["PC-0004"]["開始時の状態"], "貸出中")

    def test_開始の入力ミスはエラーになる(self):
        with self.assertRaises(ValidationError) as ctx:
            inventory.start_inventory(" ", datetime.date(2026, 10, 31), SATO, TODAY, self.db_path)
        self.assertEqual(ctx.exception.errors, ["棚卸の名前を入力してください。", "回答期限に過去の日付は指定できません。"])

    def test_実施中の棚卸があるともう1つは開始できない(self):
        self.start()
        self.assert_error("実施中の棚卸があります。先に完了してください。", self.start, "2つ目")

    def test_データベースのルールでも実施中は1つだけ(self):
        self.start()
        conn = db.get_connection(self.db_path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO inventories (name, due_date) VALUES ('2つ目', '2026-12-01')")
        finally:
            conn.close()

    def test_対象の機器がなければ開始されない(self):
        empty = Path(self.tmpdir.name) / "empty.db"
        db.init_db(empty)
        with self.assertRaises(ValidationError):
            inventory.start_inventory("棚卸", DUE, None, TODAY, empty)
        self.assertIsNone(inventory.get_open_inventory(empty))  # 開始も取り消されている

    # --- 回答 ---

    def test_社員は自分の機器に回答できる(self):
        inventory_id, _ = self.start()
        mine = self.items(inventory_id, employee_id=SUZUKI)
        self.assertEqual([i["資産番号"] for i in mine], ["MN-0001", "PC-0001", "SP-0001"])
        self.assertEqual(self.answer([(mine[0]["id"], "present", ""), (mine[1]["id"], "absent", "自宅にある")], SUZUKI), 2)
        answered = {i["資産番号"]: i for i in self.items(inventory_id, employee_id=SUZUKI)}
        self.assertEqual((answered["MN-0001"]["回答"], answered["PC-0001"]["回答"]), ("ある", "ない"))
        self.assertEqual((answered["PC-0001"]["状況"], answered["PC-0001"]["回答者"]), ("自宅にある", "鈴木 一郎"))
        self.assertEqual(answered["SP-0001"]["回答"], "未回答")

    def test_他人の機器には回答できない(self):
        inventory_id, _ = self.start()
        self.assert_error("PC-0002：あなたが回答する機器ではありません。",
                          self.answer, [(self.item_id(inventory_id, "PC-0002"), "present", "")], SUZUKI)

    def test_ないのときは状況が必要で1件でも誤りがあれば保存しない(self):
        inventory_id, _ = self.start()
        mine = self.items(inventory_id, employee_id=SUZUKI)
        with self.assertRaises(ValidationError) as ctx:
            self.answer([(mine[0]["id"], "present", ""), (mine[1]["id"], "absent", " ")], SUZUKI)
        self.assertEqual(ctx.exception.errors,
                         ["PC-0001：「ない」のときは、状況を入力してください（例：自宅にある、所在がわからない）。"])
        self.assertEqual(inventory.progress(inventory_id, self.db_path)["answered"], 0)  # 正しい1件も保存されていない

    def test_回答は完了までなら直せる(self):
        inventory_id, _ = self.start()
        item_id = self.item_id(inventory_id, "PC-0002")
        self.answer([(item_id, "absent", "見当たらない")], TAKAHASHI)
        self.answer([(item_id, "present", "引き出しにあった")], TAKAHASHI)
        self.assertEqual(self.items(inventory_id, employee_id=TAKAHASHI)[0]["回答"], "ある")

    def test_管理者は在庫の確認と代理の回答ができ代理は履歴でわかる(self):
        inventory_id, _ = self.start()
        self.answer([(self.item_id(inventory_id, "PC-0005"), "present", ""),
                     (self.item_id(inventory_id, "PC-0004"), "present", "本人に電話で確認")], SATO, as_admin=True)
        details = [log["内容"] for log in audit.list_logs(target_type="inventory", db_path=self.db_path)]
        self.assertIn("PC-0004：ある（代理）、本人に電話で確認", details)
        self.assertIn("PC-0005：ある", details)  # 在庫は代理ではない

    def test_棚卸の途中で利用者が変わっても開始時の利用者が回答する(self):
        inventory_id, _ = self.start()
        devices.change_status("PC-0004", "return", db_path=self.db_path)  # 伊藤さんが途中で返却
        self.answer([(self.item_id(inventory_id, "PC-0004"), "present", "")], ITO)
        self.assertEqual(inventory.progress(inventory_id, self.db_path)["answered"], 1)

    # --- 進み具合・差異 ---

    def test_進み具合と未回答と差異の一覧(self):
        inventory_id, _ = self.start()
        self.answer([(self.item_id(inventory_id, "PC-0002"), "absent", "所在不明"),
                     (self.item_id(inventory_id, "PC-0003"), "present", "")], SATO, as_admin=True)
        self.assertEqual(inventory.progress(inventory_id, self.db_path),
                         {"total": 9, "answered": 2, "unanswered": 7, "absent": 1, "unresolved": 1})
        self.assertEqual([i["資産番号"] for i in self.items(inventory_id, view="absent")], ["PC-0002"])
        self.assertEqual(len(self.items(inventory_id, view="unanswered")), 7)

    def test_対応メモはないの機器にだけ書ける(self):
        inventory_id, _ = self.start()
        absent_id, present_id = self.item_id(inventory_id, "PC-0002"), self.item_id(inventory_id, "PC-0003")
        self.answer([(absent_id, "absent", "所在不明"), (present_id, "present", "")], SATO, as_admin=True)
        self.assert_error("対応メモを入力してください。", inventory.resolve_item, absent_id, " ", SATO, self.db_path)
        self.assert_error("対応メモは「ない」と回答された機器にだけ書けます。",
                          inventory.resolve_item, present_id, "確認済み", SATO, self.db_path)
        inventory.resolve_item(absent_id, "紛失として処理した", SATO, self.db_path)
        self.assertEqual(inventory.progress(inventory_id, self.db_path)["unresolved"], 0)

    def test_あるに直すと対応メモは消える(self):
        inventory_id, _ = self.start()
        item_id = self.item_id(inventory_id, "PC-0002")
        self.answer([(item_id, "absent", "所在不明")], TAKAHASHI)
        inventory.resolve_item(item_id, "捜索中", SATO, self.db_path)
        self.answer([(item_id, "present", "見つかった")], TAKAHASHI)
        self.assertEqual(self.items(inventory_id, employee_id=TAKAHASHI)[0]["対応メモ"], "")

    # --- 完了 ---

    def test_未回答があると完了できない(self):
        inventory_id, _ = self.start()
        self.assert_error("未回答が 9 件あります。回答を集めるか、代理で回答してください。",
                          inventory.complete_inventory, inventory_id, SATO, self.db_path)

    def test_対応メモのないないがあると完了できない(self):
        inventory_id, _ = self.start()
        self.answer_all(inventory_id, absent=("PC-0002",))
        self.assert_error("「ない」と回答された機器のうち、対応メモがないものが 1 件あります。",
                          inventory.complete_inventory, inventory_id, SATO, self.db_path)

    def test_条件がそろえば完了でき完了後は回答できず次の棚卸を始められる(self):
        inventory_id, _ = self.start()
        self.answer_all(inventory_id, absent=("PC-0002",))
        inventory.resolve_item(self.item_id(inventory_id, "PC-0002"), "紛失として処理した", SATO, self.db_path)
        inventory.complete_inventory(inventory_id, SATO, self.db_path)

        self.assertIsNone(inventory.get_open_inventory(self.db_path))
        self.assert_error("PC-0003：この棚卸はすでに完了しています。",
                          self.answer, [(self.item_id(inventory_id, "PC-0003"), "present", "")], SATO, True)
        history = inventory.list_inventories(self.db_path)
        self.assertEqual((history[0]["状態"], history[0]["対象"], history[0]["ない"]), ("完了", 9, 1))
        second_id, _ = self.start("次の棚卸")
        self.assertNotEqual(second_id, inventory_id)

    def test_開始と完了が操作履歴に残る(self):
        inventory_id, _ = self.start()
        self.answer_all(inventory_id)
        inventory.complete_inventory(inventory_id, SATO, self.db_path)
        logs = audit.list_logs(target_type="inventory", db_path=self.db_path)
        self.assertEqual((logs[0]["操作"], logs[0]["内容"]), ("完了", "対象 9 件、「ない」0 件"))
        self.assertEqual((logs[-1]["操作"], logs[-1]["内容"]), ("開始", "対象 9 件、回答期限 2026-11-15"))
        self.assertEqual(logs[0]["操作者"], "E0001 佐藤 花子")


if __name__ == "__main__":
    unittest.main()
