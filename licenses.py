"""ソフトウェアライセンスのデータを扱うモジュール。

割当数・残数は保存せず、割当の記録（license_assignments）から毎回計算する（設計書 1章）。
"""
import datetime

import audit
import db
from devices import ValidationError

LICENSE_TYPE_LABELS = {
    "user": "人単位",
    "device": "PC単位",
}
# 操作履歴で「どの項目が変わったか」を書くときの項目名
FIELD_LABELS = {"name": "ライセンス名", "vendor": "提供元", "quantity": "保有数",
                "expires_on": "有効期限", "cost": "費用", "note": "備考"}

# 有効期限まで何日を切ったら「期限間近」として警告するか（要件定義書 F-34）
EXPIRY_WARNING_DAYS = 30


def _today(today):
    return today or datetime.date.today()


def expiry_status(expires_on, today=None):
    """有効期限の状態を返す：期限切れなら "expired"、30日以内なら "soon"、それ以外は None。"""
    if not expires_on:
        return None  # 期限なし（買い切り）
    days = (datetime.date.fromisoformat(expires_on) - _today(today)).days
    if days < 0:
        return "expired"
    if days <= EXPIRY_WARNING_DAYS:
        return "soon"
    return None


def list_licenses_of_employee(employee_id, db_path=db.DB_PATH):
    """社員本人に割り当てられているライセンスを返す（解除済みは含めない）。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT l.name, l.vendor, l.expires_on, a.assigned_on"
            " FROM license_assignments a"
            " JOIN licenses l ON l.id = a.license_id"
            " WHERE a.employee_id = ? AND a.released_on IS NULL"
            " ORDER BY l.name",
            (employee_id,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "ライセンス名": r["name"],
            "提供元": r["vendor"] or "",
            "有効期限": r["expires_on"] or "期限なし",
            "割当日": r["assigned_on"],
        }
        for r in rows
    ]


def list_device_licenses(device_id, db_path=db.DB_PATH):
    """PCに割り当てられているライセンス名の一覧（解除済みは含めない）。機器の詳細表示に使う。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT l.name FROM license_assignments a JOIN licenses l ON l.id = a.license_id"
            " WHERE a.device_id = ? AND a.released_on IS NULL ORDER BY l.name",
            (device_id,),
        ).fetchall()
    finally:
        conn.close()
    return [r["name"] for r in rows]


