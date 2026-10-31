"""集計レポート（F-52）：機器の台数（状態・種別・部署ごと）と、ライセンスの利用率。

廃棄済の機器は、今ある資産ではないので集計に含めない。
"""
import db
import devices
import licenses

# 集計に使う状態の並び（グラフの色もこの順に固定して割り当てる）
STATUS_ORDER = ["in_stock", "in_use", "lent", "repair", "lost"]


def device_summary(db_path=db.DB_PATH):
    """数字のタイル用：機器の総数、利用中（使用中＋貸出中）、在庫、要対応（修理中＋紛失）、廃棄済の台数。"""
    conn = db.get_connection(db_path)
    try:
        counts = dict(conn.execute("SELECT status, COUNT(*) FROM devices GROUP BY status").fetchall())
    finally:
        conn.close()
    return {
        "total": sum(counts.get(s, 0) for s in STATUS_ORDER),
        "in_use": counts.get("in_use", 0) + counts.get("lent", 0),
        "in_stock": counts.get("in_stock", 0),
        "attention": counts.get("repair", 0) + counts.get("lost", 0),
        "disposed": counts.get("disposed", 0),
    }


def devices_by_category_status(db_path=db.DB_PATH):
    """種別×状態ごとの台数。0台の組み合わせも含めて返す（表で抜けが出ないように）。"""
    conn = db.get_connection(db_path)
    try:
        counts = {(r["category"], r["status"]): r["n"] for r in conn.execute(
            "SELECT category, status, COUNT(*) AS n FROM devices GROUP BY category, status"
        )}
    finally:
        conn.close()
    return [
        {"種別": devices.CATEGORY_LABELS[c], "状態": devices.STATUS_LABELS[s], "台数": counts.get((c, s), 0)}
        for c in devices.CATEGORY_LABELS
        for s in STATUS_ORDER
    ]


def devices_by_department(db_path=db.DB_PATH):
    """部署ごとの、利用中（使用中＋貸出中）の機器の台数。多い順（同じ台数なら部署名の順）。"""
    conn = db.get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT e.department, COUNT(*) AS n FROM devices d JOIN employees e ON e.id = d.employee_id"
            " WHERE d.status IN ('in_use', 'lent') GROUP BY e.department ORDER BY n DESC, e.department"
        ).fetchall()
    finally:
        conn.close()
    return [{"部署": r["department"], "台数": r["n"]} for r in rows]


def license_usage(today=None, db_path=db.DB_PATH):
    """ライセンスごとの利用率（割当数 ÷ 保有数）。期限切れ・期限間近・残数なしの注意も添える。"""
    return [
        {
            "ライセンス名": r["ライセンス名"],
            "割当数": r["割当数"],
            "保有数": r["保有数"],
            "利用率（%）": round(r["割当数"] / r["保有数"] * 100, 1),
            "注意": r["注意"],
        }
        for r in licenses.list_licenses(today, db_path)
    ]
