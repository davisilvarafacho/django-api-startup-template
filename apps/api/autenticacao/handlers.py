"""Handlers do app de autenticação."""
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from rest_framework import status

from axes.helpers import (
    get_client_ip_address,
    get_client_parameters,
    get_client_user_agent,
    get_client_username,
    get_cool_off,
    get_failure_limit,
)
from axes.models import AccessAttempt

MENSAGEM_DE_BLOQUEIO = "Muitas tentativas de login."


def segundos_ate_o_desbloqueio(request: HttpRequest, credentials: dict | None) -> int:
    """Calcula quanto falta para o bloqueio expirar.

    O `attempt_time` é sobrescrito a cada falha, mas não durante o bloqueio,
    porque `AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT` é `False`. Logo o
    relógio congela na falha que causou o bloqueio e o prazo é exato.

    O filtro é montado por `get_client_parameters` em vez de codificar a chave
    de bloqueio aqui, para não quebrar caso `AXES_LOCKOUT_PARAMETERS` mude.

    Args:
        request: Request HTTP que sofreu o bloqueio.
        credentials: Credenciais enviadas na tentativa, quando disponíveis.

    Returns:
        Segundos restantes, com piso de 1. Devolve o cooloff completo se não
        houver tentativa registrada, que é o valor conservador.
    """
    cooloff = get_cool_off(request)
    if cooloff is None:
        return 0

    filtros = get_client_parameters(
        get_client_username(request, credentials),
        get_client_ip_address(request),
        get_client_user_agent(request),
        request,
        credentials,
    )

    limite = get_failure_limit(request, credentials)
    prazos: list[int] = []
    for filtro in filtros:
        tentativas = AccessAttempt.objects.filter(**filtro)
        falhas = sum(tentativas.values_list("failures_since_start", flat=True))
        if falhas < limite:
            continue

        tentativa = tentativas.order_by("-attempt_time").first()
        if tentativa is None:
            continue

        restante = (tentativa.attempt_time + cooloff) - timezone.now()
        prazos.append(max(1, int(restante.total_seconds())))

    if not prazos:
        return int(cooloff.total_seconds())

    return max(prazos)


def resposta_de_bloqueio(request: HttpRequest, credentials: dict | None = None) -> JsonResponse:
    """Monta a resposta devolvida quando o django-axes bloqueia a tentativa.

    Registrada em `AXES_LOCKOUT_CALLABLE`. O prazo restante vai só no header
    `Retry-After`; a mensagem é genérica de propósito.

    Args:
        request: Request HTTP que sofreu o bloqueio.
        credentials: Credenciais enviadas na tentativa, quando disponíveis.

    Returns:
        Resposta JSON com status 429.
    """
    resposta = JsonResponse(
        {"mensagem": MENSAGEM_DE_BLOQUEIO},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )

    segundos = segundos_ate_o_desbloqueio(request, credentials)
    if segundos:
        resposta["Retry-After"] = str(segundos)

    return resposta
