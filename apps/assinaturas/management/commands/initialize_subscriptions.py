"""Inicializa contratos gratuitos para organizações legadas."""

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError

from apps.assinaturas.catalogs import CatalogoPlanos, ErroCatalogoPlanos
from apps.assinaturas.models import Periodicidade
from apps.assinaturas.operational import FinalidadeOperacionalAssinatura, selecionar_organizacoes_operacionais
from apps.assinaturas.subscriptions import Assinaturas
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


class Command(BaseCommand):
    help = "Lista organizações ativas sem contrato corrente; escreve somente com --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Cria a assinatura gratuita para cada organização elegível.")
        parser.add_argument("--batch-size", type=int, default=100, help="Quantidade de organizações lidas por lote (padrão: 100).")

    def handle(self, *args, **options):
        aplicar = options["apply"]
        batch_size = options["batch_size"]
        if type(batch_size) is not int or batch_size <= 0:
            raise CommandError("O tamanho do lote precisa ser um inteiro positivo.")

        try:
            versao_gratuita, preco_gratuito = CatalogoPlanos.obter_versao_inicial(
                codigo="gratuito",
                periodicidade=Periodicidade.MENSAL,
                moeda="BRL",
            )
        except (ErroCatalogoPlanos, ObjectDoesNotExist) as exc:
            raise CommandError("Catálogo gratuito indisponível; execute sync_plans --apply antes deste comando.") from exc

        encontradas = 0
        inicializadas = 0
        cursor_id = 0
        while True:
            organizacao_ids = self._proximo_lote(cursor_id=cursor_id, batch_size=batch_size)
            if not organizacao_ids:
                break
            cursor_id = organizacao_ids[-1]
            encontradas += len(organizacao_ids)
            if not aplicar:
                continue

            organizacoes = Organizacao.objects.in_bulk(organizacao_ids)
            for organizacao_id in organizacao_ids:
                organizacao = organizacoes.get(organizacao_id)
                if organizacao is None:
                    continue
                with organizacao_atual_privilegiada(organizacao.pk):
                    if Assinaturas.obter_corrente(organizacao) is not None:
                        continue
                    Assinaturas.criar_gratuita(
                        organizacao=organizacao,
                        versao_plano=versao_gratuita,
                        preco_plano=preco_gratuito,
                        chave_idempotencia=f"rollout:gratuito:{organizacao.pk}",
                    )
                    inicializadas += 1

        if aplicar:
            self.stdout.write(
                self.style.SUCCESS(f"APLICADO: {inicializadas} organização(ões) inicializada(s); {encontradas} elegível(is) observada(s).")
            )
        else:
            self.stdout.write(self.style.WARNING(f"DRY-RUN: {encontradas} organização(ões) elegível(is); nenhuma alteração foi aplicada."))

    @staticmethod
    def _proximo_lote(*, cursor_id: int, batch_size: int) -> list[int]:
        return selecionar_organizacoes_operacionais(
            FinalidadeOperacionalAssinatura.ROLLOUT,
            apos_id=cursor_id,
            limite=batch_size,
        )
