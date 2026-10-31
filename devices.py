"""機器（PC・モニター・スマートフォン）のデータを扱うモジュール。"""
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
