"""動作確認用の架空のサンプルデータを登録するスクリプト。

すでにデータがある場合は、二重登録やデータの上書きを防ぐため何もしない。
パスワードは実行のたびにランダムに作り、ソースコードには書かない。
"""
import secrets
import string

import auth
import db

PASSWORD_LENGTH = 12
PASSWORD_CHARS = string.ascii_letters + string.digits

# 社員（すべて架空）
EMPLOYEES = [
    ("E0001", "佐藤 花子", "情報システム部", "sato@example.com", "admin"),
    ("E0002", "鈴木 一郎", "営業部", "suzuki@example.com", "user"),
    ("E0003", "高橋 美咲", "経理部", "takahashi@example.com", "user"),
    ("E0004", "田中 健太", "営業部", "tanaka@example.com", "user"),
    ("E0005", "伊藤 さくら", "総務部", "ito@example.com", "user"),
]

# 機器（メーカー名・型番・電話番号などもすべて架空）
# 利用者は社員番号で指定し、登録時に社員の id に置き換える
DEVICES = [
    {"asset_no": "PC-0001", "category": "pc", "maker": "サンプル電機", "model": "NB-1400", "serial_no": "SN-PC-0001",
     "status": "in_use", "user": "E0002", "location": "本社3F", "os": "Windows 11"},
    {"asset_no": "PC-0002", "category": "pc", "maker": "サンプル電機", "model": "NB-1400", "serial_no": "SN-PC-0002",
     "status": "in_use", "user": "E0003", "location": "本社2F", "os": "Windows 11"},
    {"asset_no": "PC-0003", "category": "pc", "maker": "テスト工業", "model": "TX-200", "serial_no": "SN-PC-0003",
     "status": "in_use", "user": "E0004", "location": "本社3F", "os": "Windows 11"},
    {"asset_no": "PC-0004", "category": "pc", "maker": "テスト工業", "model": "TX-200", "serial_no": "SN-PC-0004",
     "status": "lent", "user": "E0005", "location": "本社1F", "os": "Windows 11"},
    {"asset_no": "PC-0005", "category": "pc", "maker": "サンプル電機", "model": "NB-1500", "serial_no": "SN-PC-0005",
     "status": "in_stock", "user": None, "location": "情報システム部 倉庫", "os": "Windows 11"},
    {"asset_no": "PC-0006", "category": "pc", "maker": "サンプル電機", "model": "NB-1400", "serial_no": "SN-PC-0006",
     "status": "repair", "user": None, "location": "修理業者", "os": "Windows 11"},
    {"asset_no": "MN-0001", "category": "monitor", "maker": "テスト工業", "model": "MN-24", "serial_no": "SN-MN-0001",
     "status": "in_use", "user": "E0002", "location": "本社3F", "screen_size": 24},
    {"asset_no": "MN-0002", "category": "monitor", "maker": "テスト工業", "model": "MN-27", "serial_no": "SN-MN-0002",
     "status": "in_stock", "user": None, "location": "情報システム部 倉庫", "screen_size": 27},
    {"asset_no": "SP-0001", "category": "smartphone", "maker": "架空モバイル", "model": "KM-10", "serial_no": "SN-SP-0001",
     "status": "in_use", "user": "E0002", "imei": "350000000000001", "phone_number": "090-0000-0001",
     "carrier": "サンプル通信", "contract_end": "2027-03-31"},
    {"asset_no": "SP-0002", "category": "smartphone", "maker": "架空モバイル", "model": "KM-10", "serial_no": "SN-SP-0002",
     "status": "in_stock", "user": None, "location": "情報システム部 倉庫", "imei": "350000000000002",
     "phone_number": "090-0000-0002", "carrier": "サンプル通信", "contract_end": "2027-03-31"},
]

