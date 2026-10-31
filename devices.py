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
DATE_FIELDS = ["purchase_date", "warranty_end", "contract_end"]


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


def validate_device(values, conn, exclude_id=None):
    """入力チェック（F-17）。誤りがあれば、画面に出すメッセージの一覧を返す。

    更新のときは exclude_id に自分の id を渡し、重複チェックから自分自身を除く。
    """
    errors = []
    if values["category"] not in CATEGORY_LABELS:
        errors.append("種別を選んでください。")
    for key, label in REQUIRED_FIELDS.items():
        if not values.get(key):
            errors.append(f"{label}を入力してください。")

    serial_no = values.get("serial_no")
    if serial_no and _exists(conn, "serial_no", serial_no, exclude_id):
        errors.append(f"シリアル番号「{serial_no}」はすでに登録されています。")

    imei = values.get("imei")
    if imei:
        # isdigit() は全角数字も通してしまうため、半角数字だけを許す正規表現で確かめる
        if not re.fullmatch(r"[0-9]{15}", imei):
            errors.append("IMEIは15桁の半角数字で入力してください。")
        elif _exists(conn, "imei", imei, exclude_id):
            errors.append(f"IMEI「{imei}」はすでに登録されています。")

    purchase, warranty = values.get("purchase_date"), values.get("warranty_end")
    if purchase and warranty and warranty < purchase:
        errors.append("保証期限は購入日より後の日付にしてください。")

    size = values.get("screen_size")
    if size is not None and size <= 0:
        errors.append("画面サイズは0より大きい値を入力してください。")
    return errors


def _exists(conn, column, value, exclude_id=None):
    """devices の指定した列に、同じ値がすでにあるか（exclude_id の機器は除く）。

    column にはプログラム内の固定の列名だけを渡す。
    """
    sql = f"SELECT 1 FROM devices WHERE {column} = ?"
    params = [value]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone() is not None


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

        with conn:
            asset_no = next_asset_no(conn, values["category"])
            insert_device(conn, asset_no, values)
        return asset_no
    finally:
        conn.close()


def insert_device(conn, asset_no, values, employee_id=None):
    """チェック済みの機器を1台保存する（画面からの登録とCSV取り込みで共通）。

    利用者を指定したら「使用中」、指定しなければ「在庫」で登録する。
    """
    values = dict(values)  # 呼び出し元の辞書を書き換えないようにコピーする
    _dates_to_text(values)
    values["status"] = "in_use" if employee_id is not None else "in_stock"
    values["employee_id"] = employee_id
    columns = ["asset_no", *values]
    conn.execute(
        f"INSERT INTO devices ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        [asset_no, *values.values()],
    )


def _dates_to_text(values):
    """日付は「YYYY-MM-DD」の文字列で保存する（設計書 1章）。"""
    for key, value in values.items():
        if isinstance(value, datetime.date):
            values[key] = value.isoformat()


