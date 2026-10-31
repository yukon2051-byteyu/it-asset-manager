"""機器の申請（貸出・返却・故障・紛失）と承認を扱うモジュール（F-21〜F-26）。

申請を承認すると、機器の状態も同じトランザクションで変える。片方だけ変わることはない。
"""
import datetime

import audit
import db
import devices
from devices import ValidationError

TYPE_LABELS = {"lend": "貸出", "return": "返却", "failure": "故障", "loss": "紛失"}
STATUS_LABELS = {"pending": "承認待ち", "approved": "承認済", "rejected": "却下", "on_hold": "保留"}

# 承認したときに機器に行う操作（devices.TRANSITIONS の操作名）
APPROVE_ACTIONS = {"lend": "lend", "return": "return", "failure": "failure", "loss": "loss"}
# まだ処理が終わっていない申請の状態
OPEN_STATUSES = ("pending", "on_hold")


def request_no(request_id):
    """申請番号（例：R-0001）。保存はせず、申請の id から作る。"""
    return f"R-{request_id:04d}"


def _today(today):
    return today or datetime.date.today()


def list_my_devices(employee_id, db_path=db.DB_PATH):
    """返却・故障・紛失の申請で選べる、自分が使っている機器を {id: 表示名} で返す。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, asset_no, category, maker, model FROM devices"
            " WHERE employee_id = ? AND status IN ('in_use', 'lent') ORDER BY asset_no",
            (employee_id,),
        ).fetchall()
    finally:
        conn.close()
    return {r["id"]: f'{r["asset_no"]}　{devices.CATEGORY_LABELS[r["category"]]} {r["maker"]} {r["model"]}' for r in rows}


def list_stock_devices(category, db_path=db.DB_PATH):
    """貸出の承認で選べる、在庫の機器（指定した種別）を {id: 表示名} で返す。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, asset_no, maker, model, location FROM devices"
            " WHERE category = ? AND status = 'in_stock' ORDER BY asset_no",
            (category,),
        ).fetchall()
    finally:
        conn.close()
    return {r["id"]: f'{r["asset_no"]}　{r["maker"]} {r["model"]}（{r["location"] or "場所未登録"}）' for r in rows}


def _clean(value):
    if isinstance(value, str):
        return value.strip() or None
    return value


