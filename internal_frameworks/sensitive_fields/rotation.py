"""Batch re-encryption of values still written with a retired key."""

from dataclasses import dataclass

from django.apps import apps
from django.db import connection

from internal_frameworks.sensitive_fields.fields import is_encrypted_field


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
