"""Logging helpers that keep audit-log registration safe by default."""

from django.conf import settings

from auditlog.registry import auditlog

from internal_frameworks.sensitive_fields.fields import is_encrypted_field


def _unique(values):
    return list(dict.fromkeys(values))


def _automatic_excluded_fields(model):
    internal_fields = getattr(model, "internal_fields", [])
    extra_internal_fields = getattr(model, "extra_internal_fields", [])
    sensitive_fields = [field.name for field in model._meta.fields if is_encrypted_field(field)]
    configured_fields = getattr(settings, "BASE_AUDITLOG_EXCLUDE_FIELDS", [])
    return _unique([*configured_fields, *internal_fields, *extra_internal_fields, *sensitive_fields])


def register(
    model=None,
    include_fields=None,
    exclude_fields=None,
    mapping_fields=None,
    mask_fields=None,
    mask_callable=None,
    m2m_fields=None,
    serialize_data=False,
    serialize_kwargs=None,
    serialize_auditlog_fields_only=False,
):
    """Register a model with auditlog, excluding internal and sensitive fields."""

    def decorator(cls):
        return auditlog.register(
            cls,
            include_fields=include_fields,
            exclude_fields=_unique([*_automatic_excluded_fields(cls), *(exclude_fields or [])]),
            mapping_fields=mapping_fields,
            mask_fields=mask_fields,
            mask_callable=mask_callable,
            m2m_fields=m2m_fields,
            serialize_data=serialize_data,
            serialize_kwargs=serialize_kwargs,
            serialize_auditlog_fields_only=serialize_auditlog_fields_only,
        )

    return decorator if model is None else decorator(model)
