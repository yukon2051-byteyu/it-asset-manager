"""licenses.py のライセンス管理（一覧・登録・変更・割当・解除）のテスト。

seed.py のサンプルデータ（id は登録順）：
  1 文書作成ソフト   人単位  保有5・割当4（E0001〜E0004）・期限 2027-03-31
  2 PDF編集ソフト    人単位  保有2・割当2（E0002, E0003）・期限 2026-11-20
  3 ウイルス対策ソフト PC単位 保有10・割当4（PC-0001〜PC-0004）・期限 2027-06-30
"""
import datetime
import tempfile
import unittest
from pathlib import Path

import auth
import db
import licenses
import seed
from devices import ValidationError

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

TODAY = datetime.date(2026, 10, 31)
WORD, PDF, ANTIVIRUS = 1, 2, 3
SATO, SUZUKI, ITO = 1, 2, 5          # 社員の id
PC5, PC6, MN1 = 5, 6, 7              # 機器の id（PC-0005, PC-0006, MN-0001）


class TestLicenseManage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def rows(self, today=TODAY):
        return {r["ライセンス名"]: r for r in licenses.list_licenses(today, self.db_path)}

    def assign(self, license_id, employee_id=None, device_id=None, today=TODAY):
        licenses.assign_license(license_id, employee_id, device_id, today, self.db_path)

    def assert_error(self, expected_message, func, *args, **kwargs):
        with self.assertRaises(ValidationError) as ctx:
            func(*args, **kwargs)
        self.assertIn(expected_message, ctx.exception.errors)

    def run_sql(self, sql, params=()):
        conn = db.get_connection(self.db_path)
        with conn:
            conn.execute(sql, params)
        conn.close()

    # --- 期限の判定 ---

    def test_期限の状態を判定できる(self):
        self.assertIsNone(licenses.expiry_status(None, TODAY))
        self.assertEqual(licenses.expiry_status("2026-10-30", TODAY), "expired")
        self.assertEqual(licenses.expiry_status("2026-10-31", TODAY), "soon")   # 当日はまだ使える
        self.assertEqual(licenses.expiry_status("2026-11-30", TODAY), "soon")   # ちょうど30日後
        self.assertIsNone(licenses.expiry_status("2026-12-01", TODAY))         # 31日後

    # --- 一覧 ---

    def test_一覧に割当数と残数が出る(self):
        rows = self.rows()
        self.assertEqual(list(rows), ["PDF編集ソフト", "ウイルス対策ソフト", "文書作成ソフト"])
        word = rows["文書作成ソフト"]
        self.assertEqual((word["保有数"], word["割当数"], word["残数"]), (5, 4, 1))
        self.assertEqual(word["注意"], "")

    def test_期限間近と残数なしに注意が付く(self):
        self.assertEqual(self.rows()["PDF編集ソフト"]["注意"], "期限間近（あと20日）・残数なし")

    def test_期限切れに注意が付く(self):
        self.assertEqual(self.rows(today=datetime.date(2026, 11, 21))["PDF編集ソフト"]["注意"], "期限切れ・残数なし")

    def test_解除した割当は残数に戻る(self):
        self.run_sql("UPDATE license_assignments SET released_on = '2026-10-31' WHERE license_id = ? AND employee_id = ?",
                     (PDF, SUZUKI))
        self.assertEqual(self.rows()["PDF編集ソフト"]["残数"], 1)

    # --- 登録 ---

    def test_ライセンスを登録できる(self):
        new_id = licenses.register_license(
            {"name": " 図面ソフト ", "vendor": "", "license_type": "device", "quantity": 3,
             "expires_on": datetime.date(2027, 3, 31), "cost": 90000}, self.db_path)
        lic = licenses.get_license(new_id, self.db_path)
        self.assertEqual((lic["name"], lic["vendor"], lic["quantity"]), ("図面ソフト", None, 3))
        self.assertEqual(lic["expires_on"], datetime.date(2027, 3, 31))
        self.assertEqual(self.rows()["図面ソフト"]["残数"], 3)

    def test_登録の入力ミスはまとめてエラーになる(self):
        with self.assertRaises(ValidationError) as ctx:
            licenses.register_license({"name": "", "license_type": "site", "quantity": 0, "cost": -1}, self.db_path)
        self.assertEqual(ctx.exception.errors, [
            "ライセンス名を入力してください。",
            "割当の単位を選んでください。",
            "保有数は1以上の整数で入力してください。",
            "費用は0以上の整数（円）で入力してください。",
        ])

    def test_同じ名前のライセンスは登録できない(self):
        self.assert_error("ライセンス名「文書作成ソフト」はすでに登録されています。", licenses.register_license,
                          {"name": "文書作成ソフト", "license_type": "user", "quantity": 1}, self.db_path)

    # --- 変更 ---

    def test_保有数と期限を変更できる(self):
        lic = licenses.get_license(PDF, self.db_path)
        licenses.update_license(PDF, {**lic, "quantity": 5, "expires_on": datetime.date(2027, 11, 20)}, self.db_path)
        row = self.rows()["PDF編集ソフト"]
        self.assertEqual((row["保有数"], row["残数"], row["有効期限"], row["注意"]), (5, 3, "2027-11-20", ""))

    def test_保有数を割当数より少なくできない(self):
        lic = licenses.get_license(WORD, self.db_path)
        self.assert_error("保有数は、今の割当数（4）より少なくできません。先に割当を解除してください。",
                          licenses.update_license, WORD, {**lic, "quantity": 3}, self.db_path)

    def test_割当の単位は変更されない(self):
        lic = licenses.get_license(WORD, self.db_path)
        licenses.update_license(WORD, {**lic, "license_type": "device"}, self.db_path)
        self.assertEqual(licenses.get_license(WORD, self.db_path)["license_type"], "user")

    def test_自分と同じ名前のままなら変更できる(self):
        lic = licenses.get_license(WORD, self.db_path)
        licenses.update_license(WORD, {**lic, "note": "更新済み"}, self.db_path)
        self.assertEqual(licenses.get_license(WORD, self.db_path)["note"], "更新済み")

    # --- 割当 ---

    def test_人単位のライセンスを社員に割り当てられる(self):
        self.assign(WORD, employee_id=ITO)
        self.assertEqual(self.rows()["文書作成ソフト"]["残数"], 0)
        targets = [a["割当先"] for a in licenses.list_assignments(WORD, self.db_path)]
        self.assertIn("E0005 伊藤 さくら", targets)

    def test_PC単位のライセンスをPCに割り当てられる(self):
        self.assign(ANTIVIRUS, device_id=PC5)
        targets = [a["割当先"] for a in licenses.list_assignments(ANTIVIRUS, self.db_path)]
        self.assertIn("PC-0005（利用者：なし）", targets)

    def test_残数がなければ割り当てられない(self):
        self.assert_error("残数がないため割り当てられません。", self.assign, PDF, employee_id=ITO)
        self.assertEqual(self.rows()["PDF編集ソフト"]["割当数"], 2)  # 追加が取り消されている

    def test_期限切れのライセンスは割り当てられない(self):
        self.assert_error("有効期限が切れたライセンスは割り当てられません。",
                          self.assign, WORD, employee_id=ITO, today=datetime.date(2027, 4, 1))

    def test_同じ相手に二重には割り当てられない(self):
        self.assert_error("すでにこのライセンスを割り当てています。", self.assign, WORD, employee_id=SATO)

    def test_人単位を社員以外に割り当てるとエラー(self):
        self.assert_error("割り当てる在籍中の社員を選んでください。", self.assign, WORD, device_id=PC5)

    def test_退職した社員には割り当てられない(self):
        self.run_sql("UPDATE employees SET is_active = 0 WHERE id = ?", (ITO,))
        self.assert_error("割り当てる在籍中の社員を選んでください。", self.assign, WORD, employee_id=ITO)

    def test_PC単位をモニターや廃棄済のPCには割り当てられない(self):
        self.assert_error("割り当てるPC（廃棄済を除く）を選んでください。", self.assign, ANTIVIRUS, device_id=MN1)
        self.run_sql("UPDATE devices SET status = 'disposed' WHERE id = ?", (PC6,))
        self.assert_error("割り当てるPC（廃棄済を除く）を選んでください。", self.assign, ANTIVIRUS, device_id=PC6)

    def test_割当先の候補のPCに廃棄済は出ない(self):
        self.run_sql("UPDATE devices SET status = 'disposed' WHERE id = ?", (PC6,))
        pcs = licenses.list_assignable_pcs(self.db_path)
        self.assertNotIn(PC6, pcs)
        self.assertNotIn(MN1, pcs)
        self.assertEqual(pcs[PC5], "PC-0005（利用者：なし）")

    # --- 解除 ---

    def test_割当を解除すると残数に戻り記録は残る(self):
        assignment = licenses.list_assignments(PDF, self.db_path)[0]
        licenses.release_license(assignment["id"], TODAY, self.db_path)
        self.assertEqual(self.rows()["PDF編集ソフト"]["残数"], 1)
        conn = db.get_connection(self.db_path)
        released_on = conn.execute("SELECT released_on FROM license_assignments WHERE id = ?",
                                   (assignment["id"],)).fetchone()[0]
        conn.close()
        self.assertEqual(released_on, "2026-10-31")

    def test_解除済みの割当をもう一度解除するとエラー(self):
        assignment = licenses.list_assignments(PDF, self.db_path)[0]
        licenses.release_license(assignment["id"], TODAY, self.db_path)
        self.assert_error("この割当はすでに解除されています。", licenses.release_license,
                          assignment["id"], TODAY, self.db_path)


if __name__ == "__main__":
    unittest.main()
