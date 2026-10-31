import pandas as pd
import streamlit as st

import applications
import auth
import db
import device_import
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
    col3, col4 = st.columns(2)
    keyword = col3.text_input("キーワード", placeholder="資産番号・シリアル番号・利用者名の一部")
    department = col4.selectbox("利用者の部署", employees.list_departments(), index=None, placeholder="すべて")

    rows = devices.list_devices(categories, statuses, keyword=keyword, department=department)
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


def show_errors(errors, title):
    """誤りの一覧を、1つの枠にまとめて表示する（行数が多くても見やすいように）。"""
    st.error(title + "\n\n" + "\n".join(f"- {message}" for message in errors))


def show_device_import():
    """CSV一括登録（F-15）：既存の台帳をCSVで取り込む。1件でも誤りがあれば全件取り込まない。"""
    if "import_message" in st.session_state:
        st.success(st.session_state.pop("import_message"))

    st.write("既存の台帳をCSVファイルで取り込みます。1件でも誤りがあると、全件取り込みません。")
    st.download_button("ひな形をダウンロード", device_import.template_csv(),
                       file_name="機器取込ひな形.csv", mime="text/csv")

    # 取り込み後にファイル選択欄を空に戻すため、key を変えられるようにしておく
    uploader_key = f'import_file_{st.session_state.get("import_count", 0)}'
    file = st.file_uploader("CSVファイル", type="csv", key=uploader_key)
    if file is None:
        return
    content = file.getvalue()

    try:
        preview = device_import.preview_csv(content)
    except devices.ValidationError as e:
        show_errors(e.errors, "取り込めません。次の誤りを直してから、もう一度ファイルを選んでください。")
        return

    st.write(f"次の {len(preview)} 件を取り込みます。内容を確認してください。")
    st.dataframe(pd.DataFrame(preview), hide_index=True)
    if st.button("取り込む", type="primary"):
        try:
            asset_nos = device_import.import_csv(content)
        except devices.ValidationError as e:
            show_errors(e.errors, "取り込めませんでした。")
        else:
            st.session_state["import_message"] = f"{len(asset_nos)} 件を登録しました（{asset_nos[0]} ほか）。"
            st.session_state["import_count"] = st.session_state.get("import_count", 0) + 1
            st.rerun()


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


def flash(key):
    """前回の操作の完了メッセージを表示する（画面を描き直すと消えてしまうため、いったん保存してある）。"""
    if key in st.session_state:
        st.success(st.session_state.pop(key))


def done(key, message):
    """完了メッセージを保存して、新しいデータで画面を描き直す。"""
    st.session_state[key] = message
    st.rerun()


def license_form_fields(lic=None, key=""):
    """ライセンスの入力欄（登録と変更で共通）。lic を渡すと、その値を初期値にする。"""
    lic = lic or {}
    data = {}
    data["name"] = st.text_input("ライセンス名（必須）", value=lic.get("name") or "", key=key + "name")
    data["vendor"] = st.text_input("提供元", value=lic.get("vendor") or "", key=key + "vendor")
    data["quantity"] = st.number_input("保有数（必須）", value=lic.get("quantity", 1), min_value=1, step=1,
                                       key=key + "quantity")
    data["expires_on"] = st.date_input("有効期限（空欄＝期限なし）", value=lic.get("expires_on"), key=key + "expires_on")
    data["cost"] = st.number_input("費用（円）", value=lic.get("cost"), min_value=0, step=1000, key=key + "cost")
    data["note"] = st.text_area("備考", value=lic.get("note") or "", key=key + "note")
    return data


