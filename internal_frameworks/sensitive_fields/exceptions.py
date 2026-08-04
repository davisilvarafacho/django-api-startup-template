"""Errors raised by the sensitive-fields framework."""


class SensitiveFieldDecryptionError(ValueError):
    """Raised when a sensitive value cannot be safely decrypted."""
