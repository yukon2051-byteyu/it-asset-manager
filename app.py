import pandas as pd
import streamlit as st

import db
import devices

st.set_page_config(page_title="社内IT資産管理システム", page_icon="💻")

# データベースのファイルやテーブルがなければ作る（あれば何もしない）
db.init_db()


def show_device_list():
    """資産一覧（F-13）：機器を種別・状態で絞り込んで表示する。"""
    col1, col2 = st.columns(2)
    # 選択肢の中身はコード（pc など）のまま持ち、表示だけ日本語にする
    categories = col1.multiselect(
        "種別", list(devices.CATEGORY_LABELS), format_func=devices.CATEGORY_LABELS.get,
        placeholder="すべて",
    )
    statuses = col2.multiselect(
        "状態", list(devices.STATUS_LABELS), format_func=devices.STATUS_LABELS.get,
        placeholder="すべて",
    )

    rows = devices.list_devices(categories, statuses)
    st.caption(f"{len(rows)} 件")
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("条件に合う機器はありません。")


st.title("社内IT資産管理システム")

# 利用者の種類によってメニューを切り替える（仮の切り替え。ログイン機能は後で作る）
role = st.sidebar.radio("利用者", ["一般社員", "IT管理者"])

if role == "一般社員":
    menu = st.sidebar.radio("メニュー", ["自分の資産", "申請"])
else:
    menu = st.sidebar.radio("メニュー", ["資産一覧", "申請の承認", "ライセンス"])

st.header(menu)
if menu == "資産一覧":
    show_device_list()
else:
    st.info("この画面はまだ準備中です。")
