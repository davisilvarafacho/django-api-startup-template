"""Extensão mínima do drf-spectacular para o contrato de erros da API.

Não duplica o registry: os códigos documentados por operação são validados
contra `error_codes`, a mesma fonte usada em runtime pelo exception handler.
"""

from django.core.exceptions import ImproperlyConfigured

from rest_framework import serializers

from drf_spectacular.utils import OpenApiResponse, extend_schema

from .deprecation import DeprecationAwareAutoSchema
from .errors import error_codes


class APIErrorItemSchema(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    field = serializers.CharField(allow_null=True, required=False)
    path = serializers.ListField(child=serializers.JSONField(), allow_null=True, required=False)
    context = serializers.DictField(required=False)


class APIErrorResponseSchema(serializers.Serializer):
    errors = APIErrorItemSchema(many=True)
    request_id = serializers.CharField()


def document_error_codes(*codes):
    """Monta a resposta OpenAPI para um status a partir de códigos já registrados."""
    descriptions = []
    for code in codes:
        if error_codes.lookup(code.value) is not code:
            raise ImproperlyConfigured(f"{code!r} precisa estar registrado em apps.api.core.errors.error_codes.")
        descriptions.append(f"`{code.value}`: {code.label}")

    return OpenApiResponse(response=APIErrorResponseSchema, description="\n".join(descriptions))


def document_error_responses(mapping, **extend_schema_kwargs):
    """Decorator: `{status_code: [codes...]}` -> `extend_schema(responses=...)`.

    Uso:

        @document_error_responses({401: [AuthErrorCode.NOT_AUTHENTICATED], 422: [...]})
        def get(self, request):
            ...
    """
    responses = {status_code: document_error_codes(*codes) for status_code, codes in mapping.items()}
    return extend_schema(responses=responses, **extend_schema_kwargs)


class ResourceAwareAutoSchema(DeprecationAwareAutoSchema):
    """Derive each operation's integration availability from its runtime policy."""

    def get_extensions(self):
        from apps.api.base.permissions import ModelPermissionMixin
        from apps.api.base.resource_policies import ResourcePolicy

        extensions = super().get_extensions()
        policy = getattr(self.view, "authorization_policy", None)
        if isinstance(self.view, ModelPermissionMixin) and isinstance(policy, ResourcePolicy):
            rule = policy.resolve(self.view, getattr(self.view, "action", None))
            extensions["x-resource-authorization"] = {
                "resource": policy.resource,
                "action": rule.action,
                "available_for_api_key": rule.api_key_allowed,
                "scope": rule.scope if rule.api_key_allowed else None,
                "session_requires_permission": True,
                "minimum_role": rule.minimum_role,
            }
        elif getattr(self.view, "session_only", False):
            extensions["x-session-only"] = True
        return extensions
