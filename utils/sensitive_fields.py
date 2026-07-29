"""Encrypted model fields and their keyring helpers."""

import json
import os
from dataclasses import dataclass

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


def get_sensitive_field_keys(*, require_configured: bool = False) -> list[bytes]:
    """Return the ordered Fernet keyring configured in the environment."""
    keys = [value.strip().encode() for value in os.environ.get("SENSITIVE_FIELD_KEYS", "").split(",") if value.strip()]
    if require_configured and not keys:
        raise ImproperlyConfigured("SENSITIVE_FIELD_KEYS deve conter ao menos uma chave Fernet.")
    return keys


class SensitiveFieldDecryptionError(ValueError):
    """Raised when a sensitive value cannot be safely decrypted."""


class EncryptedFieldMixin:
    """Persistence behavior shared by fields returned from :func:`encrypt`."""

    is_sensitive_field = True

    def contribute_to_class(self, cls, name, private_only=False):
        super().contribute_to_class(cls, name, private_only=private_only)
        if hasattr(cls, "extra_write_only_fields"):
            cls.extra_write_only_fields = [*cls.extra_write_only_fields, *([] if name in cls.extra_write_only_fields else [name])]

    def db_type(self, connection):
        """Store every encrypted token in a text column."""
        return "text"

    def get_prep_value(self, value):
        if value is None:
            return None
        plaintext = self._serialize(value)
        return self._get_fernet().encrypt(plaintext.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        try:
            plaintext = self._get_fernet().decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeError) as exc:
            raise SensitiveFieldDecryptionError("Não foi possível decifrar campo sensível.") from exc
        return self._deserialize(plaintext)

    def deconstruct(self):
        name, _, args, kwargs = self._sensitive_base_class.deconstruct(self)
        field = self._sensitive_base_class(*args, **kwargs)
        return name, "utils.sensitive_fields.encrypt", [field], {}

    def _get_fernet(self):
        return MultiFernet([Fernet(key) for key in get_sensitive_field_keys(require_configured=True)])

    def is_encrypted_with_active_key(self, value):
        """Return whether a stored token can be decrypted by the write key."""
        try:
            Fernet(get_sensitive_field_keys(require_configured=True)[0]).decrypt(value.encode())
        except InvalidToken:
            return False
        return True

    def _serialize(self, value):
        if self._sensitive_kind == "json":
            return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        return str(value)

    def _deserialize(self, value):
        if self._sensitive_kind == "json":
            return json.loads(value)
        return value


def encrypt(field):
    """Wrap a Django field so its database representation is Fernet encrypted."""
    if not isinstance(field, (models.CharField, models.TextField, models.JSONField)):
        raise TypeError("encrypt() aceita apenas CharField, TextField ou JSONField.")
    if field.unique or field.db_index:
        raise ValueError("Campos cifrados não suportam unique ou db_index.")

    base_class = field.__class__
    encrypted_class = type(f"Encrypted{base_class.__name__}", (EncryptedFieldMixin, base_class), {})
    field.__class__ = encrypted_class
    field._sensitive_base_class = base_class
    field._sensitive_kind = "json" if isinstance(field, models.JSONField) else "text"
    return field


def is_encrypted_field(field):
    """Return whether ``field`` was created by :func:`encrypt`."""
    return bool(getattr(field, "is_sensitive_field", False))


@dataclass(frozen=True)
class RotationResult:
    """Counters produced by one sensitive-field rotation run."""

    updated: int = 0


def rotate_sensitive_fields(*, batch_size=500):
    """Re-encrypt values written with an old key using bulk updates only."""
    if batch_size <= 0:
        raise ValueError("batch_size deve ser maior que zero.")

    updated = 0
    for model in apps.get_models():
        for field in (item for item in model._meta.local_fields if is_encrypted_field(item)):
            quoted_table = connection.ops.quote_name(model._meta.db_table)
            quoted_pk = connection.ops.quote_name(model._meta.pk.column)
            quoted_column = connection.ops.quote_name(field.column)
            last_pk = None
            while True:
                where = "" if last_pk is None else f" WHERE {quoted_pk} > %s"
                params = [] if last_pk is None else [last_pk]
                params.append(batch_size)
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {quoted_pk}, {quoted_column} FROM {quoted_table}{where} ORDER BY {quoted_pk} LIMIT %s", params)
                    rows = cursor.fetchall()
                if not rows:
                    break
                changes = []
                for pk, ciphertext in rows:
                    last_pk = pk
                    if ciphertext is None or field.is_encrypted_with_active_key(ciphertext):
                        continue
                    instance = model(pk=pk)
                    setattr(instance, field.attname, field.from_db_value(ciphertext, None, connection))
                    changes.append(instance)
                if changes:
                    model._default_manager.bulk_update(changes, [field.name], batch_size=batch_size)
                    updated += len(changes)
    return RotationResult(updated=updated)
