"""Documentação OpenAPI das actions herdadas do `BaseModelViewSet`.

A leitura do histórico é servida por `LogAlteracaoSerpySerializer`, e serpy é
invisível para o `AutoSchema` — sem o serializer-espelho abaixo a action `logs`
apareceria no schema sem corpo de resposta. Ele existe só para documentar:
nenhuma request é serializada por ele, então mantenha-o em sincronia com o
serializer de leitura ao mexer nos campos.
"""

from rest_framework import serializers

from drf_spectacular.utils import OpenApiExample, extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode
from apps.api.core.schema import document_error_codes
from apps.organizacoes.errors import OrganizationErrorCode

ACOES_DESCRIPTION = (
    "`action` é numérico e ordenado por intrusividade — comparações com "
    "`__gt`/`__lt` fazem sentido:\n\n"
    "- `0`: criação\n"
    "- `1`: alteração\n"
    "- `2`: exclusão\n"
    "- `3`: acesso\n"
)

CHANGES_DESCRIPTION = (
    "`changes` é um objeto `{campo: [valor_antes, valor_depois]}`. Campos "
    "sensíveis aparecem mascarados e campos técnicos ficam de fora."
)

SOMENTE_LEITURA_DESCRIPTION = (
    "O histórico é **somente leitura** e não aceita filtros: o recorte é o "
    "próprio registro da URL, do mais recente ao mais antigo. Os registros são "
    "escritos pelo auditlog durante a request original e nunca reescritos — "
    "editá-los invalidaria a trilha de auditoria."
)


class LogAlteracaoActorSchema(serializers.Serializer):
    """Espelho do bloco `user` — nulo quando a alteração não teve ator autenticado."""

    id = serializers.IntegerField()
    nome = serializers.CharField()
    email = serializers.EmailField()


class LogAlteracaoSchema(serializers.Serializer):
    """Espelho de `LogAlteracaoSerpySerializer` para o schema OpenAPI."""

    id = serializers.IntegerField(read_only=True)
    user = LogAlteracaoActorSchema(allow_null=True)
    action = serializers.IntegerField(help_text="0=criação, 1=alteração, 2=exclusão, 3=acesso.")
    model = serializers.CharField(help_text="Modelo auditado, no formato `app_label.model`.")
    object_id = serializers.IntegerField(allow_null=True, help_text="PK numérica do objeto auditado.")
    object_repr = serializers.CharField(help_text="Representação textual do objeto no momento do registro.")
    changes = serializers.DictField(help_text="Diff no formato `{campo: [antes, depois]}`.")
    created_at = serializers.CharField(help_text="Momento do registro, em ISO 8601.")


EXEMPLO_REGISTRO = OpenApiExample(
    "Alteração de um campo",
    value={
        "id": 8421,
        "user": {"id": 12, "nome": "Maria Souza", "email": "maria@example.com"},
        "action": 1,
        "model": "organizacoes.organizacao",
        "object_id": 3,
        "object_repr": "Acme",
        "changes": {"nome": ["Acme LTDA", "Acme"]},
        "created_at": "2026-08-09T14:12:03.415Z",
    },
    response_only=True,
)

LOGS_ACTION_SCHEMA = extend_schema(
    summary="Lista o histórico de auditoria do registro",
    description="\n\n".join([SOMENTE_LEITURA_DESCRIPTION, ACOES_DESCRIPTION, CHANGES_DESCRIPTION]),
    responses={
        200: LogAlteracaoSchema(many=True),
        401: document_error_codes(
            AuthErrorCode.NOT_AUTHENTICATED,
            AuthErrorCode.INVALID_TOKEN,
            AuthErrorCode.EXPIRED_TOKEN,
        ),
        403: document_error_codes(AuthErrorCode.PERMISSION_DENIED),
        404: document_error_codes(CoreErrorCode.NOT_FOUND),
        422: document_error_codes(
            OrganizationErrorCode.HEADER_REQUIRED,
            OrganizationErrorCode.MEMBERSHIP_REQUIRED,
        ),
    },
    examples=[EXEMPLO_REGISTRO],
)