def show_licenses():
    """ライセンス管理（F-31〜F-34）：一覧・登録・割当と解除・変更。"""
    flash("license_message")
    rows = licenses.list_licenses()

    # 期限切れ・期限間近のものは、どのタブを見ていても気づけるよう画面の上部に出す（F-34）
    for r in rows:
        if "期限" in r["注意"]:
            st.warning(f'{r["ライセンス名"]}：{r["注意"].split("・")[0]}（有効期限 {r["有効期限"]}）')

    tab_list, tab_assign, tab_new, tab_edit = st.tabs(["一覧", "割当と解除", "登録", "保有数・期限の変更"])

    with tab_list:
        if rows:
            st.dataframe(pd.DataFrame(rows).drop(columns=["id"]), hide_index=True)
        else:
            st.info("登録されているライセンスはありません。")

    labels = {r["id"]: f'{r["ライセンス名"]}（残数 {r["残数"]} / {r["保有数"]}）' for r in rows}

    with tab_assign:
        if not rows:
            st.info("登録されているライセンスはありません。")
        else:
            license_id = st.selectbox("ライセンス", list(labels), format_func=labels.get, key="assign_license")
            lic = licenses.get_license(license_id)
            assignments = licenses.list_assignments(license_id)

            st.subheader("割当先")
            if assignments:
                st.dataframe(pd.DataFrame(assignments).drop(columns=["id"]), hide_index=True)
                targets = {a["id"]: a["割当先"] for a in assignments}
                col1, col2 = st.columns([3, 1])
                release_id = col1.selectbox("解除する割当先", list(targets), format_func=targets.get,
                                            key=f"release_{license_id}")
                if col2.button("解除する", key=f"release_button_{license_id}"):
                    try:
                        licenses.release_license(release_id)
                    except devices.ValidationError as e:
                        show_errors(e.errors, "解除できませんでした。")
                    else:
                        done("license_message", f'{lic["name"]}：{targets[release_id]} の割当を解除しました。')
            else:
                st.info("まだ誰にも割り当てていません。")

            st.subheader("新しく割り当てる")
            employee_id = device_id = None
            if lic["license_type"] == "user":
                staff = {e["id"]: f'{e["employee_code"]} {e["name"]}（{e["department"]}）' for e in employees.list_employees()}
                employee_id = st.selectbox("社員", list(staff), format_func=staff.get, index=None,
                                           placeholder="選んでください", key=f"assign_emp_{license_id}")
            else:
                pcs = licenses.list_assignable_pcs()
                device_id = st.selectbox("PC", list(pcs), format_func=pcs.get, index=None,
                                         placeholder="選んでください", key=f"assign_pc_{license_id}")
            if st.button("割り当てる", type="primary", key=f"assign_button_{license_id}"):
                try:
                    licenses.assign_license(license_id, employee_id=employee_id, device_id=device_id)
                except devices.ValidationError as e:
                    show_errors(e.errors, "割り当てられませんでした。")
                else:
                    done("license_message", f'{lic["name"]} を割り当てました。')

    with tab_new:
        license_type = st.radio("割当の単位", list(licenses.LICENSE_TYPE_LABELS),
                                format_func=licenses.LICENSE_TYPE_LABELS.get, horizontal=True, key="new_license_type")
        with st.form("license_new"):
            data = license_form_fields(key="new_license_")
            submitted = st.form_submit_button("登録する")
        if submitted:
            try:
                licenses.register_license({**data, "license_type": license_type})
            except devices.ValidationError as e:
                show_errors(e.errors, "登録できませんでした。")
            else:
                done("license_message", f'ライセンス「{data["name"].strip()}」を登録しました。')

    with tab_edit:
        if not rows:
            st.info("登録されているライセンスはありません。")
        else:
            edit_id = st.selectbox("ライセンス", list(labels), format_func=labels.get, key="edit_license")
            lic = licenses.get_license(edit_id)
            st.caption(f'割当の単位：{licenses.LICENSE_TYPE_LABELS[lic["license_type"]]}（変更できません）')
            with st.form(f"license_edit_{edit_id}"):
                data = license_form_fields(lic, key=f"edit_license_{edit_id}_")
                submitted = st.form_submit_button("変更する")
            if submitted:
                try:
                    licenses.update_license(edit_id, data)
                except devices.ValidationError as e:
                    show_errors(e.errors, "変更できませんでした。")
                else:
                    done("license_message", f'ライセンス「{data["name"].strip()}」を変更しました。')


# 申請の一覧のうち、画面には出さない内部用の列
REQUEST_INTERNAL_COLUMNS = ["id", "request_type", "category"]


def request_table(rows, drop=()):
    return pd.DataFrame(rows).drop(columns=[*REQUEST_INTERNAL_COLUMNS, *drop])


