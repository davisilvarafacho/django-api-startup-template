# Deprecação de API

Use exclusivamente `@api_deprecated` em handlers de ViewSets. É proibido escrever `Deprecation` ou `Sunset` manualmente, configurar depreciação apenas no OpenAPI ou gerar esses headers em middleware e gateways.

## Declarar

```python
@action(detail=False, methods=["get"])
@api_deprecated(
    since="2026-08-01",
    sunset="2026-11-01",
    documentation="/docs/deprecations/usuarios/",
    replacement="/api/v2/usuarios/",
)
def usuarios(self, request):
    ...
```

`since`, `sunset` e `documentation` são obrigatórios; `replacement` é opcional. As datas usam `YYYY-MM-DD`, meia-noite UTC, e precisam ter intervalo mínimo de 90 dias. O decorator atua por handler; métodos em `@action.mapping` precisam de decorator próprio.

## Resposta e schema

Respostas retornadas normalmente pelo handler recebem `Deprecation`, `Sunset` e `Link`, inclusive `400` explícito. Falhas anteriores ao handler e exceções convertidas pelo DRF não recebem esses headers.

O OpenAPI recebe `deprecated`, `externalDocs`, `x-deprecation-since`, `x-sunset` e, quando aplicável, `x-replacement`. Os headers são expostos por CORS.

## Remover

Uma rota só pode ser removida depois de `sunset` e numa versão SemVer incompatível: a próxima minor em `0.x` e a próxima major a partir de `1.0.0`.

Antes da remoção, confirme a janela mínima de 90 dias, atualize `Deprecated` para `Removed` no changelog e preserve o guia de migração, registrando nele data e versão da retirada. A rota removida responde `404`; não há `410` nem remoção automática por data.
