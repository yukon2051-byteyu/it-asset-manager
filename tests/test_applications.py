"""applications.py（申請と承認）のテスト。

seed.py のサンプルデータ（id は登録順）：
  社員  1 佐藤（IT管理者） 2 鈴木 3 高橋 4 田中 5 伊藤
  機器  1 PC-0001（鈴木・使用中） 4 PC-0004（伊藤・貸出中） 5 PC-0005（在庫）
        7 MN-0001（鈴木・使用中） 8 MN-0002（在庫） 9 SP-0001（鈴木・使用中） 10 SP-0002（在庫）
"""
import datetime
import tempfile
import unittest
from pathlib import Path

import applications
import auth
import db
import devices
import seed
from devices import ValidationError

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

TODAY = datetime.date(2026, 10, 31)
SATO, SUZUKI, TAKAHASHI, ITO = 1, 2, 3, 5
PC1, PC4, PC5, MN1, MN2 = 1, 4, 5, 7, 8


def lend_input(**changes):
    data = {"request_type": "lend", "category": "monitor", "detail": "在宅勤務で使うため",
            "start_date": datetime.date(2026, 11, 1), "due_date": datetime.date(2026, 11, 30)}
    data.update(changes)
    return data


class TestApplications(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def create(self, requester_id, data):
        return applications.create_request(requester_id, data, TODAY, self.db_path)

    def device(self, asset_no):
        return devices.get_device(asset_no, self.db_path)

    def request(self, request_id):
        return next(r for r in applications.list_requests(db_path=self.db_path) if r["id"] == request_id)

    def assert_create_error(self, expected_message, requester_id, data):
        with self.assertRaises(ValidationError) as ctx:
            self.create(requester_id, data)
        self.assertIn(expected_message, ctx.exception.errors)
        self.assertEqual(applications.list_requests(db_path=self.db_path), [])

    def assert_error(self, expected_message, func, *args):
        with self.assertRaises(ValidationError) as ctx:
            func(*args, db_path=self.db_path)
        self.assertIn(expected_message, ctx.exception.errors)

    # --- 申請の登録 ---

    def test_貸出を申請すると承認待ちになり申請番号が振られる(self):
        request_id = self.create(TAKAHASHI, lend_input())
        row = self.request(request_id)
        self.assertEqual((row["申請番号"], row["種類"], row["状態"]), ("R-0001", "貸出", "承認待ち"))
        self.assertEqual(row["期間・発生日"], "2026-11-01 〜 2026-11-30")

    def test_故障を申請できる(self):
        request_id = self.create(SUZUKI, {"request_type": "failure", "device_id": PC1,
                                          "occurred_on": TODAY, "detail": "電源が入らない"})
        row = self.request(request_id)
        self.assertEqual((row["種類"], row["対象"], row["期間・発生日"]), ("故障", "PC-0001", "発生日 2026-10-31"))

    def test_自分が使っている機器だけが選べる(self):
        self.assertEqual(list(applications.list_my_devices(SUZUKI, self.db_path)), [MN1, PC1, 9])
        self.assertEqual(list(applications.list_my_devices(ITO, self.db_path)), [PC4])
        self.assertEqual(applications.list_my_devices(SATO, self.db_path), {})

    def test_貸出の入力ミスはまとめてエラーになる(self):
        with self.assertRaises(ValidationError) as ctx:
            self.create(TAKAHASHI, {"request_type": "lend", "category": None, "detail": " ",
                                    "start_date": None, "due_date": None})
        self.assertEqual(ctx.exception.errors, [
            "借りたい機器の種別を選んでください。", "利用目的を入力してください。",
            "利用開始日を入力してください。", "返却予定日を入力してください。",
        ])

    def test_返却予定日が開始日より前ならエラー(self):
        self.assert_create_error("返却予定日は利用開始日より後の日付にしてください。", TAKAHASHI,
                                 lend_input(due_date=datetime.date(2026, 10, 31)))

    def test_利用開始日が過去ならエラー(self):
        self.assert_create_error("利用開始日に過去の日付は指定できません。", TAKAHASHI,
                                 lend_input(start_date=datetime.date(2026, 10, 30)))

    def test_同じ種別の貸出申請が処理中なら申請できない(self):
        self.create(TAKAHASHI, lend_input())
        with self.assertRaises(ValidationError) as ctx:
            self.create(TAKAHASHI, lend_input())
        self.assertEqual(ctx.exception.errors, ["同じ種別の貸出申請が、まだ処理されていません。"])

    def test_他人の機器は申請できない(self):
        self.assert_create_error("対象の機器を、自分が使っている機器から選んでください。", TAKAHASHI,
                                 {"request_type": "return", "device_id": PC1})

    def test_在庫の機器は返却を申請できない(self):
        self.assert_create_error("対象の機器を、自分が使っている機器から選んでください。", SUZUKI,
                                 {"request_type": "return", "device_id": PC5})

    def test_同じ機器に処理中の申請があれば申請できない(self):
        self.create(SUZUKI, {"request_type": "return", "device_id": PC1})
        with self.assertRaises(ValidationError) as ctx:
            self.create(SUZUKI, {"request_type": "failure", "device_id": PC1, "occurred_on": TODAY, "detail": "故障"})
        self.assertEqual(ctx.exception.errors, ["この機器には、まだ処理されていない申請があります。"])

    def test_故障と紛失は内容と発生日が必須で未来の日付は不可(self):
        self.assert_create_error("内容を入力してください。", SUZUKI,
                                 {"request_type": "loss", "device_id": PC1, "occurred_on": TODAY, "detail": ""})
        self.assert_create_error("発生日を入力してください。", SUZUKI,
                                 {"request_type": "loss", "device_id": PC1, "detail": "置き忘れ"})
        self.assert_create_error("発生日に未来の日付は指定できません。", SUZUKI,
                                 {"request_type": "loss", "device_id": PC1, "occurred_on": datetime.date(2026, 11, 1),
                                  "detail": "置き忘れ"})

    def test_決められていない種類はエラー(self):
        self.assert_create_error("申請の種類を選んでください。", SUZUKI, {"request_type": "purchase"})

    # --- 承認 ---

    def test_貸出を承認すると選んだ在庫の機器が申請者に貸出中になる(self):
        request_id = self.create(TAKAHASHI, lend_input())
        self.assertEqual(list(applications.list_stock_devices("monitor", self.db_path)), [MN2])
        applications.approve(request_id, SATO, MN2, db_path=self.db_path)
        mn2 = self.device("MN-0002")
        self.assertEqual((mn2["status"], mn2["user_name"]), ("lent", "高橋 美咲"))
        row = self.request(request_id)
        self.assertEqual((row["状態"], row["処理者"], row["対象"]), ("承認済", "佐藤 花子", "モニター（MN-0002）"))

    def test_返却を承認すると在庫に戻る(self):
        request_id = self.create(ITO, {"request_type": "return", "device_id": PC4})
        applications.approve(request_id, SATO, db_path=self.db_path)
        pc4 = self.device("PC-0004")
        self.assertEqual((pc4["status"], pc4["employee_id"]), ("in_stock", None))

    def test_故障を承認すると修理中になり紛失を承認すると紛失になる(self):
        failure = self.create(SUZUKI, {"request_type": "failure", "device_id": PC1, "occurred_on": TODAY, "detail": "故障"})
        loss = self.create(SUZUKI, {"request_type": "loss", "device_id": MN1, "occurred_on": TODAY, "detail": "紛失"})
        applications.approve(failure, SATO, db_path=self.db_path)
        applications.approve(loss, SATO, db_path=self.db_path)
        self.assertEqual(self.device("PC-0001")["status"], "repair")
        self.assertEqual(self.device("MN-0001")["status"], "lost")

    def test_貸出の承認で機器を選ばないとエラー(self):
        request_id = self.create(TAKAHASHI, lend_input())
        self.assert_error("貸し出す機器を選んでください。", applications.approve, request_id, SATO, None)

    def test_貸出の承認で違う種別の機器は選べない(self):
        request_id = self.create(TAKAHASHI, lend_input())
        self.assert_error("申請された種別の機器を選んでください。", applications.approve, request_id, SATO, PC5)

    def test_機器の状態を変えられないときは申請も承認済にならない(self):
        request_id = self.create(TAKAHASHI, lend_input(category="pc"))
        self.assert_error("使用中の機器は貸出できません。", applications.approve, request_id, SATO, PC1)
        self.assertEqual(self.request(request_id)["状態"], "承認待ち")  # 片方だけ変わっていない

    def test_処理済みの申請はもう一度処理できない(self):
        request_id = self.create(ITO, {"request_type": "return", "device_id": PC4})
        applications.approve(request_id, SATO, db_path=self.db_path)
        self.assert_error("この申請はすでに承認済です。", applications.reject, request_id, SATO, "重複")

    def test_申請後に利用者が変わった機器は承認できない(self):
        request_id = self.create(ITO, {"request_type": "return", "device_id": PC4})
        devices.change_status("PC-0004", "return", db_path=self.db_path)  # 管理者が先に直接返却処理した
        self.assert_error("この機器は、もう申請者の利用ではありません。却下してください。",
                          applications.approve, request_id, SATO)
        self.assertEqual(self.request(request_id)["状態"], "承認待ち")

    # --- 却下・保留 ---

    def test_却下には理由が必要で申請者に表示される(self):
        request_id = self.create(TAKAHASHI, lend_input())
        self.assert_error("却下の理由を入力してください。", applications.reject, request_id, SATO, " ")
        applications.reject(request_id, SATO, "今月は在庫を確保できないため", db_path=self.db_path)
        mine = applications.list_requests(requester_id=TAKAHASHI, db_path=self.db_path)[0]
        self.assertEqual((mine["状態"], mine["理由"]), ("却下", "今月は在庫を確保できないため"))

    def test_貸出は保留にでき保留からあとで承認できる(self):
        request_id = self.create(TAKAHASHI, lend_input())
        applications.hold(request_id, SATO, "在庫の入荷待ち", db_path=self.db_path)
        self.assertEqual(self.request(request_id)["状態"], "保留")
        applications.approve(request_id, SATO, MN2, db_path=self.db_path)
        self.assertEqual(self.request(request_id)["状態"], "承認済")

    def test_貸出以外は保留にできない(self):
        request_id = self.create(ITO, {"request_type": "return", "device_id": PC4})
        self.assert_error("保留にできるのは貸出の申請だけです。", applications.hold, request_id, SATO, "確認中")

    def test_処理待ちだけを絞り込める(self):
        first = self.create(TAKAHASHI, lend_input())
        second = self.create(ITO, {"request_type": "return", "device_id": PC4})
        applications.approve(second, SATO, db_path=self.db_path)
        open_ids = [r["id"] for r in applications.list_requests(statuses=list(applications.OPEN_STATUSES),
                                                                 db_path=self.db_path)]
        self.assertEqual(open_ids, [first])


if __name__ == "__main__":
    unittest.main()
