"""9x Security - Local app login (salted PBKDF2, no external service)."""
import binascii
import hashlib
import hmac
import os

_ITER = 200_000


def hash_password(password, salt=None):
    """Return (salt_hex, hash_hex)."""
    if salt is None:
        salt = os.urandom(16)
    elif isinstance(salt, str):
        salt = binascii.unhexlify(salt)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITER)
    return binascii.hexlify(salt).decode(), binascii.hexlify(dk).decode()


def verify_password(password, salt_hex, hash_hex):
    if not salt_hex or not hash_hex:
        return False
    try:
        _, h = hash_password(password, salt_hex)
    except Exception:
        return False
    return hmac.compare_digest(h, hash_hex)
