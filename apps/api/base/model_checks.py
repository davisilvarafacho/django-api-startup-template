"""System checks das regras de unicidade e indexação dos models multi-tenant.

Sob RLS, unicidade global é semanticamente errada: dois registros de
organizações diferentes podem legitimamente repetir o mesmo valor. As regras
valem apenas para os models que herdam de `apps.api.base.models.Base` (os que
têm o campo `organizacao`); models `BaseTenantless` (identidade/bootstrap)
ficam de fora.

Regras verificadas:

1. Nenhum campo pode declarar `unique=True`, nem o model pode usar
   `Meta.unique_together` (`base.W001`).
2. Toda `UniqueConstraint` de `Meta.constraints` e todo índice de
   `Meta.indexes` precisa ter `organizacao` como primeiro campo da lista
   (`base.W002`).

Os avisos são apenas relato: nada aqui corrige ou bloqueia o boot.
"""

from django.apps import apps as django_apps
from django.core.checks import Warning, register
from django.db import models

from apps.api.base.models import Base

CAMPO_TENANT = "organizacao"

ID_UNICIDADE_GLOBAL = "base.W001"
ID_TENANT_NAO_E_PRIMEIRO = "base.W002"
ID_WORKSPACE_OBRIGATORIO_SEM_CONSTRAINT = "base.W003"


def models_multitenant():
    """Retorna os models concretos que herdam de `Base` (têm `organizacao`).

    Returns:
        list: Models concretos isolados por organização.
    """
    todos_os_models = django_apps.get_models()
    return [model for model in todos_os_models if issubclass(model, Base)]


def verificar_unicidade_multitenant(models_verificados):
    """Coleta as violações das duas regras de unicidade nos models informados.

    Args:
        models_verificados (Iterable): Models de negócio a inspecionar.

    Returns:
        list: Avisos (`django.core.checks.Warning`) das violações encontradas.
    """
    avisos = []
    for model in models_verificados:
        avisos.extend(_verificar_campos_unicos(model))
        avisos.extend(_verificar_unique_together(model))
        avisos.extend(_verificar_constraints(model))
        avisos.extend(_verificar_indexes(model))
        avisos.extend(_verificar_workspace_obrigatorio(model))
    return avisos


def _rotulo(model):
    return model._meta.label


def _nome_do_campo(nome):
    """Normaliza o nome de um campo de índice, removendo o `-` de ordem decrescente."""
    return nome.removeprefix("-")


def _tenant_e_o_primeiro(campos):
    if not campos:
        return True

    primeiro = _nome_do_campo(campos[0])
    return primeiro == CAMPO_TENANT


def _verificar_campos_unicos(model):
    avisos = []
    for field in model._meta.local_fields:
        if field.primary_key or not field.unique:
            continue

        avisos.append(
            Warning(
                f"O campo '{field.name}' de {_rotulo(model)} declara unique=True.",
                hint=(
                    "Sob RLS a unicidade precisa ser por organização: remova o unique=True e "
                    f"declare uma UniqueConstraint em Meta.constraints com "
                    f"fields=['{CAMPO_TENANT}', '{field.name}']."
                ),
                obj=model,
                id=ID_UNICIDADE_GLOBAL,
            )
        )
    return avisos


def _verificar_unique_together(model):
    avisos = []
    for campos in model._meta.unique_together:
        listados = list(campos)
        avisos.append(
            Warning(
                f"{_rotulo(model)} usa Meta.unique_together={listados}.",
                hint=(
                    "unique_together cria unicidade global: troque por uma UniqueConstraint em "
                    f"Meta.constraints com '{CAMPO_TENANT}' como primeiro campo."
                ),
                obj=model,
                id=ID_UNICIDADE_GLOBAL,
            )
        )
    return avisos


def _verificar_constraints(model):
    avisos = []
    for constraint in model._meta.constraints:
        if not isinstance(constraint, models.UniqueConstraint):
            continue

        globais_declaradas = frozenset(getattr(model, "global_unique_constraint_names", ()))
        if constraint.name in globais_declaradas:
            continue

        campos = list(constraint.fields or ())
        if _tenant_e_o_primeiro(campos):
            continue

        avisos.append(
            Warning(
                f"A UniqueConstraint '{constraint.name}' de {_rotulo(model)} não começa por '{CAMPO_TENANT}' (fields={campos}).",
                hint=f"Declare fields=['{CAMPO_TENANT}', ...] para que a unicidade seja por organização.",
                obj=model,
                id=ID_TENANT_NAO_E_PRIMEIRO,
            )
        )
    return avisos


def _verificar_indexes(model):
    avisos = []
    for index in model._meta.indexes:
        campos = list(index.fields or ())
        if _tenant_e_o_primeiro(campos):
            continue

        avisos.append(
            Warning(
                f"O índice '{index.name}' de {_rotulo(model)} não começa por '{CAMPO_TENANT}' (fields={campos}).",
                hint=(f"Toda query passa filtrada pela organização: coloque '{CAMPO_TENANT}' como primeiro campo do índice."),
                obj=model,
                id=ID_TENANT_NAO_E_PRIMEIRO,
            )
        )
    return avisos


def _verificar_workspace_obrigatorio(model):
    if not getattr(model, "workspace_required", False):
        return []

    for constraint in model._meta.constraints:
        if not isinstance(constraint, models.CheckConstraint):
            continue
        condition = constraint.condition
        if condition is not None and _constraint_rejects_workspace_nulo(condition):
            return []

    return [
        Warning(
            f"{_rotulo(model)} declara workspace_required=True sem constraint que rejeite workspace nulo.",
            hint="Declare uma CheckConstraint com condition=models.Q(workspace__isnull=False).",
            obj=model,
            id=ID_WORKSPACE_OBRIGATORIO_SEM_CONSTRAINT,
        )
    ]


def _constraint_rejects_workspace_nulo(condition):
    """Reconhece constraints que exigem ``workspace IS NOT NULL``."""
    if isinstance(condition, models.Q):
        if condition.negated:
            return len(condition.children) == 1 and condition.children[0] == ("workspace__isnull", True)

        resultados = [_q_child_rejects_workspace_nulo(child) for child in condition.children]
        if condition.connector == models.Q.AND:
            return any(resultados)
        if condition.connector == models.Q.OR:
            return bool(resultados) and all(resultados)
    return False


def _q_child_rejects_workspace_nulo(child):
    if isinstance(child, tuple):
        return child == ("workspace__isnull", False)
    return _constraint_rejects_workspace_nulo(child)


@register()
def check_unicidade_multitenant(app_configs, **kwargs):
    """System check registrado no Django para as regras deste módulo."""
    modelos = models_multitenant()
    return verificar_unicidade_multitenant(modelos)
