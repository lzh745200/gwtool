# -*- coding: utf-8 -*-
"""资料库口令锁（PBKDF2）。

用途：防止他人随手翻阅本机资料库（防误用级防护，非涉密级加密）。
存储：settings 表 "lock_hash" = "iterations$salt_hex$hash_hex"。
"""
from __future__ import annotations

import hashlib
import secrets

from ..db import dao

_ITER = 120_000
KEY = "lock_hash"


def has_password() -> bool:
    return bool(dao.get_setting(KEY, ""))


def set_password(password: str) -> None:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                            bytes.fromhex(salt), _ITER).hex()
    dao.set_setting(KEY, f"{_ITER}${salt}${h}")


def clear_password() -> None:
    dao.set_setting(KEY, "")


def verify_password(password: str) -> bool:
    stored = dao.get_setting(KEY, "")
    if not stored:
        return True
    try:
        iters, salt, expect = stored.split("$")
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                bytes.fromhex(salt), int(iters)).hex()
        import hmac
        return hmac.compare_digest(h, expect)
    except Exception:
        return False
