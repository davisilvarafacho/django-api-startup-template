from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.translation import gettext_lazy as _

import pytest

from apps.api.core.errors import (
    APIError,
    APIErrorItem,
    ErrorCodeRegistry,
    ValidationErrorCode,
    build_error_item,
    discover_error_codes,
    error_codes,
)


class ExampleErrorCode(models.TextChoices):
    INVALID = "example.invalid", _("Valor inválido.")


class NotTextChoices(models.IntegerChoices):
    ONE = 1, "Um"


def test_api_error_usa_valor_e_label_do_textchoices():
    error = APIError(ExampleErrorCode.INVALID, status_code=422)

    assert error.code == "example.invalid"
    assert str(error.message) == "Valor inválido."
    assert error.status_code == 422


def test_api_error_aceita_mensagem_campo_path_e_contexto_customizados():
    error = APIError(
        ExampleErrorCode.INVALID,
        status_code=422,
        message="Mensagem específica.",
        field="email",
        path=("members", 0, "email"),
        context={"max_length": 10},
    )

    item = error.as_item()

    assert item.message == "Mensagem específica."
    assert item.field == "email"
    assert item.path == ("members", 0, "email")
    assert item.context == {"max_length": 10}


def test_api_error_rejeita_string_solta():
    with pytest.raises(ImproperlyConfigured):
        APIError("example.invalid", status_code=422)


def test_api_error_rejeita_choices_que_nao_seja_textchoices():
    with pytest.raises(ImproperlyConfigured):
        APIError(NotTextChoices.ONE, status_code=422)


def test_build_error_item_usa_label_quando_mensagem_nao_informada():
    item = build_error_item(ExampleErrorCode.INVALID)

    assert item == APIErrorItem(code="example.invalid", message="Valor inválido.")


def test_registry_rejeita_codigo_duplicado():
    registry = ErrorCodeRegistry()
    registry.register(ExampleErrorCode)

    with pytest.raises(ImproperlyConfigured, match="example.invalid"):
        registry.register(ExampleErrorCode)


def test_registry_rejeita_formato_invalido():
    class CodigoInvalido(models.TextChoices):
        RUIM = "Invalido", "Ruim"

    registry = ErrorCodeRegistry()

    with pytest.raises(ImproperlyConfigured, match="formato"):
        registry.register(CodigoInvalido)


def test_registry_rejeita_classe_que_nao_e_textchoices():
    registry = ErrorCodeRegistry()

    with pytest.raises(ImproperlyConfigured):
        registry.register(NotTextChoices)


def test_registry_lookup_devolve_o_membro():
    registry = ErrorCodeRegistry()
    registry.register(ExampleErrorCode)

    assert registry.lookup("example.invalid") is ExampleErrorCode.INVALID
    assert registry.lookup("nao.existe") is None


def test_discover_error_codes_encontra_enums_dos_apps_instalados():
    discover_error_codes(force=True)

    assert error_codes.lookup("auth.invalid_token") is not None
    assert error_codes.lookup("validation.required") is ValidationErrorCode.REQUIRED

    # Idempotente: chamar de novo não deve levantar por duplicidade.
    discover_error_codes()


def test_check_error_code_registry_passa_sem_inconsistencias():
    discover_error_codes(force=True)

    assert error_codes.check() == []