def get_device(asset_no, db_path=db.DB_PATH):
    """機器1台の情報を返す（見つからなければ None）。日付は画面で扱いやすいよう date 型にする。"""
    conn = db.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT d.*, e.name AS user_name FROM devices d"
            " LEFT JOIN employees e ON e.id = d.employee_id"
            " WHERE d.asset_no = ?",
            (asset_no,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    device = dict(row)
    for key in DATE_FIELDS:
        if device[key]:
            device[key] = datetime.date.fromisoformat(device[key])
    return device


def update_device(asset_no, data, db_path=db.DB_PATH):
    """機器の情報を更新する（F-12）。入力に誤りがあれば ValidationError を出す。

    資産番号・種別・状態・利用者は変更しない。状態と利用者は、割当・返却などの業務操作で変える。
    """
    conn = db.get_connection(db_path)
    try:
        current = conn.execute("SELECT id, category, status FROM devices WHERE asset_no = ?", (asset_no,)).fetchone()
        if current is None:
            raise ValidationError([f"資産番号「{asset_no}」の機器が見つかりません。"])
        if current["status"] == "disposed":
            raise ValidationError(["廃棄済の機器は変更できません。"])

        # 種別は画面から送られた値ではなく、登録済みの値を使う（種別は変更させない）
        values = clean_device_input({**data, "category": current["category"]})
        errors = validate_device(values, conn, exclude_id=current["id"])
        if errors:
            raise ValidationError(errors)

        _dates_to_text(values)
        del values["category"]
        assignments = ", ".join(f"{column} = ?" for column in values)
        with conn:
            conn.execute(
                f"UPDATE devices SET {assignments}, updated_at = datetime('now', 'localtime') WHERE id = ?",
                [*values.values(), current["id"]],
            )
    finally:
        conn.close()



# 状態を変える操作：(操作できる元の状態, 操作後の状態)。要件定義書 4.1 の図にもとづく
TRANSITIONS = {
    "assign": ({"in_stock"}, "in_use"),
    "lend": ({"in_stock"}, "lent"),
    "return": ({"in_use", "lent"}, "in_stock"),
    "failure": ({"in_use", "lent"}, "repair"),
    "repaired": ({"repair"}, "in_stock"),
    "loss": ({"in_use", "lent"}, "lost"),
    "dispose": ({"in_stock", "repair"}, "disposed"),
}
ACTION_LABELS = {
    "assign": "割当", "lend": "貸出", "return": "返却", "failure": "故障",
    "repaired": "修理完了", "loss": "紛失", "dispose": "廃棄",
}
# 管理者が「割当・返却・廃棄」画面で直接行える操作。貸出・故障・紛失は申請の承認を通して行う
MANUAL_ACTIONS = ["assign", "return", "repaired", "dispose"]
# 利用者を必ず指定する操作（それ以外の操作では利用者を外す）
ACTIONS_WITH_EMPLOYEE = {"assign", "lend"}
# そのPCに割り当てたライセンスを解除する操作（もう使えないPCのため）
ACTIONS_RELEASING_LICENSES = {"loss", "dispose"}


def available_actions(status):
    """今の状態から、管理者が直接行える操作の一覧を返す（例：在庫なら割当と廃棄）。"""
    return [a for a in MANUAL_ACTIONS if status in TRANSITIONS[a][0]]


def change_status(asset_no, action, employee_id=None, db_path=db.DB_PATH):
    """割当・返却・廃棄など（F-25 / F-16）で機器の状態を変え、変更後の状態を返す。"""
    conn = db.get_connection(db_path)
    try:
        with conn:
            return apply_status_change(conn, asset_no, action, employee_id)
    finally:
        conn.close()


def apply_status_change(conn, asset_no, action, employee_id=None):
    """状態の変更を、渡された接続の中で行う（申請の承認と同じトランザクションで使うため、確定は呼び出し側）。

    状態と利用者は必ずセットで変える。ルールに合わない操作は ValidationError を出す。
    """
    if action not in TRANSITIONS:
        raise ValidationError(["操作が正しくありません。"])
    sources, new_status = TRANSITIONS[action]

    current = conn.execute("SELECT id, status FROM devices WHERE asset_no = ?", (asset_no,)).fetchone()
    if current is None:
        raise ValidationError([f"資産番号「{asset_no}」の機器が見つかりません。"])
    if current["status"] not in sources:
        raise ValidationError([f"{STATUS_LABELS[current['status']]}の機器は{ACTION_LABELS[action]}できません。"])

    new_employee_id = None
    if action in ACTIONS_WITH_EMPLOYEE:
        if employee_id is None:
            raise ValidationError(["割り当てる社員を選んでください。"])
        active = conn.execute("SELECT 1 FROM employees WHERE id = ? AND is_active = 1", (employee_id,)).fetchone()
        if active is None:
            raise ValidationError(["在籍中の社員を選んでください。"])
        new_employee_id = employee_id

    # 「状態が確認したときのままなら」という条件付きで更新する。
    # 確認と保存の間にほかの人が状態を変えていたら、1件も更新されない
    cur = conn.execute(
        "UPDATE devices SET status = ?, employee_id = ?, updated_at = datetime('now', 'localtime')"
        " WHERE id = ? AND status = ?",
        (new_status, new_employee_id, current["id"], current["status"]),
    )
    if cur.rowcount == 0:
        raise ValidationError(["ほかの人が先にこの機器を変更しました。画面を読み込み直してください。"])
    if action in ACTIONS_RELEASING_LICENSES:
        # 廃棄・紛失したPCに割り当てていたライセンスを解除し、残数に戻す
        conn.execute(
            "UPDATE license_assignments SET released_on = ? WHERE device_id = ? AND released_on IS NULL",
            (datetime.date.today().isoformat(), current["id"]),
        )
    return new_status