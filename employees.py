"""社員のデータを扱うモジュール。利用者管理（F-54）：登録・変更・退職・再有効化・パスワード再発行。"""
import datetime
import re
import unicodedata

import audit
import auth
import db
from devices import ValidationError

ROLE_LABELS = {"user": "一般社員", "admin": "IT管理者"}
# 操作履歴で「どの項目が変わったか」を書くときの項目名
FIELD_LABELS = {"name": "氏名", "department": "部署", "email": "メールアドレス", "role": "権限"}


def list_employees(db_path=db.DB_PATH):
    """在籍中の社員の一覧を、社員番号順に返す。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, employee_code, name, department FROM employees"
            " WHERE is_active = 1 ORDER BY employee_code"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_departments(db_path=db.DB_PATH):
    """在籍中の社員がいる部署の一覧を返す（資産一覧の絞り込みに使う）。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT department FROM employees WHERE is_active = 1 ORDER BY department"
        ).fetchall()
    finally:
        conn.close()
    return [r["department"] for r in rows]


def list_all_employees(db_path=db.DB_PATH):
    """退職者も含めた全社員の一覧を、使っている機器の台数つきで返す（利用者管理の画面用）。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT e.*, COUNT(d.id) AS devices FROM employees e"
            " LEFT JOIN devices d ON d.employee_id = e.id AND d.status IN ('in_use', 'lent')"
            " GROUP BY e.id ORDER BY e.employee_code"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "社員番号": r["employee_code"],
            "氏名": r["name"],
            "部署": r["department"],
            "メールアドレス": r["email"],
            "権限": ROLE_LABELS[r["role"]],
            "在籍": "在籍" if r["is_active"] else "退職",
            "パスワード": "発行済" if auth.is_issued(r["password_hash"]) else "未発行",
            "利用中の機器": r["devices"],
        }
        for r in rows
    ]


def get_employee(employee_id, db_path=db.DB_PATH):
    """社員1人の情報を返す（見つからなければ None）。パスワードのハッシュは返さない。"""
    conn = db.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, employee_code, name, department, email, role, is_active FROM employees WHERE id = ?",
            (employee_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# 全角を半角にそろえる項目。氏名・部署は「（旧姓）」の全角かっこなどが変わらないよう、そろえない
NORMALIZED_FIELDS = {"employee_code", "email"}


def _clean(data):
    values = {}
    for key in ("employee_code", "name", "department", "email", "role"):
        value = data.get(key)
        if isinstance(value, str):
            if key in NORMALIZED_FIELDS:
                value = unicodedata.normalize("NFKC", value)
            value = value.strip() or None
        values[key] = value
    return values


def _validate(values, conn, exclude_id=None):
    errors = []
    code = values.get("employee_code")
    if exclude_id is None:  # 社員番号は登録のときだけ入力する
        if not code:
            errors.append("社員番号を入力してください。")
        elif not re.fullmatch(r"[A-Za-z0-9]{1,20}", code):
            errors.append("社員番号は半角英数字（20文字以内）で入力してください。")
        elif conn.execute("SELECT 1 FROM employees WHERE employee_code = ?", (code,)).fetchone():
            errors.append(f"社員番号「{code}」はすでに登録されています。")
    if not values["name"]:
        errors.append("氏名を入力してください。")
    if not values["department"]:
        errors.append("部署を入力してください。")

    email = values["email"]
    if not email:
        errors.append("メールアドレスを入力してください。")
    elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append("メールアドレスの形式が正しくありません（例：yamada@example.com）。")
    else:
        sql, params = "SELECT 1 FROM employees WHERE email = ?", [email]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        if conn.execute(sql, params).fetchone():
            errors.append(f"メールアドレス「{email}」はすでに登録されています。")

    if values["role"] not in ROLE_LABELS:
        errors.append("権限を選んでください。")
    return errors


def _other_active_admins(conn, employee_id):
    return conn.execute(
        "SELECT COUNT(*) FROM employees WHERE role = 'admin' AND is_active = 1 AND id <> ?", (employee_id,)
    ).fetchone()[0]


def _get(conn, employee_id):
    row = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if row is None:
        raise ValidationError(["社員が見つかりません。"])
    return row


def _label(row):
    return f'{row["employee_code"]} {row["name"]}'


def register_employee(data, db_path=db.DB_PATH, actor_id=None):
    """社員を登録し、(id, 初期パスワード) を返す。初期パスワードは保存せず、ここで一度だけ返す。"""
    values = _clean(data)
    conn = db.get_connection(db_path)
    try:
        errors = _validate(values, conn)
        if errors:
            raise ValidationError(errors)
        password = auth.make_password()
        with conn:
            cur = conn.execute(
                "INSERT INTO employees (employee_code, name, department, email, role, password_hash)"
                " VALUES (:employee_code, :name, :department, :email, :role, :password_hash)",
                {**values, "password_hash": auth.hash_password(password)},
            )
            audit.record(conn, actor_id, "employee", cur.lastrowid, f'{values["employee_code"]} {values["name"]}',
                         "create", f'{values["department"]}、{ROLE_LABELS[values["role"]]}')  # パスワードは記録しない
        return cur.lastrowid, password
    finally:
        conn.close()


def update_employee(employee_id, data, db_path=db.DB_PATH, actor_id=None):
    """氏名・部署・メールアドレス・権限を変更する。社員番号は変更しない。"""
    values = _clean(data)
    del values["employee_code"]
    conn = db.get_connection(db_path)
    try:
        current = _get(conn, employee_id)
        errors = _validate(values, conn, exclude_id=employee_id)
        if values["role"] == "user" and current["role"] == "admin":
            # 権限を下げると、管理者が締め出される事故が起きうるので止める
            if employee_id == actor_id:
                errors.append("自分自身の権限は下げられません。ほかのIT管理者に依頼してください。")
            elif current["is_active"] and _other_active_admins(conn, employee_id) == 0:
                errors.append("IT管理者が1人もいなくなるため、権限を下げられません。")
        if errors:
            raise ValidationError(errors)

        changes = audit.describe_changes(
            {**dict(current), "role": ROLE_LABELS[current["role"]]},
            {**values, "role": ROLE_LABELS[values["role"]]},
            FIELD_LABELS,
        )
        with conn:
            conn.execute(
                "UPDATE employees SET name = :name, department = :department, email = :email, role = :role"
                " WHERE id = :id",
                {**values, "id": employee_id},
            )
            if changes:
                audit.record(conn, actor_id, "employee", employee_id, _label(current), "update", changes)
    finally:
        conn.close()


def deactivate_employee(employee_id, db_path=db.DB_PATH, actor_id=None, today=None):
    """退職処理：ログインできなくする（データは消さない）。本人に割り当てたライセンスは解除する。"""
    conn = db.get_connection(db_path)
    try:
        current = _get(conn, employee_id)
        if not current["is_active"]:
            raise ValidationError(["この社員はすでに退職処理されています。"])
        if employee_id == actor_id:
            raise ValidationError(["自分自身は退職処理できません。ほかのIT管理者に依頼してください。"])
        if current["role"] == "admin" and _other_active_admins(conn, employee_id) == 0:
            raise ValidationError(["IT管理者が1人もいなくなるため、退職処理できません。"])
        in_use = [r["asset_no"] for r in conn.execute(
            "SELECT asset_no FROM devices WHERE employee_id = ? AND status IN ('in_use', 'lent') ORDER BY asset_no",
            (employee_id,),
        )]
        if in_use:
            # 機器は現物の回収が必要なので、自動では外さず、先に返却処理をしてもらう
            raise ValidationError([f"使用中の機器（{'、'.join(in_use)}）があります。先に返却処理をしてください。"])

        with conn:
            conn.execute("UPDATE employees SET is_active = 0 WHERE id = ?", (employee_id,))
            released = conn.execute(
                "UPDATE license_assignments SET released_on = ? WHERE employee_id = ? AND released_on IS NULL",
                ((today or datetime.date.today()).isoformat(), employee_id),
            ).rowcount
            audit.record(conn, actor_id, "employee", employee_id, _label(current), "deactivate",
                         f"ライセンス {released} 件を解除" if released else "")
        return released
    finally:
        conn.close()


def reactivate_employee(employee_id, db_path=db.DB_PATH, actor_id=None):
    """退職処理を取り消し、もう一度ログインできるようにする（解除したライセンスは戻さない）。"""
    conn = db.get_connection(db_path)
    try:
        current = _get(conn, employee_id)
        if current["is_active"]:
            raise ValidationError(["この社員は在籍中です。"])
        with conn:
            conn.execute("UPDATE employees SET is_active = 1 WHERE id = ?", (employee_id,))
            audit.record(conn, actor_id, "employee", employee_id, _label(current), "reactivate")
    finally:
        conn.close()


def reset_password(employee_id, db_path=db.DB_PATH, actor_id=None):
    """新しいパスワードを発行して返す。古いパスワードは使えなくなる。"""
    conn = db.get_connection(db_path)
    try:
        current = _get(conn, employee_id)
        if not current["is_active"]:
            raise ValidationError(["退職した社員のパスワードは再発行できません。"])
        password = auth.make_password()
        with conn:
            conn.execute("UPDATE employees SET password_hash = ? WHERE id = ?",
                         (auth.hash_password(password), employee_id))
            audit.record(conn, actor_id, "employee", employee_id, _label(current), "reset_password")
        return password
    finally:
        conn.close()
