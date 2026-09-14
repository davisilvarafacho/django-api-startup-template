"""Contexto de tenant para o RLS.

O contexto é sempre aplicado com `SET LOCAL` (`is_local=True`), ou seja, com
escopo da **transação** corrente. É isso que torna o isolamento seguro sob um
connection pool em *transaction mode* (PgBouncer), onde a conexão volta ao pool
a cada transação: um `SET` de sessão poderia vazar para outro cliente, um
`SET LOCAL` morre no commit.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from django.db import transaction

from django_rls.context import clear_rls_context, set_rls_context

CHAVE_TENANT = "tenant_id"

if TYPE_CHECKING:
    from apps.assinaturas.access_policies import SituacaoAcesso
    from apps.assinaturas.models import AssinaturaOrganizacao
    from apps.assinaturas.subscriptions import UtilizacaoSeats
    from apps.organizacoes.models import Organizacao, Vinculo


@dataclass(frozen=True, slots=True)
class ContextoOrganizacao:
    """Fatos de tenant e acesso comercial validados antes da view."""

    organizacao: Organizacao
    vinculo: Vinculo | None
    assinatura: AssinaturaOrganizacao
    utilizacao_seats: UtilizacaoSeats
    situacao_acesso: SituacaoAcesso

    @property
    def organization_id(self) -> int:
        return self.organizacao.pk

    @property
    def organization_slug(self) -> str:
        return self.organizacao.slug

    @property
    def membership_id(self) -> int | None:
        return self.vinculo.pk if self.vinculo is not None else None

    @property
    def role(self) -> int | None:
        return self.vinculo.papel if self.vinculo is not None else None

    def has_minimum_role(self, minimum_role: int) -> bool:
        return self.vinculo is not None and self.vinculo.papel >= minimum_role


class PoliticaComercialTenant(Protocol):
    """Contrato fechado para construir o contexto comercial final."""

    def __call__(
        self,
        organizacao: Organizacao,
        vinculo: Vinculo | None,
        *,
        regularizacao_assinatura: bool,
    ) -> ContextoOrganizacao: ...


def definir_organizacao_atual(organizacao_id):
    """Aplica a organização na transação corrente.

    Deve ser chamado **dentro** de uma transação — fora dela o `SET LOCAL` não
    tem efeito duradouro.
    """
    set_rls_context(CHAVE_TENANT, organizacao_id, is_local=True)


@contextmanager
def organizacao_atual(organizacao_id):
    """Executa um bloco sob a organização informada, em transação própria.

    Use em tasks do Celery, management commands e qualquer código que rode fora
    do ciclo de request — sem isso as queries levantam `RLSContextRequiredError`.

    Não troca de organização: uma vez definido, o tenant é imutável dentro do
    mesmo contexto (proteção da lib contra troca de tenant no meio do voo). Para
    iterar sobre várias organizações use `organizacao_atual_privilegiada`.
    """
    with transaction.atomic():
        definir_organizacao_atual(organizacao_id)
        try:
            yield
        finally:
            # `SET LOCAL` morre no commit, mas o rastreio em memória da lib não.
            # Sem limpar, o guard continuaria achando que há contexto e deixaria
            # passar uma query sem escopo no banco.
            clear_rls_context({CHAVE_TENANT})


@contextmanager
def organizacao_atual_privilegiada(organizacao_id):
    """Igual à `organizacao_atual`, mas **pode trocar** a organização vigente.

    Reservado para código de sistema que legitimamente atravessa organizações:
    jobs que varrem todos os tenants, management commands e testes. Continua
    usando `SET LOCAL` — diferente do `system_rls_context` da lib, que aplica um
    `SET` de sessão e não sobrevive a um pool em transaction mode.

    Não use em código de request: lá a imutabilidade do tenant é uma proteção.
    """
    with transaction.atomic():
        set_rls_context(CHAVE_TENANT, organizacao_id, is_local=True, system=True, source="sistema")
        try:
            yield
        finally:
            clear_rls_context({CHAVE_TENANT})
