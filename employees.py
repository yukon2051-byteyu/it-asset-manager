"""社員のデータを扱うモジュール。"""
import db


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
