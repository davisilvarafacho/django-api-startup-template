"""Depreciação de endpoints da API.

Fonte **única** de depreciação, conforme o
[ADR 0004](../../../docs/adr/0004-politica-de-deprecacao-de-api.md): o decorator
`@api_deprecated` é o único caminho permitido para anunciar que um handler vai
sair. É proibido escrever `Deprecation`/`Sunset` na mão, declarar a depreciação
só no OpenAPI ou gerá-la em middleware, proxy ou gateway.

A mesma declaração alimenta os dois canais:

- **HTTP** — `Deprecation` ([RFC 9745](https://www.rfc-editor.org/rfc/rfc9745.html)),
  `Sunset` ([RFC 8594](https://www.rfc-editor.org/rfc/rfc8594.html)) e um `Link`
  com `rel="deprecation"` apontando para o guia de migração.
- **OpenAPI** — `deprecated`, `externalDocs` e as extensões `x-deprecation-since`,
  `x-sunset` e `x-replacement`.

A granularidade é **por handler Python**, não por URL: quando o `MethodMapper`
do DRF encaminha outro método HTTP para uma função separada, essa função só é
depreciada se receber o próprio decorator.

O wrapper não captura exceções. Respostas devolvidas normalmente pelo handler
recebem os headers em qualquer status, inclusive um `400` explícito; falhas
produzidas antes do handler (autenticação, permissão) ou exceções convertidas
depois pelo DRF não recebem.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import format_datetime
from functools import wraps
from urllib.parse import urlsplit

from django.http.response import HttpResponseBase

from rest_framework import viewsets

from drf_spectacular.openapi import AutoSchema

__all__ = ["ApiDeprecation", "DeprecationAwareAutoSchema", "api_deprecated", "deprecacao_do_handler"]

_METADATA_ATTRIBUTE = "__api_deprecation__"

JANELA_MINIMA = timedelta(days=90)


def _parse_date(name, value):
    """Converte uma data ISO `YYYY-MM-DD`, recusando qualquer outro formato."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} deve usar YYYY-MM-DD") from exc


def _validate_uri(name, value):
    """Aceita URI HTTP(S) com host ou caminho absoluto iniciado por uma `/`."""
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError(f"{name} deve ser uma URI válida")

    parsed = urlsplit(value)
    is_http = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    # `//host/path` é uma URL protocol-relative, não um caminho local.
    is_absolute_path = not parsed.scheme and not parsed.netloc and value.startswith("/") and not value.startswith("//")
    if not (is_http or is_absolute_path):
        raise ValueError(f"{name} deve ser HTTP(S) ou caminho absoluto")
    return value


@dataclass(frozen=True)
class ApiDeprecation:
    """Metadados imutáveis de uma depreciação, validados na importação.

    Attributes:
        since: Data efetiva da depreciação, em UTC.
        sunset: Data a partir da qual a rota pode ser removida, em UTC.
        documentation: URI do guia de migração.
        replacement: URI do substituto, quando existir.
    """

    since: date
    sunset: date
    documentation: str
    replacement: str | None

    @classmethod
    def from_strings(cls, *, since, sunset, documentation, replacement):
        """Valida os argumentos do decorator e monta os metadados.

        Args:
            since: Data efetiva no formato `YYYY-MM-DD`.
            sunset: Data de sunset no formato `YYYY-MM-DD`.
            documentation: URI do guia de migração.
            replacement: URI do substituto ou `None`.

        Returns:
            A instância de `ApiDeprecation` correspondente.

        Raises:
            ValueError: Se alguma data ou URI for inválida, ou se a janela
                entre `since` e `sunset` for menor que 90 dias.
        """
        since_date = _parse_date("since", since)
        sunset_date = _parse_date("sunset", sunset)
        if sunset_date - since_date < JANELA_MINIMA:
            raise ValueError("sunset deve ficar pelo menos 90 dias depois de since")
        return cls(
            since=since_date,
            sunset=sunset_date,
            documentation=_validate_uri("documentation", documentation),
            replacement=(_validate_uri("replacement", replacement) if replacement is not None else None),
        )

    @property
    def deprecation_header(self):
        """Valor do header `Deprecation`: `@<timestamp Unix de since>`."""
        instant = datetime.combine(self.since, time.min, tzinfo=UTC)
        return f"@{int(instant.timestamp())}"

    @property
    def sunset_header(self):
        """Valor do header `Sunset`, no formato HTTP-date em GMT."""
        instant = datetime.combine(self.sunset, time.min, tzinfo=UTC)
        return format_datetime(instant, usegmt=True)

    @property
    def documentation_link(self):
        """Entrada de `Link` que aponta para o guia de migração."""
        return f'<{self.documentation}>; rel="deprecation"; type="text/html"'

    @property
    def openapi_extensions(self):
        """Extensões `x-*` publicadas na operação do OpenAPI."""
        extensions = {
            "x-deprecation-since": self.since.isoformat(),
            "x-sunset": self.sunset.isoformat(),
        }
        if self.replacement is not None:
            extensions["x-replacement"] = self.replacement
        return extensions

    def apply_headers(self, response):
        """Aplica os headers de depreciação, preservando um `Link` existente.

        Args:
            response: A resposta devolvida pelo handler decorado.
        """
        response["Deprecation"] = self.deprecation_header
        response["Sunset"] = self.sunset_header
        current_link = response.headers.get("Link")
        if current_link and self.documentation_link not in current_link:
            response["Link"] = f"{current_link}, {self.documentation_link}"
        elif not current_link:
            response["Link"] = self.documentation_link


