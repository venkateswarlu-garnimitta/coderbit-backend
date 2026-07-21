"""Symmetric encryption for sensitive DB columns (e.g. auth_token).

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the cryptography package.
The key is derived from TOKEN_ENCRYPTION_KEY in the environment.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    raw_key = os.getenv("TOKEN_ENCRYPTION_KEY", "")
    if not raw_key:
        raise RuntimeError(
            "TOKEN_ENCRYPTION_KEY must be set to encrypt auth tokens at rest. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    # Accept either a raw Fernet key (44-char base64url) or an arbitrary
    # passphrase — derive a 32-byte key via SHA-256 in the latter case.
    try:
        key_bytes = base64.urlsafe_b64decode(raw_key + "==")
        if len(key_bytes) == 32:
            fernet_key = base64.urlsafe_b64encode(key_bytes)
        else:
            raise ValueError("not 32 bytes")
    except Exception:
        derived = hashlib.sha256(raw_key.encode()).digest()
        fernet_key = base64.urlsafe_b64encode(derived)

    _fernet = Fernet(fernet_key)
    return _fernet


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string; returns a base64url-encoded ciphertext string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a token string previously encrypted with encrypt_token."""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("auth_token decryption failed — wrong key or corrupted data") from exc