def create_request(requester_id, data, today=None, db_path=db.DB_PATH):
    """申請を登録し（F-21〜F-23）、id を返す。入力に誤りがあれば ValidationError を出す。"""
    request_type = data.get("request_type")
    values = {
        "request_type": request_type,
        "requester_id": requester_id,
        "device_id": None,
        "category": None,
        "detail": _clean(data.get("detail")),
        "start_date": None,
        "due_date": None,
        "occurred_on": None,
    }
    errors = []
    conn = db.get_connection(db_path)
    try:
        if request_type not in TYPE_LABELS:
            raise ValidationError(["申請の種類を選んでください。"])

        if request_type == "lend":
            values["category"] = data.get("category")
            start, due = data.get("start_date"), data.get("due_date")
            if values["category"] not in devices.CATEGORY_LABELS:
                errors.append("借りたい機器の種別を選んでください。")
            if not values["detail"]:
                errors.append("利用目的を入力してください。")
            if start is None:
                errors.append("利用開始日を入力してください。")
            elif start < _today(today):
                errors.append("利用開始日に過去の日付は指定できません。")
            if due is None:
                errors.append("返却予定日を入力してください。")
            elif start is not None and due < start:
                errors.append("返却予定日は利用開始日より後の日付にしてください。")
            if start is not None and due is not None:
                values["start_date"], values["due_date"] = start.isoformat(), due.isoformat()
            if values["category"] in devices.CATEGORY_LABELS and conn.execute(
                f"SELECT 1 FROM requests WHERE requester_id = ? AND request_type = 'lend' AND category = ?"
                f" AND status IN {OPEN_STATUSES}",
                (requester_id, values["category"]),
            ).fetchone():
                errors.append("同じ種別の貸出申請が、まだ処理されていません。")
        else:
            # 返却・故障・紛失：自分が使っている機器だけを選べる
            device_id = data.get("device_id")
            if device_id is None or device_id not in list_my_devices(requester_id, db_path):
                errors.append("対象の機器を、自分が使っている機器から選んでください。")
            elif conn.execute(
                f"SELECT 1 FROM requests WHERE device_id = ? AND status IN {OPEN_STATUSES}", (device_id,)
            ).fetchone():
                errors.append("この機器には、まだ処理されていない申請があります。")
            values["device_id"] = device_id

            if request_type in ("failure", "loss"):
                occurred = data.get("occurred_on")
                if not values["detail"]:
                    errors.append("内容を入力してください。")
                if occurred is None:
                    errors.append("発生日を入力してください。")
                elif occurred > _today(today):
                    errors.append("発生日に未来の日付は指定できません。")
                else:
                    values["occurred_on"] = occurred.isoformat()

        if errors:
            raise ValidationError(errors)
        with conn:
            cur = conn.execute(
                "INSERT INTO requests (request_type, requester_id, device_id, category, detail,"
                " start_date, due_date, occurred_on)"
                " VALUES (:request_type, :requester_id, :device_id, :category, :detail,"
                " :start_date, :due_date, :occurred_on)",
                values,
            )
            if request_type == "lend":
                target = f'{devices.CATEGORY_LABELS[values["category"]]}（{values["start_date"]}〜{values["due_date"]}）'
            else:
                target = conn.execute("SELECT asset_no FROM devices WHERE id = ?", (values["device_id"],)).fetchone()[0]
            audit.record(conn, requester_id, "request", cur.lastrowid, request_no(cur.lastrowid), "request",
                         f'{TYPE_LABELS[request_type]}：{target}' + (f'、{values["detail"]}' if values["detail"] else ""))
        return cur.lastrowid
    finally:
        conn.close()


def list_requests(requester_id=None, statuses=None, db_path=db.DB_PATH):
    """申請の一覧を、新しい順に返す。requester_id で申請者を、statuses で状態を絞り込む。"""
    sql = (
        "SELECT r.*, e.name AS requester_name, e.department, v.name AS reviewer_name, d.asset_no"
        " FROM requests r"
        " JOIN employees e ON e.id = r.requester_id"
        " LEFT JOIN employees v ON v.id = r.reviewer_id"
        " LEFT JOIN devices d ON d.id = r.device_id"
    )
    conditions, params = [], []
    if requester_id is not None:
        conditions.append("r.requester_id = ?")
        params.append(requester_id)
    if statuses:
        conditions.append(f"r.status IN ({', '.join('?' for _ in statuses)})")
        params.extend(statuses)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY r.id DESC"

    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        if r["request_type"] == "lend":
            target = devices.CATEGORY_LABELS[r["category"]] + (f'（{r["asset_no"]}）' if r["asset_no"] else "")
            period = f'{r["start_date"]} 〜 {r["due_date"]}'
        else:
            target = r["asset_no"]
            period = f'発生日 {r["occurred_on"]}' if r["occurred_on"] else ""
        result.append({
            "id": r["id"],
            "申請番号": request_no(r["id"]),
            "申請日時": r["created_at"],
            "種類": TYPE_LABELS[r["request_type"]],
            "申請者": f'{r["requester_name"]}（{r["department"]}）',
            "対象": target,
            "内容": r["detail"] or "",
            "期間・発生日": period,
            "状態": STATUS_LABELS[r["status"]],
            "処理者": r["reviewer_name"] or "",
            "理由": r["comment"] or "",
            "request_type": r["request_type"],
            "category": r["category"],
        })
    return result