def api_deprecated(*, since, sunset, documentation, replacement=None):
    """Marca um handler de ViewSet como depreciado.

    A ordem em relação ao `@action` é indiferente: a depreciação é lida do
    handler durante a geração do schema, por `DeprecationAwareAutoSchema`, e não
    depende de anotação carregada em `func.kwargs`. Por clareza, prefira o
    decorator imediatamente acima do handler, abaixo do `@action`.

    Args:
        since: Data efetiva da depreciação (`YYYY-MM-DD`, meia-noite UTC). Pode
            ser futura: os headers são emitidos desde já e anunciam a mudança
            com antecedência. Datas retroativas são proibidas pelo ADR 0004.
        sunset: Data a partir da qual a rota pode ser removida, ao menos 90 dias
            depois de `since`. Não remove a rota automaticamente.
        documentation: URI do guia de migração em `docs/how-to/`.
        replacement: URI do substituto, quando houver. Só aparece no OpenAPI,
            nunca como header de resposta.

    Returns:
        O decorator que envolve o handler.

    Raises:
        ValueError: Se a configuração for inválida ou se o handler já estiver
            decorado — as duas falham durante a importação do módulo.
    """
    metadata = ApiDeprecation.from_strings(
        since=since,
        sunset=sunset,
        documentation=documentation,
        replacement=replacement,
    )

    def decorator(handler):
        if hasattr(handler, _METADATA_ATTRIBUTE):
            raise ValueError("@api_deprecated não pode ser aplicado mais de uma vez")

        @wraps(handler)
        def wrapped(*args, **kwargs):
            response = handler(*args, **kwargs)
            # Retornos que não são resposta ficam para a validação do DRF.
            if isinstance(response, HttpResponseBase):
                metadata.apply_headers(response)
            return response

        setattr(wrapped, _METADATA_ATTRIBUTE, metadata)
        return wrapped

    return decorator


def deprecacao_do_handler(handler):
    """Devolve a `ApiDeprecation` declarada no handler, ou `None`.

    Args:
        handler: A função de view a inspecionar.

    Returns:
        Os metadados gravados por `@api_deprecated`, ou `None` se o handler não
        estiver depreciado.
    """
    return getattr(handler, _METADATA_ATTRIBUTE, None)


class DeprecationAwareAutoSchema(AutoSchema):
    """Publica no OpenAPI a depreciação declarada por `@api_deprecated`.

    Lê o marcador direto do handler, em vez de anotar a operação com
    `extend_schema`. O motivo é o `@action` do DRF, que faz `func.kwargs =
    kwargs` — uma reatribuição do dicionário inteiro. Qualquer schema que
    trafegue por `func.kwargs` some quando `@action` é o decorator externo; e,
    quando é o interno, a classe do schema vaza para os `initkwargs` do router e
    quebra os handlers irmãos registrados por `MethodMapper`. Lendo o marcador
    aqui, a ordem dos decorators deixa de importar.

    A resolução do handler acompanha a do próprio gerador, o que preserva a
    granularidade por handler exigida pelo ADR 0004: um método mapeado por
    `@<action>.mapping.<verbo>` só é depreciado se tiver o próprio decorator.
    """

    def _deprecacao(self):
        """Localiza os metadados de depreciação do handler em geração."""
        if isinstance(self.view, viewsets.ViewSetMixin):
            handler = getattr(self.view, self.view.action, None)
        else:
            handler = getattr(self.view, self.method.lower(), None)
        return deprecacao_do_handler(handler)

    def is_deprecated(self):
        """Marca `deprecated: true` na operação."""
        return self._deprecacao() is not None

    def get_external_docs(self):
        """Aponta `externalDocs` para o guia de migração."""
        deprecacao = self._deprecacao()
        if deprecacao is None:
            return None
        return {"url": deprecacao.documentation}

    def get_extensions(self):
        """Publica as extensões `x-deprecation-since`, `x-sunset` e `x-replacement`."""
        deprecacao = self._deprecacao()
        if deprecacao is None:
            return {}
        return deprecacao.openapi_extensions
