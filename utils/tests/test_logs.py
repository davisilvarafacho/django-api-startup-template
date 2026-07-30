"""Tests for the audit-log registration wrapper."""

from unittest.mock import patch

from utils.logs import register
from utils.tests.test_sensitive_fields import RegistroSensivel


@patch("utils.logs.auditlog.register")
def test_register_exclui_campos_internos_e_cifrados_sem_mutar_argumento(audit_register):
    supplied = ["manual"]

    register(RegistroSensivel, exclude_fields=supplied, serialize_data=True)

    assert supplied == ["manual"]
    assert audit_register.call_args.kwargs["exclude_fields"] == [
        "data_ultima_alteracao",
        "hora_ultima_alteracao",
        "data_criacao",
        "hora_criacao",
        "is_deleted",
        "documento",
        "anotacoes",
        "dados",
        "manual",
    ]
    assert audit_register.call_args.kwargs["serialize_data"] is True


@patch("utils.logs.auditlog.register")
def test_register_encaminha_todos_os_parametros(audit_register):
    register(
        RegistroSensivel,
        include_fields=["documento"],
        mapping_fields={"documento": "Documento"},
        mask_fields=["manual"],
        mask_callable="utils.tests.test_logs.mask",
        m2m_fields={"tags"},
        serialize_data=True,
        serialize_kwargs={"fields": ["documento"]},
        serialize_auditlog_fields_only=True,
    )

    kwargs = audit_register.call_args.kwargs
    assert kwargs["include_fields"] == ["documento"]
    assert kwargs["mapping_fields"] == {"documento": "Documento"}
    assert kwargs["mask_fields"] == ["manual"]
    assert kwargs["mask_callable"] == "utils.tests.test_logs.mask"
    assert kwargs["m2m_fields"] == {"tags"}
    assert kwargs["serialize_kwargs"] == {"fields": ["documento"]}
    assert kwargs["serialize_auditlog_fields_only"] is True
