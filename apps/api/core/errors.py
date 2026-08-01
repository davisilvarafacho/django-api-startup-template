"""Base única de erros da API.

Todo código de erro é um valor de `models.TextChoices` registrado em
`error_codes`. `APIError` é a exceção que apps de domínio devem levantar;
`api_exception_handler` converge qualquer exceção (DRF, Django ou inesperada)
para o mesmo envelope `{"errors": [...], "request_id": ...}`.

Ver a spec normativa em `docs/superpowers/specs/2026-07-28-api-errors-design.md`.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from dataclasses import field as dataclass_field
from importlib import import_module

from django.apps import apps
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from django.http import Http404, JsonResponse
from django.utils.translation import gettext_lazy as _

from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response

from .request_id import get_request_id

logger = logging.getLogger("api.errors")

# `dominio.erro`, sempre em inglês, minúsculo e com `_` como separador interno.
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
REDACTED_CONTEXT_VALUE = "[REDACTED]"
SENSITIVE_CONTEXT_KEY_PARTS = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "digest",
    "password",
    "secret",
    "senha",
    "token",
)


class CoreErrorCode(models.TextChoices):
    BAD_REQUEST = "core.bad_request", _("Requisição inválida.")
    NOT_FOUND = "core.not_found", _("Recurso não encontrado.")
    CONFLICT = "core.conflict", _("Conflito de estado.")
    THROTTLED = "core.throttled", _("Muitas requisições. Tente novamente mais tarde.")
    INTERNAL_ERROR = "core.internal_error", _("Erro interno.")


class ValidationErrorCode(models.TextChoices):
    REQUIRED = "validation.required", _("Este campo é obrigatório.")
    INVALID = "validation.invalid", _("Valor inválido.")
    MALFORMED = "validation.malformed", _("Requisição malformada.")


@dataclass(frozen=True)
class APIErrorItem:
    """Um erro individual dentro do envelope `errors`."""

    code: str
    message: str
    field: str | None = None
    path: tuple | None = None
    context: Mapping = dataclass_field(default_factory=dict)


class ErrorCodeRegistry:
    """Registro central dos códigos de erro válidos na aplicação."""

    def __init__(self):
        self._codes: dict[str, tuple[type, models.Choices]] = {}
        self.discovered = False

    def register(self, enum_cls):
        if not (isinstance(enum_cls, type) and issubclass(enum_cls, models.TextChoices)):
            raise ImproperlyConfigured(f"{enum_cls!r} não é uma subclasse de models.TextChoices.")

        for member in enum_cls:
            code = member.value
            if not CODE_PATTERN.match(code):
                raise ImproperlyConfigured(f"Código de erro '{code}' inválido: use o formato 'dominio.erro'.")
            if code in self._codes:
                raise ImproperlyConfigured(f"Código de erro duplicado: '{code}'.")
            self._codes[code] = (enum_cls, member)

    def lookup(self, code):
        entry = self._codes.get(code)
        return entry[1] if entry else None

    def all_codes(self):
        return dict(self._codes)

    def reset(self):
        self._codes.clear()
        self.discovered = False

    def check(self):
        """Usado pelo system check: reafirma o formato de todo código registrado."""
        errors = []
        for code in self._codes:
            if not CODE_PATTERN.match(code):
                errors.append(
                    Error(
                        f"Código de erro '{code}' não segue o formato 'dominio.erro'.",
                        id="api.errors.E001",
                    )
                )
        return errors


error_codes = ErrorCodeRegistry()


def discover_error_codes(registry=None, *, force=False):
    """Importa `<app>.errors` de cada app instalado e registra seus `TextChoices`.

    Idempotente: chamado a partir de `CoreConfig.ready()`, mas também pode ser
    invocado livremente em testes sem re-registrar (e falhar por) os mesmos
    códigos.
    """
    registry = registry or error_codes

    if registry.discovered and not force:
        return registry

    if force:
        registry.reset()

    for app_config in apps.get_app_configs():
        module_name = f"{app_config.name}.errors"
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue

        for name in dir(module):
            obj = getattr(module, name)
            is_local_text_choices = (
                isinstance(obj, type) and issubclass(obj, models.TextChoices) and obj is not models.TextChoices and obj.__module__ == module.__name__
            )
            if is_local_text_choices:
                registry.register(obj)

    registry.discovered = True
    return registry


@register()
def check_error_code_registry(app_configs, **kwargs):
    return error_codes.check()


def _is_registered_choice(code):
    return isinstance(code, models.TextChoices) and error_codes.lookup(code.value) is code


def _is_sensitive_context_key(key):
    normalized = str(key).casefold().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_CONTEXT_KEY_PARTS)


def sanitize_error_context(value):
    """Remove segredos de estruturas que serão devolvidas no envelope público."""
    if isinstance(value, Mapping):
        return {key: (REDACTED_CONTEXT_VALUE if _is_sensitive_context_key(key) else sanitize_error_context(item)) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [sanitize_error_context(item) for item in value]

    return value


class APIError(APIException):
    """Exceção para erros deliberados da aplicação.

    `code` precisa ser um membro de `models.TextChoices` (nunca uma string
    solta), garantindo que todo erro levantado tenha um código estável e
    documentável.
    """

    def __init__(self, code, *, status_code, message=None, field=None, path=None, context=None):
        if not _is_registered_choice(code):
            raise ImproperlyConfigured(f"{code!r} precisa ser um membro de models.TextChoices registrado em error_codes.")

        self.code = code.value
        self.message = message if message is not None else code.label
        self.field = field
        self.path = tuple(path) if path else None
        self.context = sanitize_error_context(dict(context)) if context else {}
        self.status_code = status_code

        super().__init__(detail=self.message, code=self.code)

    def as_item(self):
        return APIErrorItem(
            code=self.code,
            message=str(self.message),
            field=self.field,
            path=self.path,
            context=self.context,
        )


def build_error_item(code, *, message=None, field=None, path=None, context=None):
    if not _is_registered_choice(code):
        raise ImproperlyConfigured(f"{code!r} precisa ser um membro de models.TextChoices registrado em error_codes.")

    return APIErrorItem(
        code=code.value,
        message=str(message) if message is not None else str(code.label),
        field=field,
        path=tuple(path) if path else None,
        context=sanitize_error_context(dict(context)) if context else {},
    )


def build_error_payload(items, *, request_id=None):
    serialized_items = []
    for item in items:
        serialized = asdict(item)
        serialized["context"] = sanitize_error_context(serialized["context"])
        serialized_items.append(serialized)

    return {
        "errors": serialized_items,
        "request_id": request_id if request_id is not None else get_request_id(),
    }


def error_response(code, *, status_code, message=None, field=None, path=None, context=None):
    """Constrói a resposta de erro fora do ciclo do DRF (middleware, handlers Django)."""
    item = build_error_item(code, message=message, field=field, path=path, context=context)
    return JsonResponse(build_error_payload([item]), status=status_code)


def error_response_for_api_error(exc):
    """Constrói a resposta a partir de um `APIError` já levantado fora do DRF."""
    return JsonResponse(build_error_payload([exc.as_item()]), status=exc.status_code)


def flatten_validation_errors(detail, path=()):
    """Achata o `detail` de um `ValidationError` (DRF ou Django) em `APIErrorItem`s.

    `field` é o último componente textual do caminho (o nome do campo mais
    próximo do erro); `path` é o caminho completo, incluindo índices de lista.
    """
    items: list[APIErrorItem] = []

    if isinstance(detail, dict):
        for key, value in detail.items():
            items.extend(flatten_validation_errors(value, (*path, key)))
    elif isinstance(detail, list):
        for index, value in enumerate(detail):
            if isinstance(value, (dict, list)):
                items.extend(flatten_validation_errors(value, (*path, index)))
            else:
                items.append(_validation_item(value, path))
    else:
        items.append(_validation_item(detail, path))

    return items


def _validation_item(detail, path):
    drf_code = getattr(detail, "code", None)
    validation_code = ValidationErrorCode.REQUIRED if drf_code == "required" else ValidationErrorCode.INVALID
    field_name = next((part for part in reversed(path) if isinstance(part, str)), None)

    return APIErrorItem(
        code=validation_code.value,
        message=str(detail),
        field=field_name,
        path=tuple(path) if path else None,
        context={},
    )


def _django_validation_detail(exc):
    if hasattr(exc, "error_dict"):
        return {key: [str(message) for message in messages] for key, messages in exc.message_dict.items()}
    return list(exc.messages)


def api_exception_handler(exc, context):
    """Exception handler único do DRF: todo erro converge para o mesmo envelope."""
    from apps.api.autenticacao.errors import AuthErrorCode

    headers = {}

    if isinstance(exc, APIError):
        items = [exc.as_item()]
        status_code = exc.status_code

    elif isinstance(exc, drf_exceptions.ValidationError):
        items = flatten_validation_errors(exc.detail)
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    elif isinstance(exc, DjangoValidationError):
        items = flatten_validation_errors(_django_validation_detail(exc))
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY

    elif isinstance(exc, drf_exceptions.Throttled):
        wait_context = {"retry_after": exc.wait} if exc.wait is not None else {}
        items = [build_error_item(CoreErrorCode.THROTTLED, context=wait_context)]
        status_code = status.HTTP_429_TOO_MANY_REQUESTS

    elif isinstance(exc, drf_exceptions.NotAuthenticated):
        items = [build_error_item(AuthErrorCode.NOT_AUTHENTICATED)]
        status_code = status.HTTP_401_UNAUTHORIZED
        headers.update(_auth_headers(exc, context))

    elif isinstance(exc, drf_exceptions.AuthenticationFailed):
        items = [build_error_item(AuthErrorCode.INVALID_TOKEN, message=str(exc.detail))]
        status_code = status.HTTP_401_UNAUTHORIZED
        headers.update(_auth_headers(exc, context))

    elif isinstance(exc, drf_exceptions.PermissionDenied):
        items = [build_error_item(AuthErrorCode.PERMISSION_DENIED)]
        status_code = status.HTTP_403_FORBIDDEN

    elif isinstance(exc, (drf_exceptions.NotFound, Http404)):
        items = [build_error_item(CoreErrorCode.NOT_FOUND)]
        status_code = status.HTTP_404_NOT_FOUND

    elif isinstance(exc, drf_exceptions.ParseError):
        items = [build_error_item(ValidationErrorCode.MALFORMED)]
        status_code = status.HTTP_400_BAD_REQUEST

    elif isinstance(exc, DjangoPermissionDenied):
        items = [build_error_item(AuthErrorCode.PERMISSION_DENIED)]
        status_code = status.HTTP_403_FORBIDDEN

    elif isinstance(exc, drf_exceptions.APIException):
        items = [build_error_item(CoreErrorCode.BAD_REQUEST, message=str(exc.detail))]
        status_code = exc.status_code

    else:
        logger.exception("Erro interno não tratado.", exc_info=exc)
        items = [build_error_item(CoreErrorCode.INTERNAL_ERROR)]
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    response = Response(build_error_payload(items), status=status_code)
    for name, value in headers.items():
        response[name] = value

    return response


def _auth_headers(exc, context):
    view = context.get("view")
    request = context.get("request")
    if view is None or request is None:
        return {}

    auth_header = view.get_authenticate_header(request)
    if not auth_header:
        return {}

    return {"WWW-Authenticate": auth_header}
