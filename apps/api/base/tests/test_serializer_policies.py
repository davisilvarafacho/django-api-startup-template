from apps.usuarios.models import Usuario


def test_model_protege_todos_os_campos_controlados_pela_aplicacao():
    assert Usuario.get_forbidden_internal_write_fields() == [
        "created_at",
        "created_by",
        "last_modified_at",
        "organizacao",
    ]


def test_model_inclui_campos_forbidden_extras(monkeypatch):
    monkeypatch.setattr(Usuario, "extra_forbidden_internal_write_fields", ["first_name"])

    assert Usuario.get_forbidden_internal_write_fields() == [
        "created_at",
        "created_by",
        "last_modified_at",
        "organizacao",
        "first_name",
    ]
