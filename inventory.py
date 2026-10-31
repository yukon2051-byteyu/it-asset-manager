"""棚卸（F-41〜F-43）：開始・回答・差異の対応・完了。

開始した時点の機器と利用者を「明細」として固定し、その明細に回答してもらう。
棚卸の途中で機器の利用者が変わっても、開始した時点の利用者が回答する。
"""
import datetime
import sqlite3

import audit
import db
import devices
from devices import ValidationError

ANSWER_LABELS = {"present": "ある", "absent": "ない"}
STATUS_LABELS = {"open": "実施中", "completed": "完了"}
# 棚卸の対象にする機器の状態。修理中は業者の手元にあり、紛失・廃棄済は現物がないので対象外
TARGET_STATUSES = ("in_use", "lent", "in_stock")


def _today(today):
    return today or datetime.date.today()


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_open_inventory(db_path=db.DB_PATH):
    """実施中の棚卸を返す（なければ None）。"""
    conn = db.get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM inventories WHERE status = 'open'").fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def start_inventory(name, due_date, actor_id=None, today=None, db_path=db.DB_PATH):
    """棚卸を開始し（F-41）、(棚卸の id, 対象の件数) を返す。その時点の機器を明細として固定する。"""
    name = (name or "").strip()
    errors = []
    if not name:
        errors.append("棚卸の名前を入力してください。")
    if due_date is None:
        errors.append("回答期限を入力してください。")
    elif due_date < _today(today):
        errors.append("回答期限に過去の日付は指定できません。")
    if get_open_inventory(db_path):
        errors.append("実施中の棚卸があります。先に完了してください。")
    if errors:
        raise ValidationError(errors)

    conn = db.get_connection(db_path)
    try:
        with conn:
            try:
                cur = conn.execute(
                    "INSERT INTO inventories (name, due_date, started_by) VALUES (?, ?, ?)",
                    (name, due_date.isoformat(), actor_id),
                )
            except sqlite3.IntegrityError:
                # 上の確認のあとに、ほかの管理者が先に開始した場合（データベースのルールで止まる）
                raise ValidationError(["実施中の棚卸があります。先に完了してください。"])
            inventory_id = cur.lastrowid
            count = conn.execute(
                "INSERT INTO inventory_items (inventory_id, device_id, employee_id, status_at_start)"
                f" SELECT ?, id, employee_id, status FROM devices WHERE status IN {TARGET_STATUSES}",
                (inventory_id,),
            ).rowcount
            if count == 0:
                raise ValidationError(["棚卸の対象になる機器がありません。"])  # with の中なので開始も取り消される
            audit.record(conn, actor_id, "inventory", inventory_id, name, "start",
                         f"対象 {count} 件、回答期限 {due_date.isoformat()}")
        return inventory_id, count
    finally:
        conn.close()


_ITEM_SQL = (
    "SELECT i.*, d.asset_no, d.category, d.maker, d.model, d.location,"
    "       e.employee_code, e.name AS employee_name, e.department, e.email,"
    "       ab.name AS answered_by_name"
    " FROM inventory_items i"
    " JOIN devices d ON d.id = i.device_id"
    " LEFT JOIN employees e ON e.id = i.employee_id"
    " LEFT JOIN employees ab ON ab.id = i.answered_by"
)


def list_items(inventory_id, view=None, employee_id=None, db_path=db.DB_PATH):
    """棚卸の明細を返す（F-43）。

    view="unanswered" で未回答、view="absent" で「ない」と回答したもの（差異）だけにする。
    employee_id を渡すと、その社員が回答する明細だけにする。
    """
    sql = _ITEM_SQL + " WHERE i.inventory_id = ?"
    params = [inventory_id]
    if view == "unanswered":
        sql += " AND i.answer IS NULL"
    elif view == "absent":
        sql += " AND i.answer = 'absent'"
    if employee_id is not None:
        sql += " AND i.employee_id = ?"
        params.append(employee_id)
    sql += " ORDER BY d.asset_no"
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r["id"],
            "資産番号": r["asset_no"],
            "種別": devices.CATEGORY_LABELS[r["category"]],
            "機器": f'{r["maker"]} {r["model"]}',
            "開始時の状態": devices.STATUS_LABELS[r["status_at_start"]],
            "回答する人": f'{r["employee_code"]} {r["employee_name"]}' if r["employee_code"] else "IT管理者（在庫）",
            "部署": r["department"] or "",
            "メールアドレス": r["email"] or "",
            "回答": ANSWER_LABELS.get(r["answer"], "未回答"),
            "状況": r["comment"] or "",
            "回答者": r["answered_by_name"] or "",
            "回答日時": r["answered_at"] or "",
            "対応メモ": r["resolution"] or "",
            "answer": r["answer"],
            "employee_id": r["employee_id"],
        }
        for r in rows
    ]


def progress(inventory_id, db_path=db.DB_PATH):
    """進み具合：対象・回答済み・未回答・「ない」・対応メモがまだの「ない」の件数。"""
    conn = db.get_connection(db_path)
    try:
        r = conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(answer IS NOT NULL) AS answered,"
            " SUM(answer IS NULL) AS unanswered,"
            " SUM(answer = 'absent') AS absent,"
            " SUM(answer = 'absent' AND resolution IS NULL) AS unresolved"
            " FROM inventory_items WHERE inventory_id = ?",
            (inventory_id,),
        ).fetchone()
    finally:
        conn.close()
    return {key: r[key] or 0 for key in ("total", "answered", "unanswered", "absent", "unresolved")}


