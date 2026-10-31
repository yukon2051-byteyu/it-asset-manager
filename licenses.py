"""ソフトウェアライセンスのデータを扱うモジュール。"""
import db


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
