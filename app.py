import pandas as pd
import streamlit as st

import auth
import db
import devices
import employees
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


def device_form_fields(category, device=None, key=""):
    """機器の入力欄を並べ、入力内容を辞書で返す（登録と更新で共通）。

    device を渡すと、その値を初期値にする。key は入力欄を機器ごとに区別するための目印。
    """
    device = device or {}

    def text(label, field, placeholder=None):
        return st.text_input(label, value=device.get(field) or "", placeholder=placeholder, key=key + field)

    def date(label, field):
        return st.date_input(label, value=device.get(field), key=key + field)

    data = {"category": category}
    data["maker"] = text("メーカー（必須）", "maker")
    data["model"] = text("型番（必須）", "model")
    data["serial_no"] = text("シリアル番号（必須）", "serial_no")
    if category == "pc":
        data["os"] = text("OS", "os", placeholder="例：Windows 11")
    elif category == "monitor":
        data["screen_size"] = st.number_input(
            "画面サイズ（インチ）", value=device.get("screen_size"), min_value=0.0, step=0.1, key=key + "screen_size"
        )
    elif category == "smartphone":
        data["imei"] = text("IMEI（15桁の数字）", "imei")
        data["phone_number"] = text("電話番号", "phone_number")
        data["carrier"] = text("通信キャリア", "carrier")
        data["contract_end"] = date("契約期限", "contract_end")
    data["location"] = text("設置場所", "location", placeholder="例：情報システム部 倉庫")
    data["purchase_date"] = date("購入日", "purchase_date")
    data["warranty_end"] = date("保証期限", "warranty_end")
    data["note"] = st.text_area("備考", value=device.get("note") or "", key=key + "note")
    return data


def show_device_register():
    """資産登録（F-11）：新しい機器を「在庫」の状態で登録する。"""
    # 種別は form の外に置く。form の中だと、ボタンを押すまで種別ごとの項目が切り替わらないため
    category = st.selectbox("種別", list(devices.CATEGORY_LABELS), format_func=devices.CATEGORY_LABELS.get)

    with st.form("device_register"):
        data = device_form_fields(category, key="new_")
        submitted = st.form_submit_button("登録する")

    if submitted:
        try:
            asset_no = devices.register_device(data)
        except devices.ValidationError as e:
            for message in e.errors:
                st.error(message)
        else:
            st.success(f"登録しました。資産番号は {asset_no} です。機器にラベルを貼ってください。")


def show_device_edit():
    """資産更新（F-12）：登録済みの機器の情報を変更する。状態と利用者はここでは変えない。"""
    rows = devices.list_devices()
    if not rows:
        st.info("登録されている機器はありません。")
        return
    labels = {r["資産番号"]: f'{r["資産番号"]}　{r["メーカー"]} {r["型番"]}（{r["状態"]}）' for r in rows}
    # key を付けておくと、更新で選択肢の表示（メーカー名など）が変わっても選択が先頭に戻らない
    asset_no = st.selectbox("変更する機器", list(labels), format_func=labels.get, key="edit_target")

    device = devices.get_device(asset_no)
    st.caption(
        f'種別：{devices.CATEGORY_LABELS[device["category"]]}　'
        f'状態：{devices.STATUS_LABELS[device["status"]]}　'
        f'利用者：{device["user_name"] or "なし"}'
    )
    if device["status"] == "disposed":
        st.warning("廃棄済の機器は変更できません。")
        return

    # 機器ごとに別の form・入力欄にする。同じ入力欄を使い回すと、前の機器の入力内容が残ってしまうため
    with st.form(f"edit_{asset_no}"):
        data = device_form_fields(device["category"], device, key=f"edit_{asset_no}_")
        submitted = st.form_submit_button("更新する")

    if submitted:
        try:
            devices.update_device(asset_no, data)
        except devices.ValidationError as e:
            for message in e.errors:
                st.error(message)
        else:
            st.success(f"{asset_no} を更新しました。")


def show_device_status():
    """割当・返却・廃棄（F-25 / F-16）：業務の操作として機器の状態を変える。"""
    # 前回の操作結果を表示する（操作後に画面を描き直すと、メッセージが消えてしまうため）
    if "status_message" in st.session_state:
        st.success(st.session_state.pop("status_message"))

    rows = devices.list_devices()
    if not rows:
        st.info("登録されている機器はありません。")
        return
    labels = {r["資産番号"]: f'{r["資産番号"]}　{r["メーカー"]} {r["型番"]}（{r["状態"]}）' for r in rows}
    asset_no = st.selectbox("対象の機器", list(labels), format_func=labels.get, key="status_target")

    device = devices.get_device(asset_no)
    st.caption(f'状態：{devices.STATUS_LABELS[device["status"]]}　利用者：{device["user_name"] or "なし"}')

    actions = devices.available_actions(device["status"])
    if not actions:
        st.info(f'{devices.STATUS_LABELS[device["status"]]}の機器にできる操作はありません。')
        return
    action = st.radio("操作", actions, format_func=devices.ACTION_LABELS.get, horizontal=True, key=f"action_{asset_no}")

    employee_id = None
    ready = True
    if action == "assign":
        staff = {e["id"]: f'{e["employee_code"]} {e["name"]}（{e["department"]}）' for e in employees.list_employees()}
        employee_id = st.selectbox("割り当てる社員", list(staff), format_func=staff.get, index=None,
                                   placeholder="選んでください", key=f"assignee_{asset_no}")
    elif action == "dispose":
        st.warning("廃棄すると元に戻せません。このPCに割り当てているライセンスも解除されます。")
        ready = st.checkbox("内容を確認しました", key=f"confirm_{asset_no}")

    if st.button("実行する", type="primary", disabled=not ready):
        try:
            new_status = devices.change_status(asset_no, action, employee_id)
        except devices.ValidationError as e:
            for message in e.errors:
                st.error(message)
        else:
            st.session_state["status_message"] = (
                f"{asset_no} を{devices.ACTION_LABELS[action]}しました（状態：{devices.STATUS_LABELS[new_status]}）。"
            )
            st.rerun()  # 新しい状態で、できる操作を表示し直す


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
    menu = st.sidebar.radio("メニュー", ["資産一覧", "資産登録", "資産更新", "割当・返却・廃棄", "申請の承認", "ライセンス"])
else:
    menu = st.sidebar.radio("メニュー", ["自分の資産", "申請"])

st.header(menu)
if menu == "資産一覧":
    show_device_list()
elif menu == "資産登録":
    show_device_register()
elif menu == "資産更新":
    show_device_edit()
elif menu == "割当・返却・廃棄":
    show_device_status()
elif menu == "自分の資産":
    show_my_assets(user["id"])
else:
    st.info("この画面はまだ準備中です。")
