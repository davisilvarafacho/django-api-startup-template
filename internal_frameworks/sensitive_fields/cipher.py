"""Fernet encryption and decryption on top of the configured keyring."""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from internal_frameworks.sensitive_fields.exceptions import SensitiveFieldDecryptionError
from internal_frameworks.sensitive_fields.keyring import get_sensitive_field_keys


def encrypt(plaintext: str) -> str:
    """Return the token produced by the write key (the first of the keyring)."""
    return _multi_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Return the plaintext behind ``token``, trying every key of the keyring."""
    try:
        return _multi_fernet().decrypt(token.encode()).decode()
    except (InvalidToken, UnicodeError) as exc:
        raise SensitiveFieldDecryptionError("Não foi possível decifrar campo sensível.") from exc


def is_encrypted_with_active_key(token: str) -> bool:
    """Return whether a stored token can be decrypted by the write key."""
    try:
        Fernet(get_sensitive_field_keys(require_configured=True)[0]).decrypt(token.encode())
    except InvalidToken:
        return False
    return True


def _multi_fernet() -> MultiFernet:
    return MultiFernet([Fernet(key) for key in get_sensitive_field_keys(require_configured=True)])
