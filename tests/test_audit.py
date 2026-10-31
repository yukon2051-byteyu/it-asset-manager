"""操作履歴（audit.py と、各処理からの記録）のテスト。

seed.py のサンプルデータ（id は登録順）：
  社員  1 佐藤（IT管理者） 2 鈴木 3 高橋 5 伊藤
  機器  1 PC-0001（鈴木・使用中・ウイルス対策ソフトを割当） 5 PC-0005（在庫） 8 MN-0002（在庫）
  ライセンス 1 文書作成ソフト（残1） 2 PDF編集ソフト（残0）
"""
import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

import applications
import audit
import auth
import db
import device_import
import devices
import licenses
import seed
from devices import ValidationError

# テストを速くするため、パスワード変換の回数を減らす（本番は auth.py の 600,000 回のまま）
auth.ITERATIONS = 1_000

SATO, SUZUKI, TAKAHASHI, ITO = 1, 2, 3, 5
WORD, PDF = 1, 2
MN2 = 8
TODAY = datetime.date(2026, 10, 31)


class TestAudit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test.db"
        seed.seed(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def logs(self, **conditions):
        return audit.list_logs(db_path=self.db_path, **conditions)

    def latest(self):
        return self.logs()[0]

    # --- 変更内容の文章化 ---

    def test_変わった項目だけを書く(self):
        before = {"location": "倉庫", "maker": "A社", "note": None}
        after = {"location": "本社4F", "maker": "A社", "note": ""}
        labels = {"location": "設置場所", "maker": "メーカー", "note": "備考"}
        self.assertEqual(audit.describe_changes(before, after, labels), "設置場所：倉庫 → 本社4F")

    def test_空欄は空欄と書く(self):
        self.assertEqual(audit.describe_changes({"note": "メモ"}, {"note": None}, {"note": "備考"}),
                         "備考：メモ → （空欄）")

    # --- 機器 ---

    def test_機器の登録が操作者つきで記録される(self):
        devices.register_device({"category": "pc", "maker": "サンプル電機", "model": "NB-1600", "serial_no": "SN-NEW"},
                                self.db_path, actor_id=SATO)
        log = self.latest()
        self.assertEqual((log["操作者"], log["対象"], log["対象名"], log["操作"]),
                         ("E0001 佐藤 花子", "機器", "PC-0007", "登録"))
        self.assertEqual(log["内容"], "PC サンプル電機 NB-1600（シリアル SN-NEW）")

    def test_機器の変更は変わった項目だけ記録される(self):
        device = devices.get_device("PC-0005", self.db_path)
        devices.update_device("PC-0005", {**device, "location": "本社4F"}, self.db_path, actor_id=SATO)
        self.assertEqual(self.latest()["内容"], "設置場所：情報システム部 倉庫 → 本社4F")

    def test_何も変えずに更新したら記録しない(self):
        device = devices.get_device("PC-0005", self.db_path)
        devices.update_device("PC-0005", device, self.db_path, actor_id=SATO)
        self.assertEqual(self.logs(), [])

    def test_状態の変更では利用者の変化も記録される(self):
        devices.change_status("PC-0005", "assign", TAKAHASHI, self.db_path, actor_id=SATO)
        devices.change_status("PC-0005", "return", None, self.db_path, actor_id=SATO)
        details = [log["内容"] for log in self.logs(keyword="PC-0005")]
        self.assertEqual(details, [
            "返却：使用中 → 在庫、利用者：高橋 美咲 → なし",
            "割当：在庫 → 使用中、利用者：なし → 高橋 美咲",
        ])

    def test_廃棄では解除したライセンスの件数も記録される(self):
        devices.change_status("PC-0001", "return", None, self.db_path, actor_id=SATO)
        devices.change_status("PC-0001", "dispose", None, self.db_path, actor_id=SATO)
        self.assertEqual(self.latest()["内容"], "廃棄：在庫 → 廃棄済、利用者：なし → なし、このPCのライセンス 1 件を解除")

    def test_CSV取込は1台ずつ記録される(self):
        content = "種別,メーカー,型番,シリアル番号,利用者の社員番号\r\nPC,A社,X1,SN-A,E0003\r\nモニター,B社,M1,SN-B,\r\n"
        device_import.import_csv(content.encode("utf-8"), self.db_path, actor_id=SATO)
        logs = self.logs()
        self.assertEqual([(log["対象名"], log["操作"]) for log in logs], [("MN-0003", "CSV取込"), ("PC-0007", "CSV取込")])
        self.assertEqual(logs[1]["内容"], "PC A社 X1（シリアル SN-A）、状態：使用中（CSVの2行目）")

    def test_失敗した操作は記録されない(self):
        with self.assertRaises(ValidationError):
            devices.change_status("PC-0001", "assign", TAKAHASHI, self.db_path, actor_id=SATO)  # 使用中は割当不可
        with self.assertRaises(ValidationError):
            licenses.assign_license(PDF, ITO, None, TODAY, self.db_path, actor_id=SATO)  # 残数0
        self.assertEqual(self.logs(), [])

    # --- ライセンス ---

    def test_ライセンスの登録変更割当解除が記録される(self):
        new_id = licenses.register_license({"name": "図面ソフト", "license_type": "user", "quantity": 2},
                                           self.db_path, actor_id=SATO)
        lic = licenses.get_license(new_id, self.db_path)
        licenses.update_license(new_id, {**lic, "quantity": 3}, self.db_path, actor_id=SATO)
        licenses.assign_license(new_id, ITO, None, TODAY, self.db_path, actor_id=SATO)
        assignment_id = licenses.list_assignments(new_id, self.db_path)[0]["id"]
        licenses.release_license(assignment_id, TODAY, self.db_path, actor_id=SATO)
        self.assertEqual([(log["操作"], log["内容"]) for log in self.logs(target_type="license")], [
            ("割当解除", "割当先：E0005 伊藤 さくら"),
            ("割当", "割当先：E0005 伊藤 さくら（残数 2）"),
            ("変更", "保有数：2 → 3"),
            ("登録", "人単位、保有数 2、有効期限 なし"),
        ])

    # --- 申請 ---

    def test_申請と承認では申請者と承認者がそれぞれ記録される(self):
        request_id = applications.create_request(
            TAKAHASHI, {"request_type": "lend", "category": "monitor", "detail": "在宅勤務",
                        "start_date": datetime.date(2026, 11, 1), "due_date": datetime.date(2026, 11, 30)},
            TODAY, self.db_path)
        applications.approve(request_id, SATO, MN2, db_path=self.db_path)
        logs = self.logs()
        self.assertEqual([(log["操作者"], log["対象名"], log["操作"]) for log in logs], [
            ("E0001 佐藤 花子", "R-0001", "承認"),
            ("E0001 佐藤 花子", "MN-0002", "状態変更"),  # 承認による機器の変化も、承認した人の操作として残る
            ("E0003 高橋 美咲", "R-0001", "申請"),
        ])
        self.assertEqual(logs[1]["内容"], "貸出：在庫 → 貸出中、利用者：なし → 高橋 美咲")
        self.assertEqual(logs[2]["内容"], "貸出：モニター（2026-11-01〜2026-11-30）、在宅勤務")

    def test_却下と保留は理由も記録される(self):
        data = {"request_type": "lend", "category": "monitor", "detail": "在宅勤務",
                "start_date": datetime.date(2026, 11, 1), "due_date": datetime.date(2026, 11, 30)}
        first = applications.create_request(TAKAHASHI, data, TODAY, self.db_path)
        applications.hold(first, SATO, "入荷待ち", db_path=self.db_path)
        applications.reject(first, SATO, "予算外", db_path=self.db_path)
        self.assertEqual([log["内容"] for log in self.logs(keyword="R-0001")][:2], ["貸出、理由：予算外", "理由：入荷待ち"])

    # --- 一覧・保護 ---

    def test_種類とキーワードで絞り込める(self):
        devices.change_status("PC-0005", "assign", TAKAHASHI, self.db_path, actor_id=SATO)
        licenses.assign_license(WORD, ITO, None, TODAY, self.db_path, actor_id=SATO)
        self.assertEqual([log["対象"] for log in self.logs(target_type="device")], ["機器"])
        self.assertEqual([log["対象名"] for log in self.logs(keyword="伊藤")], ["文書作成ソフト"])  # 内容も検索対象
        self.assertEqual([log["対象名"] for log in self.logs(keyword="ｐｃ－０００５")], ["PC-0005"])

    def test_操作者が分からない記録は不明と表示する(self):
        devices.change_status("PC-0005", "assign", TAKAHASHI, self.db_path)
        self.assertEqual(self.latest()["操作者"], "（不明）")

    def test_操作履歴は変更も削除もできない(self):
        devices.change_status("PC-0005", "assign", TAKAHASHI, self.db_path, actor_id=SATO)
        conn = db.get_connection(self.db_path)
        try:
            for sql in ("UPDATE audit_logs SET detail = '改ざん'", "DELETE FROM audit_logs"):
                with self.subTest(sql=sql):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(sql)
        finally:
            conn.close()
        self.assertEqual(len(self.logs()), 1)


if __name__ == "__main__":
    unittest.main()
