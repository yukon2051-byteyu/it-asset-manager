"""CSVファイルから機器をまとめて登録する（F-15）。既存のExcel台帳からの移行に使う。

1件でも誤りがあれば、全件取り込まない。
"""
import datetime
import io
import re
import sqlite3

import pandas as pd

import audit
import db
import devices

# CSVの列名と、データベースの列名の対応
COLUMNS = {
    "資産番号": "asset_no",
    "種別": "category",
    "メーカー": "maker",
    "型番": "model",
    "シリアル番号": "serial_no",
    "利用者の社員番号": "employee_code",
    "設置場所": "location",
    "購入日": "purchase_date",
    "保証期限": "warranty_end",
    "OS": "os",
    "画面サイズ": "screen_size",
    "IMEI": "imei",
    "電話番号": "phone_number",
    "通信キャリア": "carrier",
    "契約期限": "contract_end",
    "備考": "note",
}
REQUIRED_COLUMNS = ["種別", "メーカー", "型番", "シリアル番号"]
DATE_COLUMNS = {"purchase_date": "購入日", "warranty_end": "保証期限", "contract_end": "契約期限"}

# 種別は「PC」「モニター」などの表示名でも、「pc」などのコードでも受け付ける
CATEGORY_CODES = {label: code for code, label in devices.CATEGORY_LABELS.items()}
CATEGORY_CODES.update({code: code for code in devices.CATEGORY_LABELS})


def template_csv():
    """取り込み用のひな形（列名だけのCSV）。Excelで開いても文字化けしないよう BOM 付き UTF-8 にする。"""
    return (",".join(COLUMNS) + "\r\n").encode("utf-8-sig")


def read_csv(content):
    """CSVの中身（バイト列）を表として読み込む。文字コードは UTF-8 と Shift_JIS に対応する。"""
    for encoding in ("utf-8-sig", "cp932"):  # cp932 は Windows で使われる Shift_JIS
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise devices.ValidationError(["文字コードを判別できません。UTF-8 または Shift_JIS で保存してください。"])
    if not text.strip():
        raise devices.ValidationError(["ファイルが空です。"])

    try:
        # dtype=str：すべて文字として読む。数字として読むと、IMEI が「3.5e+14」になったり先頭の0が消えたりするため
        # keep_default_na=False：空欄を「欠損値（NaN）」ではなく空文字として読む
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except pd.errors.ParserError:
        raise devices.ValidationError(["CSVの形式が正しくありません（列の数がそろっていない行があります）。"])
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise devices.ValidationError([f"必須の列がありません：{'、'.join(missing)}"])
    if df.empty:
        raise devices.ValidationError(["取り込む行がありません。"])
    return df


def _parse_date(text):
    """「2026-04-01」「2026/4/1」を日付にする。読めなければ ValueError。"""
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not match:
        raise ValueError(text)
    return datetime.date(*map(int, match.groups()))  # 2026/2/30 のような存在しない日も ValueError になる


