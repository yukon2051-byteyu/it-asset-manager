import streamlit as st

st.set_page_config(page_title="社内IT資産管理システム", page_icon="💻")

st.title("社内IT資産管理システム")

# 利用者の種類によってメニューを切り替える（仮の切り替え。ログイン機能は後で作る）
role = st.sidebar.radio("利用者", ["一般社員", "IT管理者"])

if role == "一般社員":
    menu = st.sidebar.radio("メニュー", ["自分の資産", "申請"])
else:
    menu = st.sidebar.radio("メニュー", ["資産一覧", "申請の承認", "ライセンス"])

st.header(menu)
st.info("この画面はまだ準備中です。")
