"""Casos de uso transacionais para times de uma organização."""

from django.db import transaction

from apps.api.autenticacao.services import lock_user_accounts
from apps.api.core.errors import APIError
from apps.organizacoes.errors import OrganizationErrorCode
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao, Papel, Time
from apps.usuarios.models import Usuario


class Times:
    """Mutações de times com autorização revalidada na seção crítica."""

    @classmethod
    def criar(
        cls,
        *,
        organizacao: Organizacao,
        dados: dict,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> Time:
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            organizacao = cls._bloquear_contexto(
                organizacao.pk,
                ator=ator,
                validar_papel_ator=validar_papel_ator,
                using=using,
            )
            return Time.objects.using(using).create(organizacao=organizacao, **dados)

    @classmethod
    def atualizar(
        cls,
        time: Time,
        *,
        dados: dict,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> Time:
        if {"organizacao", "organizacao_id"} & dados.keys():
            raise ValueError("O campo organizacao é imutável.")
        using = time._state.db or "default"
        with transaction.atomic(using=using):
            organizacao = cls._bloquear_contexto(
                time.organizacao_id,
                ator=ator,
                validar_papel_ator=validar_papel_ator,
                using=using,
            )
            atual = Time.all_objects.using(using).select_for_update().get(pk=time.pk, organizacao=organizacao)
            for campo, valor in dados.items():
                setattr(atual, campo, valor)
            if dados:
                atual.save(using=using)
            return atual

    @classmethod
    def remover(
        cls,
        time: Time,
        *,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> None:
        using = time._state.db or "default"
        with transaction.atomic(using=using):
            organizacao = cls._bloquear_contexto(
                time.organizacao_id,
                ator=ator,
                validar_papel_ator=validar_papel_ator,
                using=using,
            )
            atual = Time.all_objects.using(using).select_for_update().get(pk=time.pk, organizacao=organizacao)
            atual.delete(using=using)

    @staticmethod
    def _bloquear_contexto(
        organizacao_id: int,
        *,
        ator: Usuario | None,
        validar_papel_ator: bool,
        using: str,
    ) -> Organizacao:
        usuarios_bloqueados = lock_user_accounts((ator,), using=using)
        ator_bloqueado = usuarios_bloqueados.get(ator.pk) if ator is not None else None
        organizacao = Vinculos._bloquear_organizacao_aberta(organizacao_id, using=using)
        if organizacao.is_deleted or not organizacao.is_active:
            raise APIError(OrganizationErrorCode.ORGANIZATION_INACTIVE, status_code=403)
        if ator_bloqueado is not None:
            Vinculos.bloquear_e_exigir_papel(
                organizacao=organizacao,
                usuario=ator_bloqueado,
                papel_minimo=Papel.GESTOR if validar_papel_ator else None,
                using=using,
            )
        return organizacao