def answer_items(answers, actor_id, as_admin=False, db_path=db.DB_PATH):
    """明細に「ある／ない」を回答する（F-42）。answers は (明細の id, "present"/"absent", 状況) の一覧。

    一般社員は自分の明細だけに回答できる。IT管理者（as_admin=True）は、在庫の確認と代理の回答ができる。
    1件でも誤りがあれば、どれも保存しない。
    """
    if not answers:
        raise ValidationError(["回答する機器を選んでください。"])
    conn = db.get_connection(db_path)
    try:
        errors, rows = [], []
        for item_id, answer, comment in answers:
            comment = (comment or "").strip() or None
            item = conn.execute(
                "SELECT i.*, d.asset_no, v.status AS inventory_status, v.name AS inventory_name"
                " FROM inventory_items i JOIN devices d ON d.id = i.device_id"
                " JOIN inventories v ON v.id = i.inventory_id WHERE i.id = ?",
                (item_id,),
            ).fetchone()
            if item is None:
                errors.append("棚卸の明細が見つかりません。")
                continue
            label = item["asset_no"]
            if item["inventory_status"] != "open":
                errors.append(f"{label}：この棚卸はすでに完了しています。")
            elif not as_admin and item["employee_id"] != actor_id:
                errors.append(f"{label}：あなたが回答する機器ではありません。")
            elif answer not in ANSWER_LABELS:
                errors.append(f"{label}：「ある」か「ない」を選んでください。")
            elif answer == "absent" and not comment:
                errors.append(f"{label}：「ない」のときは、状況を入力してください（例：自宅にある、所在がわからない）。")
            else:
                rows.append((item, answer, comment))
        if errors:
            raise ValidationError(errors)

        with conn:
            for item, answer, comment in rows:
                conn.execute(
                    "UPDATE inventory_items SET answer = ?, comment = ?, answered_by = ?, answered_at = ?,"
                    " resolution = CASE WHEN ? = 'present' THEN NULL ELSE resolution END WHERE id = ?",
                    (answer, comment, actor_id, _now(), answer, item["id"]),
                )
                proxy = "（代理）" if as_admin and item["employee_id"] not in (None, actor_id) else ""
                audit.record(conn, actor_id, "inventory", item["inventory_id"], item["inventory_name"], "answer",
                             f'{item["asset_no"]}：{ANSWER_LABELS[answer]}{proxy}' + (f"、{comment}" if comment else ""))
        return len(rows)
    finally:
        conn.close()


def resolve_item(item_id, resolution, actor_id=None, db_path=db.DB_PATH):
    """「ない」と回答された機器に、IT管理者の対応メモを残す（例：紛失として処理した、後日返却された）。"""
    resolution = (resolution or "").strip()
    if not resolution:
        raise ValidationError(["対応メモを入力してください。"])
    conn = db.get_connection(db_path)
    try:
        item = conn.execute(
            "SELECT i.*, d.asset_no, v.status AS inventory_status, v.name AS inventory_name"
            " FROM inventory_items i JOIN devices d ON d.id = i.device_id"
            " JOIN inventories v ON v.id = i.inventory_id WHERE i.id = ?",
            (item_id,),
        ).fetchone()
        if item is None:
            raise ValidationError(["棚卸の明細が見つかりません。"])
        if item["inventory_status"] != "open":
            raise ValidationError(["この棚卸はすでに完了しています。"])
        if item["answer"] != "absent":
            raise ValidationError(["対応メモは「ない」と回答された機器にだけ書けます。"])
        with conn:
            conn.execute("UPDATE inventory_items SET resolution = ? WHERE id = ?", (resolution, item_id))
            audit.record(conn, actor_id, "inventory", item["inventory_id"], item["inventory_name"], "resolve",
                         f'{item["asset_no"]}：{resolution}')
    finally:
        conn.close()


def complete_inventory(inventory_id, actor_id=None, db_path=db.DB_PATH):
    """棚卸を完了する。全件の回答がそろい、「ない」のすべてに対応メモがあることが条件。"""
    conn = db.get_connection(db_path)
    try:
        inv = conn.execute("SELECT * FROM inventories WHERE id = ?", (inventory_id,)).fetchone()
        if inv is None:
            raise ValidationError(["棚卸が見つかりません。"])
        if inv["status"] != "open":
            raise ValidationError(["この棚卸はすでに完了しています。"])
        p = progress(inventory_id, db_path)
        errors = []
        if p["unanswered"]:
            errors.append(f"未回答が {p['unanswered']} 件あります。回答を集めるか、代理で回答してください。")
        if p["unresolved"]:
            errors.append(f"「ない」と回答された機器のうち、対応メモがないものが {p['unresolved']} 件あります。")
        if errors:
            raise ValidationError(errors)
        with conn:
            conn.execute("UPDATE inventories SET status = 'completed', completed_at = ? WHERE id = ?",
                         (_now(), inventory_id))
            audit.record(conn, actor_id, "inventory", inventory_id, inv["name"], "complete",
                         f"対象 {p['total']} 件、「ない」{p['absent']} 件")
    finally:
        conn.close()


def list_inventories(db_path=db.DB_PATH):
    """これまでの棚卸の一覧（新しい順）。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM inventories ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        p = progress(r["id"], db_path)
        result.append({
            "id": r["id"],
            "名前": r["name"],
            "状態": STATUS_LABELS[r["status"]],
            "開始日時": r["started_at"],
            "回答期限": r["due_date"],
            "完了日時": r["completed_at"] or "",
            "対象": p["total"],
            "ない": p["absent"],
        })
    return result
