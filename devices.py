"""機器（PC・モニター・スマートフォン）のデータを扱うモジュール。"""
import datetime
import re

import db

# データベースに保存するコードと、画面に表示する名前の対応表（設計書 5章）
CATEGORY_LABELS = {
    "pc": "PC",
    "monitor": "モニター",
    "smartphone": "スマートフォン",
}

STATUS_LABELS = {
    "in_stock": "在庫",
    "in_use": "使用中",
    "lent": "貸出中",
    "repair": "修理中",
    "lost": "紛失",
    "disposed": "廃棄済",
}

# 資産番号の頭文字（設計書 5.3）
ASSET_NO_PREFIXES = {
    "pc": "PC-",
    "monitor": "MN-",
    "smartphone": "SP-",
}

# 登録時に入力する項目。種別ごとの項目は、その種別のときだけ保存する
COMMON_FIELDS = ["category", "maker", "model", "serial_no", "location", "purchase_date", "warranty_end", "note"]
CATEGORY_FIELDS = {
    "pc": ["os"],
    "monitor": ["screen_size"],
    "smartphone": ["imei", "phone_number", "carrier", "contract_end"],
}
REQUIRED_FIELDS = {"maker": "メーカー", "model": "型番", "serial_no": "シリアル番号"}


class ValidationError(Exception):
    """入力内容に誤りがあることを表すエラー。errors に誤りの一覧を持つ。"""

    def __init__(self, errors):
        super().__init__("、".join(errors))
        self.errors = errors


def list_devices(categories=None, statuses=None, employee_id=None, db_path=db.DB_PATH):
    """機器の一覧を、画面表示用の日本語の列名で返す。

    categories / statuses にコードのリストを渡すと、その値だけに絞り込む。
    空のリストや None のときは絞り込まない。
    employee_id を渡すと、その社員が利用している機器だけに絞り込む。
    """
    sql = (
        "SELECT d.asset_no, d.category, d.maker, d.model, d.status,"
        " e.name AS user_name, e.department, d.location"
        " FROM devices d"
        " LEFT JOIN employees e ON e.id = d.employee_id"  # 利用者がいない機器も一覧に出すため LEFT JOIN
    )
    conditions = []
    params = []
    if categories:
        conditions.append(f"d.category IN ({', '.join('?' for _ in categories)})")
        params.extend(categories)
    if statuses:
        conditions.append(f"d.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if employee_id is not None:
        conditions.append("d.employee_id = ?")
        params.append(employee_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY d.asset_no"

    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [
        {
            "資産番号": r["asset_no"],
            "種別": CATEGORY_LABELS[r["category"]],
            "メーカー": r["maker"],
            "型番": r["model"],
            "状態": STATUS_LABELS[r["status"]],
            "利用者": r["user_name"] or "",
            "部署": r["department"] or "",
            "設置場所": r["location"] or "",
        }
        for r in rows
    ]


def clean_device_input(data):
    """入力をそろえる：前後の空白を取り、空欄は None にし、種別に関係ない項目は捨てる。"""
    fields = COMMON_FIELDS + CATEGORY_FIELDS.get(data.get("category"), [])
    values = {}
    for key in fields:
        value = data.get(key)
        if isinstance(value, str):
            value = value.strip() or None
        values[key] = value
    return values


def validate_device(values, conn):
    """入力チェック（F-17）。誤りがあれば、画面に出すメッセージの一覧を返す。"""
    errors = []
    if values["category"] not in CATEGORY_LABELS:
        errors.append("種別を選んでください。")
    for key, label in REQUIRED_FIELDS.items():
        if not values.get(key):
            errors.append(f"{label}を入力してください。")

    serial_no = values.get("serial_no")
    if serial_no and _exists(conn, "serial_no", serial_no):
        errors.append(f"シリアル番号「{serial_no}」はすでに登録されています。")

    imei = values.get("imei")
    if imei:
        # isdigit() は全角数字も通してしまうため、半角数字だけを許す正規表現で確かめる
        if not re.fullmatch(r"[0-9]{15}", imei):
            errors.append("IMEIは15桁の半角数字で入力してください。")
        elif _exists(conn, "imei", imei):
            errors.append(f"IMEI「{imei}」はすでに登録されています。")

    purchase, warranty = values.get("purchase_date"), values.get("warranty_end")
    if purchase and warranty and warranty < purchase:
        errors.append("保証期限は購入日より後の日付にしてください。")

    size = values.get("screen_size")
    if size is not None and size <= 0:
        errors.append("画面サイズは0より大きい値を入力してください。")
    return errors


def _exists(conn, column, value):
    """devices の指定した列に、同じ値がすでにあるか。column にはプログラム内の固定の列名だけを渡す。"""
    return conn.execute(f"SELECT 1 FROM devices WHERE {column} = ?", (value,)).fetchone() is not None


def next_asset_no(conn, category):
    """次の資産番号を作る（例：PC-0006 まであれば PC-0007）。廃棄済みの番号も数えるので、番号は使い回さない。"""
    prefix = ASSET_NO_PREFIXES[category]
    rows = conn.execute("SELECT asset_no FROM devices WHERE asset_no LIKE ?", (prefix + "%",)).fetchall()
    numbers = [int(r["asset_no"][len(prefix):]) for r in rows if r["asset_no"][len(prefix):].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def register_device(data, db_path=db.DB_PATH):
    """機器を「在庫」の状態で登録し（F-11）、発行した資産番号を返す。入力に誤りがあれば ValidationError を出す。"""
    values = clean_device_input(data)
    conn = db.get_connection(db_path)
    try:
        errors = validate_device(values, conn)
        if errors:
            raise ValidationError(errors)

        # 日付は「YYYY-MM-DD」の文字列で保存する（設計書 1章）
        for key, value in values.items():
            if isinstance(value, datetime.date):
                values[key] = value.isoformat()

        with conn:
            asset_no = next_asset_no(conn, values["category"])
            columns = ["asset_no", *values]
            conn.execute(
                f"INSERT INTO devices ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [asset_no, *values.values()],
            )
        return asset_no
    finally:
        conn.close()