# ライセンス（ソフトウェア名・提供元も架空）
LICENSES = [
    ("文書作成ソフト", "サンプルソフト社", "user", 5, "2027-03-31", 150000),
    ("PDF編集ソフト", "テストソフト社", "user", 2, "2026-11-20", 40000),  # 期限が近い・残数0の例
    ("ウイルス対策ソフト", "架空セキュリティ社", "device", 10, "2027-06-30", 60000),
]

# ライセンス割当（ライセンス名, 割当先の種類, 社員番号または資産番号）
ASSIGNMENTS = [
    ("文書作成ソフト", "employee", "E0001"),
    ("文書作成ソフト", "employee", "E0002"),
    ("文書作成ソフト", "employee", "E0003"),
    ("文書作成ソフト", "employee", "E0004"),
    ("PDF編集ソフト", "employee", "E0002"),
    ("PDF編集ソフト", "employee", "E0003"),
    ("ウイルス対策ソフト", "device", "PC-0001"),
    ("ウイルス対策ソフト", "device", "PC-0002"),
    ("ウイルス対策ソフト", "device", "PC-0003"),
    ("ウイルス対策ソフト", "device", "PC-0004"),
]

DEVICE_COLUMNS = ["asset_no", "category", "maker", "model", "serial_no", "status", "location",
                  "os", "screen_size", "imei", "phone_number", "carrier", "contract_end"]


def make_password():
    """推測されにくいランダムなパスワードを作る（secrets は暗号用の乱数を使う）。"""
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(PASSWORD_LENGTH))


def seed(db_path=db.DB_PATH):
    """サンプルデータを登録し、{社員番号: パスワード} を返す。既にデータがあれば何もせず None を返す。"""
    db.init_db(db_path)
    conn = db.get_connection(db_path)
    try:
        if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] > 0:
            return None

        passwords = {}
        # with conn: の中は1つのまとまり（トランザクション）として扱われる。
        # 途中でエラーが起きたら全部取り消されるので、中途半端なデータが残らない
        with conn:
            employee_ids = {}
            for code, name, dept, email, role in EMPLOYEES:
                passwords[code] = make_password()
                cur = conn.execute(
                    "INSERT INTO employees (employee_code, name, department, email, role, password_hash)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (code, name, dept, email, role, auth.hash_password(passwords[code])),
                )
                employee_ids[code] = cur.lastrowid

            device_ids = {}
            placeholders = ", ".join("?" for _ in DEVICE_COLUMNS)
            for d in DEVICES:
                values = [d.get(col) for col in DEVICE_COLUMNS]  # 指定のない項目は None（空欄）になる
                cur = conn.execute(
                    f"INSERT INTO devices ({', '.join(DEVICE_COLUMNS)}, employee_id)"
                    f" VALUES ({placeholders}, ?)",
                    (*values, employee_ids.get(d["user"])),
                )
                device_ids[d["asset_no"]] = cur.lastrowid

            license_ids = {}
            for name, vendor, ltype, qty, expires, cost in LICENSES:
                cur = conn.execute(
                    "INSERT INTO licenses (name, vendor, license_type, quantity, expires_on, cost)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (name, vendor, ltype, qty, expires, cost),
                )
                license_ids[name] = cur.lastrowid

            for lic_name, target_type, target in ASSIGNMENTS:
                emp_id = employee_ids[target] if target_type == "employee" else None
                dev_id = device_ids[target] if target_type == "device" else None
                conn.execute(
                    "INSERT INTO license_assignments (license_id, employee_id, device_id, assigned_on)"
                    " VALUES (?, ?, ?, '2026-10-01')",
                    (license_ids[lic_name], emp_id, dev_id),
                )
        return passwords
    finally:
        conn.close()


if __name__ == "__main__":
    passwords = seed()
    if passwords is None:
        print("すでにデータがあるため、何もしませんでした。")
    else:
        print("サンプルデータを登録しました。")
        print("ログイン用のパスワードです（この画面でしか表示されません。控えておいてください）:")
        for code, name, _, _, role in EMPLOYEES:
            print(f"  {code}  {name}（{'IT管理者' if role == 'admin' else '一般社員'}）  {passwords[code]}")
