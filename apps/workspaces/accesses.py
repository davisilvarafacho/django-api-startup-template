from django.db import transaction
from django.db.models import Q

from apps.api.core.errors import APIError
from apps.organizacoes.memberships import Vinculos
from apps.organizacoes.models import Organizacao, Papel, Vinculo
from apps.usuarios.models import Usuario
from apps.workspaces.errors import WorkspaceErrorCode
from apps.workspaces.models import VinculoWorkspace, Workspace


class AcessosWorkspace:
    _access_model = VinculoWorkspace

    @classmethod
    def _lock_vinculos(
        cls, *, organizacao_id: int, vinculo_ids: set[int] = frozenset(), ator: Usuario | None = None, using: str
    ) -> dict[int, Vinculo]:
        filtro = Q(organizacao_id=organizacao_id)
        alvo = Q(pk__in=vinculo_ids) if vinculo_ids else Q(pk__in=[])
        if ator is not None:
            alvo |= Q(usuario_id=ator.pk)
        return {vinculo.pk: vinculo for vinculo in Vinculo.all_objects.using(using).select_for_update().filter(filtro & alvo).order_by("pk")}

    @classmethod
    def _garantir_para_workspace(cls, workspace: Workspace, *, using: str) -> list[VinculoWorkspace]:
        vinculos = list(
            Vinculo.all_objects.using(using)
            .select_for_update()
            .filter(organizacao_id=workspace.organizacao_id, papel__gte=Papel.ADMINISTRADOR, is_active=True, is_deleted=False)
            .order_by("pk")
        )
        return [cls._conceder_bloqueado(vinculo=vinculo, workspace=workspace, using=using) for vinculo in vinculos]

    @classmethod
    def garantir_obrigatorios(cls, vinculo: Vinculo, *, using: str = "default") -> list[VinculoWorkspace]:
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=vinculo.organizacao_id)
            if org.is_deleted or not org.is_active:
                return []
            alvo = Vinculo.all_objects.using(using).select_for_update().get(pk=vinculo.pk, organizacao_id=org.pk)
            if alvo.is_deleted or not alvo.is_active or alvo.papel < Papel.ADMINISTRADOR:
                return []
            workspaces = list(
                Workspace.all_objects.using(using).select_for_update().filter(organizacao_id=org.pk, is_active=True, is_deleted=False).order_by("pk")
            )
            return [cls._conceder_bloqueado(vinculo=alvo, workspace=workspace, using=using) for workspace in workspaces]

    @classmethod
    def conceder(
        cls,
        *,
        vinculo: Vinculo,
        workspace: Workspace,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> VinculoWorkspace:
        using = vinculo._state.db or workspace._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=vinculo.organizacao_id)
            bloqueados = cls._lock_vinculos(organizacao_id=org.pk, vinculo_ids={vinculo.pk}, ator=ator, using=using)
            alvo = bloqueados.get(vinculo.pk)
            if alvo is None:
                raise APIError(WorkspaceErrorCode.ACCESS_REQUIRED, status_code=422)
            if ator is not None:
                Vinculos.bloquear_e_exigir_papel(
                    organizacao=org, usuario=ator, papel_minimo=Papel.ADMINISTRADOR if validar_papel_ator else None, using=using
                )
            ws = Workspace.all_objects.using(using).select_for_update().get(pk=workspace.pk)
            cls._validar_workspace(ws, org)
            return cls._conceder_bloqueado(vinculo=alvo, workspace=ws, using=using)

    @classmethod
    def _conceder_bloqueado(cls, *, vinculo: Vinculo, workspace: Workspace, using: str) -> VinculoWorkspace:
        cls._validar_workspace(workspace, Organizacao.all_objects.using(using).get(pk=workspace.organizacao_id))
        if vinculo.is_deleted or not vinculo.is_active:
            raise APIError(WorkspaceErrorCode.ACCESS_REQUIRED, status_code=422)
        acesso = (
            cls._access_model.all_objects.using(using)
            .select_for_update()
            .filter(vinculo_id=vinculo.pk, workspace_id=workspace.pk)
            .order_by("pk")
            .first()
        )
        if acesso is None:
            return cls._access_model.objects.using(using).create(vinculo=vinculo, workspace=workspace)
        if acesso.is_deleted or not acesso.is_active:
            acesso.is_deleted = False
            acesso.is_active = True
            acesso.selected_for_view = True
            acesso.save(using=using, update_fields=["is_deleted", "is_active", "selected_for_view"])
        return acesso

    @classmethod
    def revogar(
        cls,
        acesso: VinculoWorkspace,
        *,
        ator: Usuario | None = None,
        validar_papel_ator: bool = True,
    ) -> None:
        using = acesso._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=acesso.vinculo.organizacao_id)
            bloqueados = cls._lock_vinculos(organizacao_id=org.pk, vinculo_ids={acesso.vinculo_id}, ator=ator, using=using)
            vinculo = bloqueados.get(acesso.vinculo_id)
            if vinculo is None:
                raise APIError(WorkspaceErrorCode.ACCESS_REQUIRED, status_code=422)
            if ator is not None:
                Vinculos.bloquear_e_exigir_papel(
                    organizacao=org, usuario=ator, papel_minimo=Papel.ADMINISTRADOR if validar_papel_ator else None, using=using
                )
            Workspace.all_objects.using(using).select_for_update().get(pk=acesso.workspace_id)
            alvo = cls._access_model.all_objects.using(using).select_for_update().get(pk=acesso.pk)
            if vinculo.papel >= Papel.ADMINISTRADOR and vinculo.is_active and not vinculo.is_deleted and alvo.is_active and not alvo.is_deleted:
                raise APIError(WorkspaceErrorCode.MANDATORY_ACCESS, status_code=422)
            era_atual = vinculo.current_workspace_id == alvo.workspace_id
            alvo.delete(using=using)
            if era_atual:
                vinculo.current_workspace_id = None
                vinculo.save(using=using, update_fields=["current_workspace"])

    @classmethod
    def definir_atual(cls, *, vinculo: Vinculo, workspace: Workspace) -> Vinculo:
        using = vinculo._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=vinculo.organizacao_id)
            alvo = Vinculo.all_objects.using(using).select_for_update().get(pk=vinculo.pk, organizacao_id=org.pk)
            if alvo.is_deleted or not alvo.is_active:
                raise APIError(WorkspaceErrorCode.ACCESS_REQUIRED, status_code=422)
            ws = Workspace.all_objects.using(using).select_for_update().get(pk=workspace.pk)
            cls._validar_workspace(ws, org)
            acesso = cls._access_model.all_objects.using(using).select_for_update().filter(vinculo_id=alvo.pk, workspace_id=ws.pk).first()
            if acesso is None or acesso.is_deleted or not acesso.is_active:
                raise APIError(WorkspaceErrorCode.ACCESS_REQUIRED, status_code=422)
            if not acesso.selected_for_view:
                acesso.selected_for_view = True
                acesso.save(using=using, update_fields=["selected_for_view"])
            alvo.current_workspace_id = ws.pk
            alvo.save(using=using, update_fields=["current_workspace"])
            return alvo

    @classmethod
    def selecionar_visualizacao(cls, *, vinculo: Vinculo, workspace_ids: set[int]) -> list[VinculoWorkspace]:
        using = vinculo._state.db or "default"
        with transaction.atomic(using=using):
            org = Organizacao.all_objects.using(using).select_for_update().get(pk=vinculo.organizacao_id)
            alvo = Vinculo.all_objects.using(using).select_for_update().get(pk=vinculo.pk, organizacao_id=org.pk)
            if alvo.is_deleted or not alvo.is_active:
                raise APIError(WorkspaceErrorCode.ACCESS_REQUIRED, status_code=422)
            acessos_queryset = cls._access_model.all_objects.using(using).filter(vinculo_id=alvo.pk).order_by("pk")
            workspace_ids_existentes = set(acessos_queryset.values_list("workspace_id", flat=True))
            # Lock every Workspace that participates in the selection before
            # locking any access rows.  Loading the requested IDs as well as
            # existing relations lets us reject a cross-tenant relation with
            # the same stable error used by the other Workspace operations.
            workspace_ids_selecionados = set(workspace_ids)
            workspace_ids = workspace_ids_existentes | workspace_ids_selecionados
            workspaces_locked = list(
                Workspace.all_objects.using(using).select_for_update().filter(pk__in=workspace_ids).order_by("pk")
            )
            workspaces = {ws.pk: ws for ws in workspaces_locked}
            for ws in workspaces_locked:
                if ws.organizacao_id != org.pk:
                    raise APIError(WorkspaceErrorCode.ORGANIZATION_MISMATCH, status_code=422)
            acessos = list(acessos_queryset.select_for_update())
            ativos = {
                a.workspace_id: a
                for a in acessos
                if a.is_active
                and not a.is_deleted
                and workspaces.get(a.workspace_id) is not None
                and workspaces[a.workspace_id].is_active
                and not workspaces[a.workspace_id].is_deleted
            }
            if len(workspaces) != len(workspace_ids):
                raise APIError(WorkspaceErrorCode.INVALID_SELECTION, status_code=422)
            if alvo.current_workspace_id is not None and alvo.current_workspace_id not in workspace_ids_selecionados:
                raise APIError(WorkspaceErrorCode.INVALID_SELECTION, status_code=422)
            if not workspace_ids_selecionados.issubset(ativos):
                raise APIError(WorkspaceErrorCode.INVALID_SELECTION, status_code=422)
            for acesso in acessos:
                novo = acesso.workspace_id in workspace_ids_selecionados
                if acesso.selected_for_view != novo:
                    acesso.selected_for_view = novo
                    acesso.save(using=using, update_fields=["selected_for_view"])
            return [a for a in acessos if a.is_active and not a.is_deleted]

    @classmethod
    def resolver_atual_valido(cls, vinculo: Vinculo, *, using: str = "default") -> Workspace | None:
        with transaction.atomic(using=using):
            organizacao_id = Vinculo.all_objects.using(using).filter(pk=vinculo.pk).values_list("organizacao_id", flat=True).first()
            if organizacao_id is None:
                return None
            organizacao = Organizacao.all_objects.using(using).select_for_update().get(pk=organizacao_id)
            alvo = Vinculo.all_objects.using(using).select_for_update().get(pk=vinculo.pk)
            if organizacao.is_deleted or not organizacao.is_active or alvo.is_deleted or not alvo.is_active or alvo.current_workspace_id is None:
                if alvo.current_workspace_id is not None:
                    alvo.current_workspace_id = None
                    alvo.save(using=using, update_fields=["current_workspace"])
                return None
            ws = Workspace.all_objects.using(using).select_for_update().filter(pk=alvo.current_workspace_id).first()
            acesso = (
                cls._access_model.all_objects.using(using)
                .select_for_update()
                .filter(vinculo_id=alvo.pk, workspace_id=alvo.current_workspace_id)
                .first()
            )
            if (
                ws is None
                or ws.is_deleted
                or not ws.is_active
                or ws.organizacao_id != alvo.organizacao_id
                or acesso is None
                or acesso.is_deleted
                or not acesso.is_active
            ):
                alvo.current_workspace_id = None
                alvo.save(using=using, update_fields=["current_workspace"])
                return None
            return ws

    @staticmethod
    def _validar_workspace(workspace: Workspace, organizacao: Organizacao) -> None:
        if workspace.organizacao_id != organizacao.pk:
            raise APIError(WorkspaceErrorCode.ORGANIZATION_MISMATCH, status_code=422)
        if workspace.is_deleted or not workspace.is_active or organizacao.is_deleted or not organizacao.is_active:
            raise APIError(WorkspaceErrorCode.WORKSPACE_INACTIVE, status_code=422)
