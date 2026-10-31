"""画面の表をCSV・Excelファイルにする（F-51）。"""
import io

import pandas as pd


def to_csv_bytes(df):
    """CSVファイルの中身を返す。Excelでダブルクリックして開いても文字化けしないよう BOM 付き UTF-8 にする。"""
    return df.to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(df, sheet_name="一覧"):
    """Excelファイル（.xlsx）の中身を返す。列の幅は、中身の長さに合わせて広げる。"""
    buffer = io.BytesIO()  # ファイルに保存せず、メモリの上でExcelファイルを作る
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
        sheet = writer.sheets[sheet_name]
        for i, column in enumerate(df.columns):
            longest = max([len(str(column)), *(len(str(v)) for v in df[column])])
            # 日本語は1文字が英数字2文字分の幅なので、少し広めにとる（広すぎないよう上限 60）
            sheet.column_dimensions[sheet.cell(row=1, column=i + 1).column_letter].width = min(longest * 2 + 2, 60)
    return buffer.getvalue()
