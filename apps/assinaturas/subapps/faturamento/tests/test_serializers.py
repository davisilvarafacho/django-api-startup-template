from datetime import UTC, datetime
from types import SimpleNamespace

from apps.assinaturas.subapps.faturamento.serializers import FaturaResponseSerializer


def test_fatura_response_preserva_total_indisponivel_como_null():
    fatura = SimpleNamespace(
        id=1,
        status=10,
        motivo="",
        total_centavos=None,
        moeda="BRL",
        vencimento_em=None,
        paga_em=None,
        url_hospedada="",
        tentativas=0,
        created_at=datetime(2026, 9, 3, tzinfo=UTC),
    )

    assert FaturaResponseSerializer(fatura).data["total_centavos"] is None
