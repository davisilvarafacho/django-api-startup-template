from collections.abc import Mapping

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
TIPOS_ESCALARES = (str, int, bool, type(None))


def normalizar_payload_evento(payload: Mapping[str, object]) -> dict[str, str | int | bool | None]:
    """Retém somente fatos normalizados escalares necessários à reconciliação."""
    if not isinstance(payload, Mapping):
        raise ValidationError("O payload normalizado deve ser um objeto.")
    normalizado = {chave: valor for chave, valor in payload.items() if chave in CHAVES_PAYLOAD_EVENTO}
    if any(not isinstance(valor, TIPOS_ESCALARES) for valor in normalizado.values()):
        raise ValidationError("O payload normalizado aceita somente valores escalares.")
    return normalizado


def validar_payload_evento(payload: object) -> None:
    if not isinstance(payload, dict) or normalizar_payload_evento(payload) != payload:
        raise ValidationError("O payload contém campos fora do esquema permitido.")
