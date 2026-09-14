"""Fixtures reutilizáveis para cenários HTTP tenantizados."""

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, CatalogoPlanos, sincronizar_planos
from apps.assinaturas.models import Periodicidade, StatusAssinatura, StatusFinanceiro
from apps.assinaturas.subscriptions import Assinaturas, CriacaoAssinatura, OrigemVersaoPlano
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


def garantir_assinatura_corrente(organizacao: Organizacao, *, seats: int = 100):
    """Cria um contrato ativo folgado sem substituir um contrato do cenário."""
    sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=True)
    with organizacao_atual_privilegiada(organizacao.pk):
        corrente = Assinaturas.obter_corrente(organizacao)
        if corrente is not None:
            return corrente
        versao, preco = CatalogoPlanos.obter_versao_inicial(codigo="profissional", periodicidade=Periodicidade.MENSAL)
        return Assinaturas.criar(
            CriacaoAssinatura(
                organizacao=organizacao,
                origem=OrigemVersaoPlano(versao),
                termos=Assinaturas._termos_catalogo(versao, preco, seats_contratados=seats),
                status=StatusAssinatura.ATIVA,
                status_financeiro=StatusFinanceiro.REGULAR,
                politica_trial=None,
                trial_termina_em=None,
                chave_idempotencia=f"fixture-http-profissional:{organizacao.pk}",
            )
        )
