from datetime import UTC, datetime

from rest_framework import serializers

import pytest

from apps.api.base.serializers import (
    BaseModelSerializer,
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
)
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


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


class UsuarioForbiddenSerializer(BaseModelSerializer):
    created_at = serializers.DateTimeField(required=False)
    created_by = serializers.PrimaryKeyRelatedField(
        queryset=Usuario.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name", "last_name", "created_at", "created_by"]


class UsuarioForbiddenAliasSerializer(BaseModelSerializer):
    autor = serializers.PrimaryKeyRelatedField(
        source="created_by",
        queryset=Usuario.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name", "last_name", "autor"]


class UsuarioInjectingForbiddenSerializer(UsuarioForbiddenSerializer):
    def validate(self, attrs):
        attrs["created_by"] = self.context["atacante"]
        attrs["created_at"] = datetime(2000, 1, 1, tzinfo=UTC)
        return attrs


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


@pytest.mark.django_db
def test_create_descarta_forbidden_sem_mutar_payload():
    atacante = criar_usuario()
    instante_forjado = datetime(2000, 1, 1, tzinfo=UTC)
    payload = {
        "email": f"novo-{atacante.pk}@exemplo.com",
        "first_name": "Nome",
        "last_name": "Criado",
        "created_at": instante_forjado.isoformat(),
        "created_by": atacante.pk,
    }
    payload_original = payload.copy()
    serializer = UsuarioForbiddenSerializer(
        data=payload,
        ignore_read_only=["created_at", "created_by"],
    )

    assert serializer.is_valid(), serializer.errors
    assert "created_at" not in serializer.validated_data
    assert "created_by" not in serializer.validated_data

    usuario = serializer.save()

    assert usuario.created_at != instante_forjado
    assert usuario.created_by_id is None
    assert payload == payload_original


@pytest.mark.django_db
@pytest.mark.parametrize("partial", [False, True])
def test_update_descarta_forbidden_e_preserva_campos_normais(partial):
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    created_at_original = usuario.created_at
    payload = {
        "email": usuario.email,
        "first_name": "Nome alterado",
        "last_name": usuario.last_name,
        "created_at": datetime(2000, 1, 1, tzinfo=UTC).isoformat(),
        "created_by": atacante.pk,
    }
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data=payload,
        partial=partial,
        ignore_read_only=["created_at", "created_by"],
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save()
    usuario.refresh_from_db()

    assert usuario.first_name == "Nome alterado"
    assert usuario.created_at == created_at_original
    assert usuario.created_by_id == criador.pk


@pytest.mark.django_db
def test_payload_descarta_alias_com_source_forbidden():
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    serializer = UsuarioForbiddenAliasSerializer(
        usuario,
        data={"autor": atacante.pk, "first_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    assert "created_by" not in serializer.validated_data

    serializer.save()
    usuario.refresh_from_db()

    assert usuario.created_by_id == criador.pk
    assert usuario.first_name == "Permitido"


@pytest.mark.django_db
def test_payload_descarta_extra_forbidden_do_model(monkeypatch):
    monkeypatch.setattr(Usuario, "extra_forbidden_internal_write_fields", ["first_name"])
    usuario = criar_usuario(first_name="Original", last_name="Original")
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data={"first_name": "Bloqueado", "last_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    assert "first_name" not in serializer.validated_data
    assert serializer.validated_data["last_name"] == "Permitido"

    serializer.save()
    usuario.refresh_from_db()

    assert usuario.first_name == "Original"
    assert usuario.last_name == "Permitido"


@pytest.mark.django_db
def test_save_descarta_kwargs_forbidden_inclusive_attname():
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    created_at_original = usuario.created_at
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data={"last_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save(
        created_by=atacante,
        created_by_id=atacante.pk,
        created_at=datetime(2000, 1, 1, tzinfo=UTC),
    )
    usuario.refresh_from_db()

    assert usuario.last_name == "Permitido"
    assert usuario.created_by_id == criador.pk
    assert usuario.created_at == created_at_original


@pytest.mark.django_db
def test_save_descarta_kwarg_extra_forbidden(monkeypatch):
    monkeypatch.setattr(Usuario, "extra_forbidden_internal_write_fields", ["first_name"])
    usuario = criar_usuario(first_name="Original")
    serializer = UsuarioForbiddenSerializer(
        usuario,
        data={"last_name": "Permitido"},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save(first_name="Bloqueado")
    usuario.refresh_from_db()

    assert usuario.first_name == "Original"
    assert usuario.last_name == "Permitido"


@pytest.mark.django_db
def test_save_remove_forbidden_injetado_por_validacao_customizada():
    criador = criar_usuario()
    atacante = criar_usuario()
    usuario = criar_usuario(created_by=criador)
    created_at_original = usuario.created_at
    serializer = UsuarioInjectingForbiddenSerializer(
        usuario,
        data={"last_name": "Permitido"},
        partial=True,
        context={"atacante": atacante},
    )

    assert serializer.is_valid(), serializer.errors
    serializer.save()
    usuario.refresh_from_db()

    assert usuario.created_by_id == criador.pk
    assert usuario.created_at == created_at_original
