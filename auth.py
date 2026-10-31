"""パスワードのハッシュ化とログインの確認を担当するモジュール。"""
import hashlib
import hmac
import secrets
import string

import db

# 変換を繰り返す回数。多いほど総当たり攻撃に時間がかかる
ITERATIONS = 600_000

PASSWORD_LENGTH = 12
PASSWORD_CHARS = string.ascii_letters + string.digits


def make_password():
    """推測されにくいランダムなパスワードを作る（secrets は暗号用の乱数を使う）。"""
    return "".join(secrets.choice(PASSWORD_CHARS) for _ in range(PASSWORD_LENGTH))


def hash_password(password):
    """パスワードを元に戻せない文字列に変換する。

    保存形式は「方式$繰り返し回数$ソルト$ハッシュ値」。
    回数とソルトも一緒に保存しておくことで、照合時に同じ変換をやり直せる。
    """
    salt = secrets.token_bytes(16)  # 社員ごとに違うランダムな値（ソルト）
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    """入力されたパスワードが、保存してあるハッシュと一致するかを確かめる。"""
    try:
        method, iterations, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False  # 形式が違う（未設定など）ときは、一致しないものとして扱う
    if method != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    # == ではなく compare_digest を使うと、比較にかかる時間から中身を推測されにくい
    return hmac.compare_digest(digest.hex(), digest_hex)


def authenticate(employee_code, password, db_path=db.DB_PATH):
    """社員番号とパスワードが正しく、在籍中ならその社員の情報を返す。違えば None を返す。"""
    conn = db.get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, employee_code, name, department, role, password_hash"
            " FROM employees WHERE employee_code = ? AND is_active = 1",
            (employee_code,),
        ).fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(password, row["password_hash"]):
        return None
    user = dict(row)
    del user["password_hash"]  # 画面側にハッシュを渡さない
    return user
