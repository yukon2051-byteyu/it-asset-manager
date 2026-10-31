"""データベース（SQLite）への接続とテーブル作成を担当するモジュール。

テーブルの内容は docs/basic-design-db.md の設計に合わせている。
"""
import os
import sqlite3
from pathlib import Path

# データベースファイルの保存場所。通常はこのファイルと同じフォルダの assets.db。
# 環境変数 ASSETS_DB_PATH を設定すると、そのファイルを使う（画面の確認を本番のデータと分けて行うため）
DB_PATH = Path(os.environ.get("ASSETS_DB_PATH") or Path(__file__).parent / "assets.db")

# テーブルを作るSQL。IF NOT EXISTS により、すでにテーブルがあれば何もしない
SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY,
    employee_code TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    department    TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    role          TEXT    NOT NULL CHECK (role IN ('user', 'admin')),
    password_hash TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS devices (
    id            INTEGER PRIMARY KEY,
    asset_no      TEXT    NOT NULL UNIQUE,
    category      TEXT    NOT NULL CHECK (category IN ('pc', 'monitor', 'smartphone')),
    maker         TEXT    NOT NULL,
    model         TEXT    NOT NULL,
    serial_no     TEXT    NOT NULL UNIQUE,
    status        TEXT    NOT NULL DEFAULT 'in_stock'
                  CHECK (status IN ('in_stock', 'in_use', 'lent', 'repair', 'lost', 'disposed')),
    employee_id   INTEGER REFERENCES employees(id),
    location      TEXT,
    purchase_date TEXT,
    warranty_end  TEXT,
    os            TEXT,
    screen_size   REAL,
    imei          TEXT    UNIQUE,
    phone_number  TEXT,
    carrier       TEXT,
    contract_end  TEXT,
    note          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS licenses (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    vendor       TEXT,
    license_type TEXT    NOT NULL CHECK (license_type IN ('user', 'device')),
    quantity     INTEGER NOT NULL CHECK (quantity >= 1),
    expires_on   TEXT,
    cost         INTEGER CHECK (cost >= 0),
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS license_assignments (
    id          INTEGER PRIMARY KEY,
    license_id  INTEGER NOT NULL REFERENCES licenses(id),
    employee_id INTEGER REFERENCES employees(id),
    device_id   INTEGER REFERENCES devices(id),
    assigned_on TEXT    NOT NULL,
    released_on TEXT,
    -- 割当先は社員かPCのどちらか一方だけ（両方空欄・両方に値はエラー）
    CHECK ((employee_id IS NULL) <> (device_id IS NULL))
);

CREATE TABLE IF NOT EXISTS requests (
    id            INTEGER PRIMARY KEY,
    request_type  TEXT    NOT NULL CHECK (request_type IN ('lend', 'return', 'failure', 'loss')),
    requester_id  INTEGER NOT NULL REFERENCES employees(id),
    -- 貸出は承認時に管理者が機器を選ぶので、それまで空欄。返却・故障・紛失は申請時に必須
    device_id     INTEGER REFERENCES devices(id),
    category      TEXT    CHECK (category IN ('pc', 'monitor', 'smartphone')),
    detail        TEXT,
    start_date    TEXT,
    due_date      TEXT,
    occurred_on   TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'rejected', 'on_hold')),
    reviewer_id   INTEGER REFERENCES employees(id),
    comment       TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    processed_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    actor_id     INTEGER REFERENCES employees(id),
    target_type  TEXT    NOT NULL CHECK (target_type IN ('device', 'license', 'request', 'employee')),
    target_id    INTEGER NOT NULL,
    target_label TEXT    NOT NULL,
    action       TEXT    NOT NULL,
    detail       TEXT
);

-- 操作履歴は、あとから変更・削除できないようにする（要件定義書 N-11）
CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, '操作履歴は変更できません');
END;
CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, '操作履歴は削除できません');
END;
"""


def get_connection(db_path=DB_PATH):
    """データベースに接続する。"""
    conn = sqlite3.connect(db_path)
    # SQLiteは初期設定では外部キーのチェックが無効なので、接続のたびに有効にする
    conn.execute("PRAGMA foreign_keys = ON")
    # 結果を row["name"] のように列名で取り出せるようにする
    conn.row_factory = sqlite3.Row
    return conn


def like_pattern(keyword):
    """部分一致検索（LIKE ... ESCAPE '\\'）用の文字列を作る。

    % と _ はSQLでは特別な意味を持つので、ただの文字として扱うよう印（\\）を付ける。
    """
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _migrate(conn):
    """古い構造のデータベースを、データを残したまま新しい構造に移す（マイグレーション）。

    v0.3 の audit_logs は対象の種類に「社員（employee）」を含まないルールで作られている。
    SQLite は既存テーブルのルール（CHECK）を変更できないため、作り直してデータを移す。
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'audit_logs'").fetchone()
    if row is None or "'employee'" in row["sql"]:
        return  # まだテーブルがない、またはすでに新しい構造
    conn.executescript(
        """
        BEGIN;
        DROP TRIGGER IF EXISTS audit_logs_no_update;
        DROP TRIGGER IF EXISTS audit_logs_no_delete;
        ALTER TABLE audit_logs RENAME TO audit_logs_old;
        """
        + SCHEMA  # 新しいルールの audit_logs と、書き換え禁止のトリガーを作る
        + """
        INSERT INTO audit_logs SELECT * FROM audit_logs_old;
        DROP TABLE audit_logs_old;
        COMMIT;
        """
    )


def init_db(db_path=DB_PATH):
    """テーブルを作る。何度実行しても、既存のデータは消えない。"""
    conn = get_connection(db_path)
    try:
        _migrate(conn)
        conn.executescript(SCHEMA)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"データベースを準備しました: {DB_PATH}")
