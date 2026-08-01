"""Avaliação de risco de login: compara com sessões recentes do mesmo responsável."""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone


@dataclass(frozen=True)
class RiskAssessment:
    is_suspicious: bool
    risk_score: int
    reason: str = ""


def evaluate_login_risk(token_metadata):
    """Sinaliza risco comparando `token_metadata` com logins dos últimos 7 dias.

    Por ora, o único sinal é mudança de país entre logins; outros sinais podem
    ser adicionados aqui sem tocar a view.
    """
    from .models import TokenMetaData

    recentes = (
        TokenMetaData.objects.filter(
            token__responsavel=token_metadata.token.responsavel,
            first_used__gte=timezone.now() - timedelta(days=7),
        )
        .exclude(token=token_metadata.token)
        .select_related("token")
    )

    for anterior in recentes:
        if anterior.country_code and token_metadata.country_code and anterior.country_code != token_metadata.country_code:
            return RiskAssessment(
                is_suspicious=True,
                risk_score=50,
                reason=f"Login de país diferente: {anterior.country} → {token_metadata.country}",
            )

    return RiskAssessment(is_suspicious=False, risk_score=0)