def show_my_requests(user):
    """申請（UC-03〜UC-06）：一般社員が貸出・返却・故障・紛失を申請し、状況を確認する。"""
    flash("request_message")
    tab_new, tab_list = st.tabs(["新しい申請", "申請の状況"])

    with tab_new:
        # 種類は form の外に置く（種類ごとに入力欄を切り替えるため）
        request_type = st.radio("申請の種類", list(applications.TYPE_LABELS),
                                format_func=applications.TYPE_LABELS.get, horizontal=True, key="request_type")
        my_devices = applications.list_my_devices(user["id"])
        if request_type != "lend" and not my_devices:
            st.info("あなたが使っている機器はありません。")
        else:
            key = f"req_{request_type}_"
            with st.form(f"request_{request_type}"):
                data = {"request_type": request_type}
                if request_type == "lend":
                    data["category"] = st.selectbox("借りたい機器", list(devices.CATEGORY_LABELS),
                                                    format_func=devices.CATEGORY_LABELS.get, key=key + "category")
                    data["detail"] = st.text_area("利用目的（必須）", placeholder="例：出張先でのプレゼンに使うため",
                                                  key=key + "detail")
                    col1, col2 = st.columns(2)
                    data["start_date"] = col1.date_input("利用開始日（必須）", value=None, key=key + "start")
                    data["due_date"] = col2.date_input("返却予定日（必須）", value=None, key=key + "due")
                else:
                    data["device_id"] = st.selectbox("対象の機器", list(my_devices), format_func=my_devices.get,
                                                     key=key + "device")
                    if request_type in ("failure", "loss"):
                        data["occurred_on"] = st.date_input("発生日（必須）", value=None, key=key + "occurred")
                        data["detail"] = st.text_area("内容（必須）", placeholder="例：電源が入らない／電車に置き忘れた",
                                                      key=key + "detail")
                    else:
                        data["detail"] = st.text_area("備考", key=key + "detail")
                submitted = st.form_submit_button("申請する")
            if submitted:
                try:
                    request_id = applications.create_request(user["id"], data)
                except devices.ValidationError as e:
                    show_errors(e.errors, "申請できませんでした。")
                else:
                    done("request_message",
                         f"{applications.request_no(request_id)} で申請しました。IT管理者の承認をお待ちください。")

    with tab_list:
        rows = applications.list_requests(requester_id=user["id"])
        if rows:
            st.dataframe(request_table(rows, drop=["申請者"]), hide_index=True)
        else:
            st.info("まだ申請はありません。")


def show_request_review(user):
    """申請の承認（UC-13）：IT管理者が申請を承認・保留・却下する。承認すると機器の状態も変わる。"""
    flash("review_message")
    tab_open, tab_all = st.tabs(["処理待ち", "すべての申請"])

    with tab_open:
        rows = applications.list_requests(statuses=list(applications.OPEN_STATUSES))
        if not rows:
            st.info("処理待ちの申請はありません。")
        else:
            st.dataframe(request_table(rows), hide_index=True)
            labels = {r["id"]: f'{r["申請番号"]}　{r["種類"]}　{r["申請者"]}（{r["状態"]}）' for r in rows}
            request_id = st.selectbox("処理する申請", list(labels), format_func=labels.get, key="review_target")
            r = next(row for row in rows if row["id"] == request_id)
            st.caption(f'対象：{r["対象"]}　{r["期間・発生日"]}　内容：{r["内容"] or "なし"}')

            device_id = None
            can_approve = True
            if r["request_type"] == "lend":
                stock = applications.list_stock_devices(r["category"])
                if stock:
                    device_id = st.selectbox("貸し出す機器（在庫）", list(stock), format_func=stock.get,
                                             key=f"lend_device_{request_id}")
                else:
                    st.warning("この種別の在庫がありません。保留にするか、却下してください。")
                    can_approve = False
            reason = st.text_input("却下・保留の理由（却下・保留のときは必須。申請者に表示されます）",
                                   key=f"reason_{request_id}")

            col1, col2, col3 = st.columns(3)
            action = None
            if col1.button("承認する", type="primary", disabled=not can_approve, key=f"approve_{request_id}"):
                action = ("承認しました", lambda: applications.approve(request_id, user["id"], device_id))
            if r["request_type"] == "lend" and col2.button("保留にする", key=f"hold_{request_id}"):
                action = ("保留にしました", lambda: applications.hold(request_id, user["id"], reason))
            if col3.button("却下する", key=f"reject_{request_id}"):
                action = ("却下しました", lambda: applications.reject(request_id, user["id"], reason))
            if action:
                message, run = action
                try:
                    run()
                except devices.ValidationError as e:
                    show_errors(e.errors, "処理できませんでした。")
                else:
                    done("review_message", f'{r["申請番号"]}（{r["種類"]}）を{message}。')

    with tab_all:
        rows = applications.list_requests()
        if rows:
            st.dataframe(request_table(rows), hide_index=True)
        else:
            st.info("まだ申請はありません。")


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
    menu = st.sidebar.radio("メニュー", ["資産一覧", "資産登録", "CSV一括登録", "資産更新", "割当・返却・廃棄", "申請の承認", "ライセンス"])
else:
    menu = st.sidebar.radio("メニュー", ["自分の資産", "申請"])

st.header(menu)
if menu == "資産一覧":
    show_device_list()
elif menu == "資産登録":
    show_device_register()
elif menu == "CSV一括登録":
    show_device_import()
elif menu == "資産更新":
    show_device_edit()
elif menu == "割当・返却・廃棄":
    show_device_status()
elif menu == "ライセンス":
    show_licenses()
elif menu == "申請の承認":
    show_request_review(user)
elif menu == "自分の資産":
    show_my_assets(user["id"])
elif menu == "申請":
    show_my_requests(user)
else:
    st.info("この画面はまだ準備中です。")