def _get_open_request(conn, request_id):
    row = conn.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        raise ValidationError(["申請が見つかりません。"])
    if row["status"] not in OPEN_STATUSES:
        raise ValidationError([f"この申請はすでに{STATUS_LABELS[row['status']]}です。"])
    return row


def _finish(conn, request, new_status, reviewer_id, comment=None, device_id=None):
    """申請の状態を変える。処理中のままのときだけ更新し、ほかの人が先に処理していたら止める。"""
    cur = conn.execute(
        "UPDATE requests SET status = ?, reviewer_id = ?, comment = ?, device_id = COALESCE(?, device_id),"
        " processed_at = datetime('now', 'localtime')"
        f" WHERE id = ? AND status IN {OPEN_STATUSES}",
        (new_status, reviewer_id, comment, device_id, request["id"]),
    )
    if cur.rowcount == 0:
        raise ValidationError(["ほかの人が先にこの申請を処理しました。画面を読み込み直してください。"])


def approve(request_id, reviewer_id, device_id=None, db_path=db.DB_PATH):
    """申請を承認し（F-24）、機器の状態を変える。貸出のときは、渡す在庫の機器を device_id で指定する。"""
    conn = db.get_connection(db_path)
    try:
        with conn:  # 申請と機器の更新を1つのまとまりにする。どちらかが失敗したら両方取り消す
            request = _get_open_request(conn, request_id)
            if request["request_type"] == "lend":
                if device_id is None:
                    raise ValidationError(["貸し出す機器を選んでください。"])
                device = conn.execute("SELECT asset_no, category FROM devices WHERE id = ?", (device_id,)).fetchone()
                if device is None or device["category"] != request["category"]:
                    raise ValidationError(["申請された種別の機器を選んでください。"])
                employee_id = request["requester_id"]
            else:
                device = conn.execute(
                    "SELECT asset_no, employee_id FROM devices WHERE id = ?", (request["device_id"],)
                ).fetchone()
                if device["employee_id"] != request["requester_id"]:
                    raise ValidationError(["この機器は、もう申請者の利用ではありません。却下してください。"])
                device_id, employee_id = None, None
            devices.apply_status_change(conn, device["asset_no"], APPROVE_ACTIONS[request["request_type"]],
                                        employee_id, actor_id=reviewer_id)
            _finish(conn, request, "approved", reviewer_id, device_id=device_id)
            audit.record(conn, reviewer_id, "request", request_id, request_no(request_id), "approve",
                         f'{TYPE_LABELS[request["request_type"]]}：{device["asset_no"]}')
    finally:
        conn.close()


def reject(request_id, reviewer_id, reason, db_path=db.DB_PATH):
    """申請を却下する。申請者が理由を確認できるよう、理由は必須にする。"""
    reason = _clean(reason)
    if not reason:
        raise ValidationError(["却下の理由を入力してください。"])
    conn = db.get_connection(db_path)
    try:
        with conn:
            request = _get_open_request(conn, request_id)
            _finish(conn, request, "rejected", reviewer_id, reason)
            audit.record(conn, reviewer_id, "request", request_id, request_no(request_id), "reject",
                         f'{TYPE_LABELS[request["request_type"]]}、理由：{reason}')
    finally:
        conn.close()


def hold(request_id, reviewer_id, reason, db_path=db.DB_PATH):
    """貸出の申請を保留にする（在庫がないときなど）。保留した申請は、あとで承認・却下できる。"""
    reason = _clean(reason)
    if not reason:
        raise ValidationError(["保留の理由を入力してください。"])
    conn = db.get_connection(db_path)
    try:
        with conn:
            request = _get_open_request(conn, request_id)
            if request["request_type"] != "lend":
                raise ValidationError(["保留にできるのは貸出の申請だけです。"])
            _finish(conn, request, "on_hold", reviewer_id, reason)
            audit.record(conn, reviewer_id, "request", request_id, request_no(request_id), "hold", f"理由：{reason}")
    finally:
        conn.close()