def _prepare(df, conn):
    """各行をチェックし、登録用のデータにする。1件でも誤りがあれば、全部の誤りをまとめて ValidationError にする。"""
    errors = []
    records = []
    seen = {"asset_no": set(), "serial_no": set(), "imei": set()}  # ファイル内の重複を見つけるため

    for index, row in df.iterrows():
        line = index + 2  # 1行目は列名なので、データは2行目から始まる
        row_errors = []
        data = {COLUMNS[c]: row[c].strip() for c in df.columns if c in COLUMNS}

        category_text = data.get("category", "")
        category = CATEGORY_CODES.get(category_text)
        if category is None:
            row_errors.append(f"種別「{category_text}」は使えません（PC・モニター・スマートフォンのいずれか）。")
        data["category"] = category

        for key, label in DATE_COLUMNS.items():
            if data.get(key):
                try:
                    data[key] = _parse_date(data[key])
                except ValueError:
                    row_errors.append(f"{label}「{data[key]}」は日付として読めません（例：2026-04-01）。")
                    data[key] = None

        if data.get("screen_size"):
            try:
                data["screen_size"] = float(data["screen_size"])
            except ValueError:
                row_errors.append(f"画面サイズ「{data['screen_size']}」は数字ではありません。")
                data["screen_size"] = None

        employee_id = None
        employee_code = data.pop("employee_code", "")
        if employee_code:
            found = conn.execute(
                "SELECT id FROM employees WHERE employee_code = ? AND is_active = 1", (employee_code,)
            ).fetchone()
            if found is None:
                row_errors.append(f"利用者の社員番号「{employee_code}」は、在籍中の社員にいません。")
            else:
                employee_id = found["id"]

        asset_no = data.pop("asset_no", "") or None
        if asset_no and category:
            prefix = devices.ASSET_NO_PREFIXES[category]
            if not re.fullmatch(re.escape(prefix) + r"[0-9]{4}", asset_no):
                row_errors.append(f"資産番号「{asset_no}」は「{prefix}0001」の形式にしてください。")
            elif conn.execute("SELECT 1 FROM devices WHERE asset_no = ?", (asset_no,)).fetchone():
                row_errors.append(f"資産番号「{asset_no}」はすでに登録されています。")

        # 必須項目・データベース内の重複などは、画面からの登録と同じチェックを使う
        values = devices.clean_device_input(data)
        row_errors += [e for e in devices.validate_device(values, conn) if e != "種別を選んでください。"]

        for key, label, value in [("asset_no", "資産番号", asset_no), ("serial_no", "シリアル番号", values.get("serial_no")),
                                  ("imei", "IMEI", values.get("imei"))]:
            if value:
                if value in seen[key]:
                    row_errors.append(f"{label}「{value}」がファイル内で重複しています。")
                seen[key].add(value)

        errors += [f"{line}行目：{message}" for message in row_errors]
        records.append({"line": line, "asset_no": asset_no, "values": values, "employee_id": employee_id})

    if errors:
        raise devices.ValidationError(errors)
    _fill_asset_numbers(records, conn)
    return records


def _fill_asset_numbers(records, conn):
    """資産番号が空欄の行に番号を振る。ファイル内で指定された番号ともぶつからないよう、大きい方の続きから振る。"""
    last = {}
    for category, prefix in devices.ASSET_NO_PREFIXES.items():
        in_db = int(devices.next_asset_no(conn, category)[len(prefix):]) - 1
        in_file = [int(r["asset_no"][len(prefix):]) for r in records
                   if r["asset_no"] and r["values"]["category"] == category]
        last[category] = max([in_db, *in_file])
    for r in records:
        if r["asset_no"] is None:
            category = r["values"]["category"]
            last[category] += 1
            r["asset_no"] = f"{devices.ASSET_NO_PREFIXES[category]}{last[category]:04d}"


def preview_csv(content, db_path=db.DB_PATH):
    """取り込む前の確認用に、登録される内容を一覧で返す（データベースは変更しない）。"""
    df = read_csv(content)
    conn = db.get_connection(db_path)
    try:
        records = _prepare(df, conn)
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM employees")}
    finally:
        conn.close()
    return [
        {
            "行": r["line"],
            "資産番号": r["asset_no"],
            "種別": devices.CATEGORY_LABELS[r["values"]["category"]],
            "メーカー": r["values"]["maker"],
            "型番": r["values"]["model"],
            "シリアル番号": r["values"]["serial_no"],
            "状態": "使用中" if r["employee_id"] else "在庫",
            "利用者": names.get(r["employee_id"], ""),
        }
        for r in records
    ]


def import_csv(content, db_path=db.DB_PATH, actor_id=None):
    """CSVの機器をまとめて登録し、登録した資産番号の一覧を返す。誤りがあれば1件も登録しない。"""
    df = read_csv(content)
    conn = db.get_connection(db_path)
    try:
        records = _prepare(df, conn)
        try:
            # 全件を1つのトランザクションで登録する。途中で失敗したら全部取り消される
            with conn:
                for r in records:
                    device_id = devices.insert_device(conn, r["asset_no"], r["values"], r["employee_id"])
                    status = "使用中" if r["employee_id"] else "在庫"
                    audit.record(conn, actor_id, "device", device_id, r["asset_no"], "import",
                                 f'{devices.describe_device(r["values"])}、状態：{status}（CSVの{r["line"]}行目）')
        except sqlite3.IntegrityError:
            # チェックのあとに、ほかの人が同じ番号を登録した場合など
            raise devices.ValidationError(["ほかの人が同時に機器を登録したため、取り込めませんでした。もう一度お試しください。"])
        return [r["asset_no"] for r in records]
    finally:
        conn.close()
