from django.db import transaction

from apps.api.core.errors import APIError
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.models import Usuario
from apps.workspaces.accesses import AcessosWorkspace
from apps.workspaces.errors import WorkspaceErrorCode
from apps.workspaces.models import Workspace


class Workspaces:
    @classmethod
    def criar(
        cls,
        *,
        organizacao: Organizacao,
        nome: str,
        slug: str,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> Workspace:
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao.pk)
            if org.is_deleted or not org.is_active:
                raise APIError(WorkspaceErrorCode.WORKSPACE_INACTIVE, status_code=422)
            afetados_ids = set(
                Vinculo.all_objects.using(using)
                .filter(organizacao_id=org.pk, papel__gte=Papel.ADMINISTRADOR, is_active=True, is_deleted=False)
                .values_list("pk", flat=True)
            )
            AcessosWorkspace._lock_vinculos(organizacao_id=org.pk, vinculo_ids=afetados_ids, ator=ator, using=using)
            if ator is not None:
                Vinculos.bloquear_e_exigir_papel(
                    organizacao=org, usuario=ator, papel_minimo=Papel.ADMINISTRADOR if validar_papel_ator else None, using=using
                )
            workspace = Workspace.objects.using(using).create(organizacao=org, nome=nome, slug=slug)
            AcessosWorkspace._garantir_para_workspace(workspace, using=using)
            return workspace

    @classmethod
    def criar_inicial(cls, *, organizacao: Organizacao, proprietario: Vinculo) -> Workspace:
        using = organizacao._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao.pk)
            vinculo = Vinculo.all_objects.using(using).select_for_update().get(pk=proprietario.pk, organizacao_id=org.pk)
            workspace = Workspace.objects.using(using).create(organizacao=org, nome="Principal", slug="principal")
            AcessosWorkspace._conceder_bloqueado(vinculo=vinculo, workspace=workspace, using=using)
            vinculo.current_workspace_id = workspace.pk
            vinculo.save(using=using, update_fields=["current_workspace"])
            return workspace

    @classmethod
    def inativar(
        cls,
        workspace: Workspace,
        *,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> None:
        using = workspace._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=workspace.organizacao_id)
            # Inativar mutates every active access in the Workspace.  Lock all
            # of the affected memberships before taking the Workspace lock;
            # only locking memberships that currently point at ``current``
            # would leave another writer free to change its access concurrently.
            acesso_vinculo_ids = set(
                AcessosWorkspace._access_model.all_objects.using(using)
                .filter(workspace_id=workspace.pk)
                .values_list("vinculo_id", flat=True)
            )
            atual_vinculo_ids = set(
                Vinculo.all_objects.using(using).filter(current_workspace_id=workspace.pk).values_list("pk", flat=True)
            )
            afetados_ids = acesso_vinculo_ids | atual_vinculo_ids
            bloqueados = AcessosWorkspace._lock_vinculos(organizacao_id=org.pk, vinculo_ids=afetados_ids, ator=ator, using=using)
            if ator is not None:
                Vinculos.bloquear_e_exigir_papel(
                    organizacao=org, usuario=ator, papel_minimo=Papel.ADMINISTRADOR if validar_papel_ator else None, using=using
                )
            vinculos = [bloqueados[vinculo_id] for vinculo_id in sorted(afetados_ids) if vinculo_id in bloqueados]
            alvo = Workspace.all_objects.using(using).select_for_update().get(pk=workspace.pk, organizacao_id=org.pk)
            if alvo.is_deleted or not alvo.is_active:
                return
            acessos = list(AcessosWorkspace._access_model.all_objects.using(using).select_for_update().filter(workspace_id=alvo.pk).order_by("pk"))
            alvo.is_active = False
            alvo.save(using=using, update_fields=["is_active"])
            for acesso in acessos:
                if not acesso.is_deleted and acesso.is_active:
                    acesso.is_active = False
                    acesso.save(using=using, update_fields=["is_active"])
            for vinculo in vinculos:
                vinculo.current_workspace_id = None
                vinculo.save(using=using, update_fields=["current_workspace"])
