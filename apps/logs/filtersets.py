"""Filtros do log de alteração.

O log é somente leitura e cresce sem limite, então a interface de filtragem é a
única forma prática de consultá-lo: os campos expostos aqui cobrem as três
perguntas recorrentes — *o que mudou* (`model`, `object_id`, `changed_field`),
*quem mudou* (`actor`, `actor_email`) e *quando/em qual request* (`timestamp`,
`cid`).

Os nomes dos parâmetros seguem os campos herdados de `AbstractLogEntry` (em
inglês), para bater com o contrato já publicado pelo serializer de leitura.
"""

from django_filters.rest_framework import (
    BaseInFilter,
    CharFilter,
    ChoiceFilter,
    DateTimeFromToRangeFilter,
    FilterSet,
    NumberFilter,
)

from .models import LogAlteracao


class NumberInFilter(BaseInFilter, NumberFilter):
    """Aceita uma lista de números separada por vírgula (`?object_id__in=1,2,3`)."""


class ChoiceInFilter(BaseInFilter, ChoiceFilter):
    """Aceita uma lista de choices separada por vírgula (`?action__in=0,2`)."""


class LogAlteracaoFilterSet(FilterSet):
    """Filtros públicos de `GET /logs-alteracao/`."""

    action = ChoiceFilter(
        choices=LogAlteracao.Action.choices,
        label="Ação registrada (0=criação, 1=alteração, 2=exclusão, 3=acesso).",
    )
    action__in = ChoiceInFilter(
        field_name="action",
        choices=LogAlteracao.Action.choices,
        label="Lista de ações separadas por vírgula.",
    )

    model = CharFilter(
        method="filtrar_por_model",
        label="Modelo auditado, como `app_label.model` ou só `model`.",
    )
    object_id = NumberFilter(label="PK numérica do objeto auditado.")
    object_id__in = NumberInFilter(
        field_name="object_id",
        label="Lista de PKs numéricas separadas por vírgula.",
    )
    object_pk = CharFilter(label="PK textual do objeto auditado (cobre PKs não numéricas).")
    object_repr = CharFilter(
        lookup_expr="icontains",
        label="Trecho da representação textual do objeto no momento do registro.",
    )

    actor = NumberFilter(field_name="actor_id", label="ID do usuário responsável pela alteração.")
    actor_email = CharFilter(
        lookup_expr="icontains",
        label="Trecho do e-mail do responsável (preservado mesmo se o usuário for removido).",
    )

    cid = CharFilter(label="Correlation ID da request que gerou o registro.")
    remote_addr = CharFilter(label="Endereço IP de origem da request.")

    timestamp = DateTimeFromToRangeFilter(
        label="Intervalo do registro: use `timestamp_after` e/ou `timestamp_before`.",
    )

    changed_field = CharFilter(
        method="filtrar_por_campo_alterado",
        label="Nome do campo que precisa constar no diff do registro.",
    )

    class Meta:
        model = LogAlteracao
        fields = (
            "action",
            "action__in",
            "model",
            "object_id",
            "object_id__in",
            "object_pk",
            "object_repr",
            "actor",
            "actor_email",
            "cid",
            "remote_addr",
            "timestamp",
            "changed_field",
        )

    def filtrar_por_model(self, queryset, name, value):
        """Filtra pelo `ContentType` do objeto auditado.

        Aceita `app_label.model` (forma não ambígua) ou apenas `model`, que é o
        atalho útil enquanto não há colisão de nome entre apps.

        Args:
            queryset: QuerySet de `LogAlteracao` sendo filtrado.
            name: Nome do filtro (não usado; a resolução é manual).
            value: Rótulo do modelo informado na query string.

        Returns:
            O queryset restrito ao modelo informado.
        """
        rotulo = value.strip().lower()
        if not rotulo:
            return queryset

        if "." in rotulo:
            app_label, _separador, model = rotulo.partition(".")
            return queryset.filter(content_type__app_label=app_label, content_type__model=model)

        return queryset.filter(content_type__model=rotulo)

    def filtrar_por_campo_alterado(self, queryset, name, value):
        """Restringe aos registros cujo diff toca o campo informado.

        `changes` é um `JSONField` com o formato `{campo: [antes, depois]}`, então
        a checagem é uma presença de chave — resolvida no Postgres via `?`, sem
        varrer o JSON em Python.

        Args:
            queryset: QuerySet de `LogAlteracao` sendo filtrado.
            name: Nome do filtro (não usado; a resolução é manual).
            value: Nome do campo que deve constar no diff.

        Returns:
            O queryset restrito aos registros que alteraram o campo.
        """
        campo = value.strip()
        if not campo:
            return queryset

        return queryset.filter(changes__has_key=campo)