def list_licenses(today=None, db_path=db.DB_PATH):
    """ライセンスの一覧を、割当数・残数・注意点つきで返す（F-33）。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT l.*, COUNT(a.id) AS assigned"
            " FROM licenses l"
            " LEFT JOIN license_assignments a ON a.license_id = l.id AND a.released_on IS NULL"
            " GROUP BY l.id ORDER BY l.name"
        ).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        remaining = r["quantity"] - r["assigned"]
        status = expiry_status(r["expires_on"], today)
        notes = []
        if status == "expired":
            notes.append("期限切れ")
        elif status == "soon":
            days = (datetime.date.fromisoformat(r["expires_on"]) - _today(today)).days
            notes.append(f"期限間近（あと{days}日）")
        if remaining == 0:
            notes.append("残数なし")
        result.append({
            "id": r["id"],
            "ライセンス名": r["name"],
            "提供元": r["vendor"] or "",
            "割当の単位": LICENSE_TYPE_LABELS[r["license_type"]],
            "保有数": r["quantity"],
            "割当数": r["assigned"],
            "残数": remaining,
            "有効期限": r["expires_on"] or "期限なし",
            "注意": "・".join(notes),
        })
    return result


def get_license(license_id, db_path=db.DB_PATH):
    """ライセンス1件の情報を返す（見つからなければ None）。有効期限は date 型にする。"""
    conn = db.get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM licenses WHERE id = ?", (license_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    lic = dict(row)
    if lic["expires_on"]:
        lic["expires_on"] = datetime.date.fromisoformat(lic["expires_on"])
    return lic


def list_assignments(license_id, db_path=db.DB_PATH):
    """ライセンスの割当先（解除済みは除く）を返す。PC単位なら、そのPCの利用者も添える。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT a.id, a.assigned_on, e.employee_code, e.name,"
            "       d.asset_no, du.name AS device_user"
            " FROM license_assignments a"
            " LEFT JOIN employees e ON e.id = a.employee_id"
            " LEFT JOIN devices d ON d.id = a.device_id"
            " LEFT JOIN employees du ON du.id = d.employee_id"
            " WHERE a.license_id = ? AND a.released_on IS NULL"
            " ORDER BY a.assigned_on, a.id",
            (license_id,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        if r["employee_code"]:
            target = f'{r["employee_code"]} {r["name"]}'
        else:
            target = f'{r["asset_no"]}（利用者：{r["device_user"] or "なし"}）'
        result.append({"id": r["id"], "割当先": target, "割当日": r["assigned_on"]})
    return result


def list_assignable_pcs(db_path=db.DB_PATH):
    """PC単位のライセンスを割り当てられるPC（廃棄済を除く）を、{id: 表示名} で返す。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT d.id, d.asset_no, e.name FROM devices d"
            " LEFT JOIN employees e ON e.id = d.employee_id"
            " WHERE d.category = 'pc' AND d.status <> 'disposed' ORDER BY d.asset_no"
        ).fetchall()
    finally:
        conn.close()
    return {r["id"]: f'{r["asset_no"]}（利用者：{r["name"] or "なし"}）' for r in rows}


def _clean_license_input(data):
    """入力をそろえる：文字の前後の空白を取り、空欄は None にする。"""
    values = {}
    for key in ("name", "vendor", "license_type", "quantity", "expires_on", "cost", "note"):
        value = data.get(key)
        if isinstance(value, str):
            value = value.strip() or None
        values[key] = value
    return values


def _validate_license(values, conn, exclude_id=None, assigned=0):
    """ライセンスの入力チェック。誤りがあれば、画面に出すメッセージの一覧を返す。"""
    errors = []
    if not values["name"]:
        errors.append("ライセンス名を入力してください。")
    else:
        sql, params = "SELECT 1 FROM licenses WHERE name = ?", [values["name"]]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        if conn.execute(sql, params).fetchone():
            errors.append(f"ライセンス名「{values['name']}」はすでに登録されています。")
    if values["license_type"] not in LICENSE_TYPE_LABELS:
        errors.append("割当の単位を選んでください。")

    quantity = values["quantity"]
    if quantity is None or int(quantity) != quantity or quantity < 1:
        errors.append("保有数は1以上の整数で入力してください。")
    elif quantity < assigned:
        errors.append(f"保有数は、今の割当数（{assigned}）より少なくできません。先に割当を解除してください。")

    cost = values["cost"]
    if cost is not None and (int(cost) != cost or cost < 0):
        errors.append("費用は0以上の整数（円）で入力してください。")
    return errors


def _to_db_values(values):
    """保存用に型をそろえる（数は整数、日付は「YYYY-MM-DD」の文字列）。"""
    values = dict(values)
    values["quantity"] = int(values["quantity"])
    if values["cost"] is not None:
        values["cost"] = int(values["cost"])
    if isinstance(values["expires_on"], datetime.date):
        values["expires_on"] = values["expires_on"].isoformat()
    return values


def register_license(data, db_path=db.DB_PATH, actor_id=None):
    """ライセンスを登録し（F-31）、id を返す。入力に誤りがあれば ValidationError を出す。"""
    values = _clean_license_input(data)
    conn = db.get_connection(db_path)
    try:
        errors = _validate_license(values, conn)
        if errors:
            raise ValidationError(errors)
        values = _to_db_values(values)
        with conn:
            cur = conn.execute(
                "INSERT INTO licenses (name, vendor, license_type, quantity, expires_on, cost, note)"
                " VALUES (:name, :vendor, :license_type, :quantity, :expires_on, :cost, :note)",
                values,
            )
            audit.record(conn, actor_id, "license", cur.lastrowid, values["name"], "create",
                         f'{LICENSE_TYPE_LABELS[values["license_type"]]}、保有数 {values["quantity"]}、'
                         f'有効期限 {values["expires_on"] or "なし"}')
        return cur.lastrowid
    finally:
        conn.close()


def update_license(license_id, data, db_path=db.DB_PATH, actor_id=None):
    """保有数・有効期限などを変更する。割当の単位は、割当先の種類が変わってしまうため変更しない。"""
    conn = db.get_connection(db_path)
    try:
        current = conn.execute(
            "SELECT l.*, COUNT(a.id) AS assigned FROM licenses l"
            " LEFT JOIN license_assignments a ON a.license_id = l.id AND a.released_on IS NULL"
            " WHERE l.id = ? GROUP BY l.id",
            (license_id,),
        ).fetchone()
        if current is None:
            raise ValidationError(["ライセンスが見つかりません。"])
        values = _clean_license_input({**data, "license_type": current["license_type"]})
        errors = _validate_license(values, conn, exclude_id=license_id, assigned=current["assigned"])
        if errors:
            raise ValidationError(errors)
        values = _to_db_values(values)
        changes = audit.describe_changes(dict(current), values, FIELD_LABELS)
        with conn:
            conn.execute(
                "UPDATE licenses SET name = :name, vendor = :vendor, quantity = :quantity,"
                " expires_on = :expires_on, cost = :cost, note = :note WHERE id = :id",
                {**values, "id": license_id},
            )
            if changes:
                audit.record(conn, actor_id, "license", license_id, values["name"], "update", changes)
    finally:
        conn.close()


def _target_label(conn, employee_id, device_id):
    """割当先の表示名（社員なら「E0005 伊藤 さくら」、PCなら資産番号）。"""
    if employee_id is not None:
        r = conn.execute("SELECT employee_code, name FROM employees WHERE id = ?", (employee_id,)).fetchone()
        return f'{r["employee_code"]} {r["name"]}'
    return conn.execute("SELECT asset_no FROM devices WHERE id = ?", (device_id,)).fetchone()["asset_no"]


def assign_license(license_id, employee_id=None, device_id=None, today=None, db_path=db.DB_PATH, actor_id=None):
    """ライセンスを社員（人単位）またはPC（PC単位）に割り当てる（F-32）。"""
    conn = db.get_connection(db_path)
    try:
        lic = conn.execute("SELECT * FROM licenses WHERE id = ?", (license_id,)).fetchone()
        if lic is None:
            raise ValidationError(["ライセンスが見つかりません。"])
        if expiry_status(lic["expires_on"], today) == "expired":
            raise ValidationError(["有効期限が切れたライセンスは割り当てられません。"])

        if lic["license_type"] == "user":
            ok = employee_id is not None and conn.execute(
                "SELECT 1 FROM employees WHERE id = ? AND is_active = 1", (employee_id,)
            ).fetchone()
            if not ok:
                raise ValidationError(["割り当てる在籍中の社員を選んでください。"])
            device_id = None
            target_sql, target_id = "employee_id = ?", employee_id
        else:
            ok = device_id is not None and conn.execute(
                "SELECT 1 FROM devices WHERE id = ? AND category = 'pc' AND status <> 'disposed'", (device_id,)
            ).fetchone()
            if not ok:
                raise ValidationError(["割り当てるPC（廃棄済を除く）を選んでください。"])
            employee_id = None
            target_sql, target_id = "device_id = ?", device_id

        already = conn.execute(
            f"SELECT 1 FROM license_assignments WHERE license_id = ? AND {target_sql} AND released_on IS NULL",
            (license_id, target_id),
        ).fetchone()
        if already:
            raise ValidationError(["すでにこのライセンスを割り当てています。"])

        with conn:
            conn.execute(
                "INSERT INTO license_assignments (license_id, employee_id, device_id, assigned_on)"
                " VALUES (?, ?, ?, ?)",
                (license_id, employee_id, device_id, _today(today).isoformat()),
            )
            # 追加したあとに数え直す。同時に割り当てた人がいて保有数を超えたら、この追加を取り消す
            assigned = conn.execute(
                "SELECT COUNT(*) FROM license_assignments WHERE license_id = ? AND released_on IS NULL",
                (license_id,),
            ).fetchone()[0]
            if assigned > lic["quantity"]:
                raise ValidationError(["残数がないため割り当てられません。"])  # with の中で例外 → 取り消し
            audit.record(conn, actor_id, "license", license_id, lic["name"], "assign",
                         f"割当先：{_target_label(conn, employee_id, device_id)}（残数 {lic['quantity'] - assigned}）")
    finally:
        conn.close()


def release_license(assignment_id, today=None, db_path=db.DB_PATH, actor_id=None):
    """ライセンスの割当を解除する。記録は消さず、解除日を入れる（残数に戻る）。"""
    conn = db.get_connection(db_path)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE license_assignments SET released_on = ? WHERE id = ? AND released_on IS NULL",
                (_today(today).isoformat(), assignment_id),
            )
            if cur.rowcount == 0:
                raise ValidationError(["この割当はすでに解除されています。"])
            a = conn.execute(
                "SELECT a.license_id, a.employee_id, a.device_id, l.name FROM license_assignments a"
                " JOIN licenses l ON l.id = a.license_id WHERE a.id = ?",
                (assignment_id,),
            ).fetchone()
            audit.record(conn, actor_id, "license", a["license_id"], a["name"], "release",
                         f'割当先：{_target_label(conn, a["employee_id"], a["device_id"])}')
    finally:
        conn.close()
