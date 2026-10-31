import pandas as pd
import streamlit as st

import auth
import db
import devices
import licenses

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


def show_my_assets(employee_id):
    """自分の資産（UC-02）：自分が利用している機器とライセンスを表示する。"""
    st.subheader("機器")
    rows = devices.list_devices(employee_id=employee_id)
    if rows:
        # 利用者・部署は自分自身なので表示しない
        st.dataframe(pd.DataFrame(rows).drop(columns=["利用者", "部署"]), hide_index=True)
    else:
        st.info("割り当てられている機器はありません。")

    st.subheader("ソフトウェアライセンス")
    rows = licenses.list_licenses_of_employee(employee_id)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("割り当てられているライセンスはありません。")


def show_login():
    """ログイン画面（F-01）。成功したらログインした社員の情報を session_state に保存する。"""
    st.subheader("ログイン")
    # form にまとめると、ボタンを押したときだけ入力内容が送られる
    with st.form("login"):
        code = st.text_input("社員番号")
        password = st.text_input("パスワード", type="password")
        submitted = st.form_submit_button("ログイン")

    if submitted:
        if not code.strip() or not password:
            st.error("社員番号とパスワードを入力してください。")
            return
        user = auth.authenticate(code.strip(), password)
        if user is None:
            # どちらが違うかは伝えない（社員番号が存在するかを推測されないため）
            st.error("社員番号またはパスワードが違います。")
        else:
            st.session_state["user"] = user
            st.rerun()  # ログイン後の画面で描き直す


st.title("社内IT資産管理システム")

# session_state は、画面を操作しても消えずに残る保存場所。ここにログイン中の社員を入れておく
user = st.session_state.get("user")
if user is None:
    show_login()
    st.stop()  # ログインしていなければ、ここから下は表示しない

st.sidebar.write(f'{user["name"]}（{"IT管理者" if user["role"] == "admin" else "一般社員"}）')
if st.sidebar.button("ログアウト"):
    del st.session_state["user"]
    st.rerun()

# 権限によって使えるメニューを分ける（F-02）
if user["role"] == "admin":
    menu = st.sidebar.radio("メニュー", ["資産一覧", "申請の承認", "ライセンス"])
else:
    menu = st.sidebar.radio("メニュー", ["自分の資産", "申請"])

st.header(menu)
if menu == "資産一覧":
    show_device_list()
elif menu == "自分の資産":
    show_my_assets(user["id"])
else:
    st.info("この画面はまだ準備中です。")
