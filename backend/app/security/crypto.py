"""Encryption at rest for stored secrets (database passwords, API keys).

Fernet (AES-128-CBC + HMAC-SHA256) keyed from ENCRYPTION_KEY. Changing ENCRYPTION_KEY makes
existing secrets unreadable: they then have to be entered again.
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretUnreadable(ValueError):
    pass


@lru_cache
def _fernet(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(plain: str, *, key: str | None = None) -> str:
    return _fernet(key or get_settings().encryption_key).encrypt(plain.encode()).decode()


def decrypt(token: str, *, key: str | None = None) -> str:
    try:
        return _fernet(key or get_settings().encryption_key).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnreadable(
            "the stored password can't be decrypted (ENCRYPTION_KEY changed?); enter it again"
        ) from exc
