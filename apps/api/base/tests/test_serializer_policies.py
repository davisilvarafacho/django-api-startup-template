from rest_framework import serializers

from apps.api.base.serializers import (
    BaseModelSerializer,
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
)
from apps.usuarios.models import Usuario


class UsuarioFieldPolicySerializer(BaseModelSerializer):
    created_at = serializers.DateTimeField(required=False)

    class Meta:
        model = Usuario
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "password",
            "created_at",
            "last_modified_at",
        ]


def test_base_serializer_compoe_os_mixins_de_politica():
    assert issubclass(BaseModelSerializer, InternalFieldsSerializerMixin)
    assert issubclass(BaseModelSerializer, ReadOnlyFieldsSerializerMixin)
    assert issubclass(BaseModelSerializer, WriteOnlyFieldsSerializerMixin)


def test_mixins_aplicam_internal_read_only_e_write_only():
    serializer = UsuarioFieldPolicySerializer()

    assert "last_modified_at" not in serializer.fields
    assert serializer.fields["created_at"].read_only is True
    assert serializer.fields["password"].write_only is True


def test_mixins_preservam_opcoes_ignore_existentes():
    serializer = UsuarioFieldPolicySerializer(
        ignore_internal=["last_modified_at"],
        ignore_read_only=["created_at"],
    )

    assert "last_modified_at" in serializer.fields
    assert serializer.fields["created_at"].read_only is False


def test_mixins_preservam_opcoes_additional_existentes():
    serializer = UsuarioFieldPolicySerializer(
        additional_internal=["last_name"],
        additional_read_only=["email"],
        additional_write_only=["first_name"],
    )

    assert "last_name" not in serializer.fields
    assert serializer.fields["email"].read_only is True
    assert serializer.fields["first_name"].write_only is True


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
