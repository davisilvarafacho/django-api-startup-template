import re
from collections.abc import Mapping
from datetime import datetime

from django.core.exceptions import ValidationError

CHAVES_PAYLOAD_EVENTO = frozenset(
    {
        "amount",
        "currency",
        "customer_reference",
        "failure_code",
        "invoice_status",
        "payment_status",
        "period_end",
        "period_start",
        "subscription_status",
    }
)
_STATUS = re.compile(r"^[a-z][a-z0-9_.-]{0,49}$")
_CODIGO = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_REFERENCIA = re.compile(r"^[A-Za-z0-9_.:~-]{1,512}$")
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|[+-](?:0[0-9]|1[0-4]):[0-5][0-9])$")
_MAX_BIGINT = 9_223_372_036_854_775_807
TIPO_EVENTO_PATTERN = r"^[a-z][a-z0-9]*([._][a-z0-9]+)*$"
_TIPO_EVENTO = re.compile(TIPO_EVENTO_PATTERN)


def validar_tipo_evento(tipo: object) -> None:
    """Valida o identificador normalizado, sem PII ou separadores ambíguos."""
    if not isinstance(tipo, str) or len(tipo) > 100 or not _TIPO_EVENTO.fullmatch(tipo):
        raise ValidationError("Informe um tipo normalizado em lowercase, separado por ponto ou underscore.")


def _normalizar_valor(chave: str, valor: object) -> str | int:
    if chave == "amount":
        if isinstance(valor, bool) or not isinstance(valor, int) or not 0 <= valor <= _MAX_BIGINT:
            raise ValidationError({chave: "Informe um inteiro não negativo em unidades menores."})
        return valor
    if chave == "currency":
        if not isinstance(valor, str) or not re.fullmatch(r"[A-Za-z]{3}", valor):
            raise ValidationError({chave: "Informe uma moeda ISO com três letras."})
        return valor.upper()
    if chave in {"invoice_status", "payment_status", "subscription_status"}:
        if not isinstance(valor, str) or not _STATUS.fullmatch(valor):
            raise ValidationError({chave: "Informe um status normalizado válido."})
        return valor
    if chave == "failure_code":
        if not isinstance(valor, str) or not _CODIGO.fullmatch(valor):
            raise ValidationError({chave: "Informe um código normalizado válido."})
        return valor
    if chave == "customer_reference":
        if not isinstance(valor, str) or not _REFERENCIA.fullmatch(valor):
            raise ValidationError({chave: "Informe uma referência opaca válida."})
        return valor
    if chave in {"period_start", "period_end"}:
        if not isinstance(valor, str) or not _TIMESTAMP.fullmatch(valor):
            raise ValidationError({chave: "Informe um timestamp ISO-8601 com timezone."})
        try:
            instante = datetime.fromisoformat(valor)
        except ValueError as exc:
            raise ValidationError({chave: "Informe um timestamp ISO-8601 com timezone."}) from exc
        if instante.tzinfo is None:
            raise ValidationError({chave: "Informe um timestamp ISO-8601 com timezone."})
        return valor
    raise AssertionError(f"Chave de payload sem schema: {chave}")


def normalizar_payload_evento(payload: Mapping[str, object]) -> dict[str, str | int]:
    """Descarta desconhecidos e valida estritamente cada fato conhecido."""
    if not isinstance(payload, Mapping):
        raise ValidationError("O payload normalizado deve ser um objeto.")
    return {chave: _normalizar_valor(chave, valor) for chave, valor in payload.items() if chave in CHAVES_PAYLOAD_EVENTO}


def validar_payload_evento(payload: object) -> None:
    if not isinstance(payload, dict) or normalizar_payload_evento(payload) != payload:
        raise ValidationError("O payload contém campos fora do esquema permitido ou não normalizados.")
