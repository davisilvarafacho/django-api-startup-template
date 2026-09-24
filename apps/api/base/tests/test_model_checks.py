"""Testes DB-less do system check de unicidade dos models multi-tenant."""

from django.db import models

from apps.api.base.model_checks import (
    ID_TENANT_NAO_E_PRIMEIRO,
    ID_UNICIDADE_GLOBAL,
    ID_WORKSPACE_OBRIGATORIO_SEM_CONSTRAINT,
    models_multitenant,
    verificar_unicidade_multitenant,
)
from apps.api.base.models import Base, BaseTenantless


class RegistroComUnicidadeGlobal(Base):
    """Modelo efêmero que viola a regra 1 (unicidade global)."""

    codigo = models.CharField(max_length=32, unique=True)
    apelido = models.CharField(max_length=32)
    sequencial = models.IntegerField(default=0)

    class Meta:
        app_label = "base"
        unique_together = [("apelido", "sequencial")]


class RegistroComConstraintSemTenant(Base):
    """Modelo efêmero que viola a regra 2 (tenant não é o primeiro campo)."""

    codigo = models.CharField(max_length=32)

    class Meta:
        app_label = "base"
        constraints = [
            models.UniqueConstraint(fields=["codigo"], name="registro_sem_tenant_codigo_unique"),
        ]
        indexes = [
            models.Index(fields=["codigo"], name="registro_sem_tenant_codigo_idx"),
        ]


class RegistroConforme(Base):
    """Modelo efêmero que respeita as duas regras."""

    codigo = models.CharField(max_length=32)

    class Meta:
        app_label = "base"
        constraints = [
            models.UniqueConstraint(fields=["organizacao", "codigo"], name="registro_conforme_codigo_unique"),
        ]
        indexes = [
            models.Index(fields=["organizacao", "codigo"], name="registro_conforme_codigo_idx"),
        ]


class RegistroSemTenant(BaseTenantless):
    """Modelo efêmero de identidade: fora do escopo das regras."""

    codigo = models.CharField(max_length=32, unique=True)

    class Meta:
        app_label = "base"


class RegistroWorkspaceObrigatorioSemConstraint(Base):
    workspace_required = True
    codigo = models.CharField(max_length=32)

    class Meta:
        app_label = "base"


class RegistroWorkspaceObrigatorioComConstraint(Base):
    workspace_required = True
    codigo = models.CharField(max_length=32)

    class Meta:
        app_label = "base"
        constraints = [
            models.CheckConstraint(condition=models.Q(workspace__isnull=False), name="registro_workspace_obrigatorio_check"),
        ]


def test_campo_unique_e_unique_together_viram_w001():
    avisos = verificar_unicidade_multitenant([RegistroComUnicidadeGlobal])
    ids = [aviso.id for aviso in avisos]

    assert ids == [ID_UNICIDADE_GLOBAL, ID_UNICIDADE_GLOBAL]
    assert "codigo" in avisos[0].msg
    assert "unique_together" in avisos[1].msg


def test_constraint_e_index_sem_organizacao_no_inicio_viram_w002():
    avisos = verificar_unicidade_multitenant([RegistroComConstraintSemTenant])
    ids = [aviso.id for aviso in avisos]

    assert ids == [ID_TENANT_NAO_E_PRIMEIRO, ID_TENANT_NAO_E_PRIMEIRO]
    assert "registro_sem_tenant_codigo_unique" in avisos[0].msg
    assert "registro_sem_tenant_codigo_idx" in avisos[1].msg


def test_model_conforme_nao_gera_aviso():
    assert verificar_unicidade_multitenant([RegistroConforme]) == []


def test_model_sem_tenant_fica_fora_do_escopo():
    assert RegistroSemTenant not in models_multitenant()


def test_models_multitenant_so_traz_subclasses_de_base():
    modelos = models_multitenant()

    assert RegistroComUnicidadeGlobal in modelos
    assert all(issubclass(model, Base) for model in modelos)


def test_workspace_obrigatorio_exige_constraint_contra_nulo():
    avisos = verificar_unicidade_multitenant([RegistroWorkspaceObrigatorioSemConstraint, RegistroWorkspaceObrigatorioComConstraint])

    workspace_avisos = [aviso for aviso in avisos if aviso.id == ID_WORKSPACE_OBRIGATORIO_SEM_CONSTRAINT]
    assert len(workspace_avisos) == 1
    assert workspace_avisos[0].obj is RegistroWorkspaceObrigatorioSemConstraint
