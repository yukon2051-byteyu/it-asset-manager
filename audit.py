"""操作履歴（F-53）：誰が・いつ・何を・どう変えたかを記録する。

記録は、変更そのものと同じトランザクションの中で行う（record に変更中の接続を渡す）。
そのため「変更されたのに履歴がない」「履歴があるのに変更されていない」ということは起きない。
"""
import unicodedata

import db

TARGET_LABELS = {"device": "機器", "license": "ライセンス", "request": "申請", "employee": "社員"}
ACTION_LABELS = {
    "deactivate": "退職処理",
    "reactivate": "再有効化",
    "reset_password": "パスワード再発行",
    "create": "登録",
    "import": "CSV取込",
    "update": "変更",
    "status": "状態変更",
    "assign": "割当",
    "release": "割当解除",
    "request": "申請",
    "approve": "承認",
    "hold": "保留",
    "reject": "却下",
}


def record(conn, actor_id, target_type, target_id, target_label, action, detail=""):
    """操作履歴を1件記録する。確定（コミット）は、変更と一緒に呼び出し側で行う。"""
    conn.execute(
        "INSERT INTO audit_logs (actor_id, target_type, target_id, target_label, action, detail)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (actor_id, target_type, target_id, target_label, action, detail),
    )


def _show(value):
    return "（空欄）" if value is None or value == "" else str(value)


def describe_changes(before, after, labels):
    """変わった項目だけを「設置場所：倉庫 → 本社4F」の形で並べる。何も変わっていなければ空文字。"""
    parts = []
    for key, label in labels.items():
        if key in after and _show(before.get(key)) != _show(after[key]):
            parts.append(f"{label}：{_show(before.get(key))} → {_show(after[key])}")
    return "、".join(parts)


def list_logs(target_type=None, keyword=None, limit=500, db_path=db.DB_PATH):
    """操作履歴を新しい順に返す。target_type で対象の種類、keyword で対象名・内容の部分一致に絞り込む。"""
    sql = (
        "SELECT a.*, e.employee_code, e.name FROM audit_logs a"
        " LEFT JOIN employees e ON e.id = a.actor_id"
    )
    conditions, params = [], []
    if target_type:
        conditions.append("a.target_type = ?")
        params.append(target_type)
    if keyword:
        keyword = unicodedata.normalize("NFKC", keyword).strip()
    if keyword:
        conditions.append("(a.target_label LIKE ? ESCAPE '\\' OR a.detail LIKE ? ESCAPE '\\')")
        params.extend([db.like_pattern(keyword)] * 2)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY a.id DESC LIMIT ?"
    params.append(limit)

    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "日時": r["created_at"],
            "操作者": f'{r["employee_code"]} {r["name"]}' if r["employee_code"] else "（不明）",
            "対象": TARGET_LABELS[r["target_type"]],
            "対象名": r["target_label"],
            "操作": ACTION_LABELS.get(r["action"], r["action"]),
            "内容": r["detail"] or "",
        }
        for r in rows
    ]
