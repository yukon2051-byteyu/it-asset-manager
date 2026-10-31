"""employees.py の利用者管理（登録・変更・退職・再有効化・パスワード再発行）のテスト。

seed.py のサンプルデータ（id は登録順）：
  1 E0001 佐藤（IT管理者） 2 E0002 鈴木（PC-0001 などを使用中） 3 E0003 高橋 5 E0005 伊藤（PC-0004 を貸出中）
  E0004 田中 は PC-0003 を使用中。文書作成ソフトは E0001〜E0004 に割り当て済み
"""
import datetime
import tempfile
import unittest
from pathlib import Path

import audit
import auth
import db
import devices
import employees
import licenses
import seed
from devices import ValidationError

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

SATO, SUZUKI, TAKAHASHI = 1, 2, 3


def new_input(**changes):
    data = {"employee_code": "E0006", "name": "山田 太郎", "department": "人事部",
            "email": "yamada@example.com", "role": "user"}
    data.update(changes)
    return data


class TestEmployeeManage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        self.passwords = seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def get(self, employee_id):
        return employees.get_employee(employee_id, self.db_path)

    def assert_error(self, expected_message, func, *args, **kwargs):
        with self.assertRaises(ValidationError) as ctx:
            func(*args, db_path=self.db_path, **kwargs)
        self.assertIn(expected_message, ctx.exception.errors)

    def logs(self):
        return audit.list_logs(target_type="employee", db_path=self.db_path)

    # --- 登録 ---

    def test_登録すると初期パスワードでログインできる(self):
        new_id, password = employees.register_employee(new_input(), self.db_path, actor_id=SATO)
        self.assertEqual(len(password), 12)
        user = auth.authenticate("E0006", password, self.db_path)
        self.assertEqual((user["id"], user["name"], user["role"]), (new_id, "山田 太郎", "user"))

    def test_全角の社員番号やメールアドレスは半角にそろえる(self):
        new_id, _ = employees.register_employee(new_input(employee_code="Ｅ０００６", email="ｙａｍａｄａ@example.com"),
                                                self.db_path)
        emp = self.get(new_id)
        self.assertEqual((emp["employee_code"], emp["email"]), ("E0006", "yamada@example.com"))

    def test_登録の入力ミスはまとめてエラーになる(self):
        with self.assertRaises(ValidationError) as ctx:
            employees.register_employee({"employee_code": "", "name": " ", "department": "", "email": "", "role": "boss"},
                                        self.db_path)
        self.assertEqual(ctx.exception.errors, [
            "社員番号を入力してください。", "氏名を入力してください。", "部署を入力してください。",
            "メールアドレスを入力してください。", "権限を選んでください。",
        ])

    def test_社員番号とメールアドレスの重複はエラー(self):
        with self.assertRaises(ValidationError) as ctx:
            employees.register_employee(new_input(employee_code="E0001", email="sato@example.com"), self.db_path)
        self.assertEqual(ctx.exception.errors, [
            "社員番号「E0001」はすでに登録されています。", "メールアドレス「sato@example.com」はすでに登録されています。",
        ])

    def test_社員番号とメールアドレスの形式が違うとエラー(self):
        with self.assertRaises(ValidationError) as ctx:
            employees.register_employee(new_input(employee_code="E-0006", email="yamada.example.com"), self.db_path)
        self.assertEqual(ctx.exception.errors, [
            "社員番号は半角英数字（20文字以内）で入力してください。",
            "メールアドレスの形式が正しくありません（例：yamada@example.com）。",
        ])

    def test_登録は記録されるがパスワードは記録されない(self):
        _, password = employees.register_employee(new_input(), self.db_path, actor_id=SATO)
        log = self.logs()[0]
        self.assertEqual((log["操作者"], log["対象名"], log["操作"], log["内容"]),
                         ("E0001 佐藤 花子", "E0006 山田 太郎", "登録", "人事部、一般社員"))
        self.assertNotIn(password, str(audit.list_logs(db_path=self.db_path)))

    # --- 変更 ---

    def test_部署と権限を変更でき変わった項目が記録される(self):
        emp = self.get(TAKAHASHI)
        employees.update_employee(TAKAHASHI, {**emp, "department": "情報システム部", "role": "admin"},
                                  self.db_path, actor_id=SATO)
        self.assertEqual((self.get(TAKAHASHI)["department"], self.get(TAKAHASHI)["role"]), ("情報システム部", "admin"))
        self.assertEqual(self.logs()[0]["内容"], "部署：経理部 → 情報システム部、権限：一般社員 → IT管理者")

    def test_社員番号は変更されない(self):
        emp = self.get(TAKAHASHI)
        employees.update_employee(TAKAHASHI, {**emp, "employee_code": "X9999"}, self.db_path, actor_id=SATO)
        self.assertEqual(self.get(TAKAHASHI)["employee_code"], "E0003")

    def test_自分と同じメールアドレスのままなら変更できる(self):
        emp = self.get(TAKAHASHI)
        employees.update_employee(TAKAHASHI, {**emp, "name": "高橋 美咲（旧姓）"}, self.db_path, actor_id=SATO)
        self.assertEqual(self.get(TAKAHASHI)["name"], "高橋 美咲（旧姓）")

    def test_自分自身の権限は下げられない(self):
        emp = self.get(SATO)
        self.assert_error("自分自身の権限は下げられません。ほかのIT管理者に依頼してください。",
                          employees.update_employee, SATO, {**emp, "role": "user"}, actor_id=SATO)

    def test_最後のIT管理者の権限は下げられない(self):
        emp = self.get(SATO)
        self.assert_error("IT管理者が1人もいなくなるため、権限を下げられません。",
                          employees.update_employee, SATO, {**emp, "role": "user"}, actor_id=None)

    def test_ほかに管理者がいれば管理者の権限を下げられる(self):
        takahashi = self.get(TAKAHASHI)
        employees.update_employee(TAKAHASHI, {**takahashi, "role": "admin"}, self.db_path, actor_id=SATO)
        sato = self.get(SATO)
        employees.update_employee(SATO, {**sato, "role": "user"}, self.db_path, actor_id=TAKAHASHI)
        self.assertEqual(self.get(SATO)["role"], "user")

    # --- 退職・再有効化 ---

    def test_退職するとログインできずライセンスが解除される(self):
        devices.change_status("PC-0002", "return", db_path=self.db_path)  # 高橋さんのPCを先に返却
        released = employees.deactivate_employee(TAKAHASHI, self.db_path, actor_id=SATO,
                                                 today=datetime.date(2026, 11, 1))
        self.assertEqual(released, 2)  # 文書作成ソフトとPDF編集ソフト
        self.assertIsNone(auth.authenticate("E0003", self.passwords["E0003"], self.db_path))
        self.assertEqual(licenses.list_licenses_of_employee(TAKAHASHI, self.db_path), [])
        self.assertEqual(self.logs()[0]["内容"], "ライセンス 2 件を解除")
        self.assertNotIn("E0003", [e["employee_code"] for e in employees.list_employees(self.db_path)])

    def test_使用中の機器がある社員は退職処理できない(self):
        self.assert_error("使用中の機器（MN-0001、PC-0001、SP-0001）があります。先に返却処理をしてください。",
                          employees.deactivate_employee, SUZUKI, actor_id=SATO)
        self.assertTrue(self.get(SUZUKI)["is_active"])

    def test_自分自身は退職処理できない(self):
        self.assert_error("自分自身は退職処理できません。ほかのIT管理者に依頼してください。",
                          employees.deactivate_employee, SATO, actor_id=SATO)

    def test_最後のIT管理者は退職処理できない(self):
        self.assert_error("IT管理者が1人もいなくなるため、退職処理できません。",
                          employees.deactivate_employee, SATO, actor_id=None)

    def test_再有効化するともう一度ログインできる(self):
        devices.change_status("PC-0002", "return", db_path=self.db_path)
        employees.deactivate_employee(TAKAHASHI, self.db_path, actor_id=SATO)
        self.assert_error("この社員はすでに退職処理されています。", employees.deactivate_employee, TAKAHASHI, actor_id=SATO)
        employees.reactivate_employee(TAKAHASHI, self.db_path, actor_id=SATO)
        self.assertIsNotNone(auth.authenticate("E0003", self.passwords["E0003"], self.db_path))
        self.assert_error("この社員は在籍中です。", employees.reactivate_employee, TAKAHASHI, actor_id=SATO)

    # --- パスワード再発行 ---

    def test_パスワードを再発行すると古いパスワードは使えない(self):
        new_password = employees.reset_password(TAKAHASHI, self.db_path, actor_id=SATO)
        self.assertIsNone(auth.authenticate("E0003", self.passwords["E0003"], self.db_path))
        self.assertIsNotNone(auth.authenticate("E0003", new_password, self.db_path))
        log = self.logs()[0]
        self.assertEqual((log["操作"], log["内容"]), ("パスワード再発行", ""))

    def test_退職した社員のパスワードは再発行できない(self):
        devices.change_status("PC-0002", "return", db_path=self.db_path)
        employees.deactivate_employee(TAKAHASHI, self.db_path, actor_id=SATO)
        self.assert_error("退職した社員のパスワードは再発行できません。", employees.reset_password, TAKAHASHI)

    # --- 一覧 ---

    def test_一覧には退職者と利用中の機器の台数も出る(self):
        devices.change_status("PC-0002", "return", db_path=self.db_path)
        employees.deactivate_employee(TAKAHASHI, self.db_path, actor_id=SATO)
        rows = {r["社員番号"]: r for r in employees.list_all_employees(self.db_path)}
        self.assertEqual((rows["E0002"]["利用中の機器"], rows["E0002"]["在籍"]), (3, "在籍"))
        self.assertEqual((rows["E0003"]["利用中の機器"], rows["E0003"]["在籍"]), (0, "退職"))
        self.assertEqual(rows["E0001"]["権限"], "IT管理者")


if __name__ == "__main__":
    unittest.main()
