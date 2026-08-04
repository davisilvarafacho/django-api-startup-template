"""Encrypted Django model fields and the :func:`encrypt` wrapper that builds them."""

from django.db import models

from internal_frameworks.sensitive_fields import cipher, serialization


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
        plaintext = serialization.serialize(value, self._sensitive_kind)
        return cipher.encrypt(plaintext)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        plaintext = cipher.decrypt(value)
        return serialization.deserialize(plaintext, self._sensitive_kind)

    def deconstruct(self):
        base_class = getattr(self, "_sensitive_base_class", self.__class__.__mro__[2])
        name, _, args, kwargs = base_class.deconstruct(self)
        field = base_class(*args, **kwargs)
        return name, "internal_frameworks.sensitive_fields.fields.encrypt", [field], {}

    def clone(self):
        """Clone through the public wrapper instead of the dynamic field class."""
        _, _, args, _ = self.deconstruct()
        return encrypt(args[0])

    def is_encrypted_with_active_key(self, value):
        """Return whether a stored token can be decrypted by the write key."""
        return cipher.is_encrypted_with_active_key(value)


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
    field._sensitive_kind = serialization.JSON if isinstance(field, models.JSONField) else serialization.TEXT
    return field


def is_encrypted_field(field):
    """Return whether ``field`` was created by :func:`encrypt`."""
    return bool(getattr(field, "is_sensitive_field", False))
