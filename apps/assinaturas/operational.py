"""Leitura operacional mínima através das policies RLS de cada organização."""

from datetime import datetime
from enum import StrEnum

from django.db import NotSupportedError, connections
from django.utils import timezone


class FinalidadeOperacionalAssinatura(StrEnum):
    ROLLOUT = "rollout"
    TRIAL_EXPIRADO = "trial_expired"
    CARENCIA_SEATS_DIVERGENTE = "seat_grace_mismatch"


def selecionar_organizacoes_operacionais(
    finalidade: FinalidadeOperacionalAssinatura,
    *,
    apos_id: int = 0,
    limite: int,
    referencia: datetime | None = None,
    using: str = "default",
) -> list[int]:
    """Retorna somente IDs acionáveis sem desativar ou ampliar o RLS."""
    if not isinstance(finalidade, FinalidadeOperacionalAssinatura):
        raise ValueError("Finalidade operacional de assinatura inválida.")
    if type(apos_id) is not int or apos_id < 0:
        raise ValueError("Cursor operacional precisa ser um inteiro não negativo.")
    if type(limite) is not int or limite <= 0:
        raise ValueError("Limite operacional precisa ser um inteiro positivo.")
    referencia = referencia or timezone.now()
    if not isinstance(referencia, datetime) or not timezone.is_aware(referencia):
        raise ValueError("Referência operacional precisa ser um datetime consciente de fuso.")

    conexao = connections[using]
    if conexao.vendor != "postgresql":
        raise NotSupportedError("O selector operacional de assinaturas exige PostgreSQL.")
    with conexao.cursor() as cursor:
        cursor.execute(
            """
            SELECT organizacao_id
            FROM public.selecionar_organizacoes_operacionais_assinatura(%s, %s, %s, %s)
            """,
            [finalidade.value, apos_id, limite, referencia],
        )
        return [row[0] for row in cursor.fetchall()]
