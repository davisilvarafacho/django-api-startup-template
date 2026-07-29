# 0004 — Política de depreciação de API

- Status: Aceito
- Data: 2026-07-29

## Contexto

A API precisa comunicar mudanças incompatíveis antes de remover endpoints. Sem
uma convenção única, a informação pode divergir entre resposta HTTP, schema
OpenAPI, changelog e documentação de migração.

Django e Django REST Framework não oferecem um mecanismo nativo que faça essa
depreciação HTTP. Avisos Python de depreciação das bibliotecas não são enviados
aos consumidores da API.

Esta decisão segue:

- [RFC 9745 — The Deprecation HTTP Response Header Field](https://www.rfc-editor.org/rfc/rfc9745.html);
- [RFC 8594 — The Sunset HTTP Header Field](https://www.rfc-editor.org/rfc/rfc8594.html);
- [Semantic Versioning 2.0.0](https://semver.org/lang/pt-BR/).

## Decisão

### Mecanismo único e obrigatório

Toda depreciação de endpoint da aplicação deve ser declarada exclusivamente
pelo decorator `@api_deprecated` no handler correspondente do ViewSet.

É proibido declarar ou alterar uma depreciação por qualquer outro mecanismo,
incluindo:

- atribuição manual dos headers `Deprecation` ou `Sunset` dentro da action;
- middleware ou mixin global;
- configuração paralela usada somente pelo OpenAPI;
- geração desses headers em proxy, gateway ou outra camada de infraestrutura;
- qualquer helper alternativo que não delegue ao decorator oficial.

Camadas externas podem encaminhar os headers sem modificá-los. Revisões de
código devem rejeitar depreciações que não tenham o decorator como fonte única.
Aplicar `@api_deprecated` mais de uma vez no mesmo handler é erro de
configuração e deve falhar durante a importação.

### Interface

O decorator será usado em actions padrão:

```python
@api_deprecated(
    since="2026-08-01",
    sunset="2026-11-01",
    documentation="/docs/deprecations/usuarios/",
    replacement="/api/v2/usuarios/",
)
def list(self, request, *args, **kwargs):
    ...
```

E em custom actions, sempre imediatamente acima do handler:

```python
@action(detail=False, methods=["get"])
@api_deprecated(
    since="2026-08-01",
    sunset="2026-11-01",
    documentation="/docs/deprecations/usuarios/",
)
def usuarios(self, request):
    ...
```

`since`, `sunset` e `documentation` são obrigatórios. `replacement` é
opcional, porque uma remoção pode não ter substituto.

As datas usam o formato ISO `YYYY-MM-DD` e representam `00:00:00 UTC`.
`documentation` e `replacement` aceitam URI HTTP(S) com host ou caminho
absoluto iniciado por `/`.

### Granularidade por handler

A depreciação se aplica a todos os métodos HTTP encaminhados ao handler Python
decorado, não automaticamente à URL inteira.

Quando o `MethodMapper` do DRF encaminhar outro método para um handler
separado, esse handler só será depreciado se também receber o decorator:

```python
@relatorio.mapping.delete
@api_deprecated(
    since="2026-08-01",
    sunset="2026-11-01",
    documentation="/docs/deprecations/exclusao-relatorio/",
)
def apagar_relatorio(self, request):
    ...
```

Se uma única função atender a mais de um método HTTP, todos eles compartilham a
mesma depreciação. Para datas ou substitutos diferentes, devem ser usados
handlers separados.

### Comunicação HTTP e OpenAPI

Quando o handler retorna normalmente uma resposta, o decorator acrescenta:

```http
Deprecation: @<timestamp Unix de since>
Sunset: <sunset no formato HTTP-date, em GMT>
Link: <documentation>; rel="deprecation"; type="text/html"
```

Um header `Link` preexistente é preservado, e o link de depreciação é anexado
sem duplicação.

O decorator não captura nem converte exceções. Respostas retornadas
explicitamente pelo handler recebem os headers independentemente do status,
inclusive um `400`. Respostas criadas antes da execução do handler, como falhas
de autenticação ou autorização, e exceções convertidas posteriormente pelo DRF
não recebem os headers.

O mesmo decorator marca a operação no OpenAPI com `deprecated: true`,
`externalDocs`, `x-deprecation-since`, `x-sunset` e, quando aplicável,
`x-replacement`.

Como aplicações web não podem ler esses headers cross-origin por padrão, a
configuração CORS deve expor:

```python
CORS_EXPOSE_HEADERS = [
    "Deprecation",
    "Sunset",
    "Link",
]
```

### Datas e janela de migração

`since` é a data efetiva da depreciação. O decorator pode ser publicado antes
dela; nesse caso, os headers são emitidos imediatamente e anunciam a mudança
com antecedência.

Uma data `since` anterior à primeira publicação do aviso é proibida. A primeira
versão usa governança documental: revisão de código, changelog e checklist de
release verificam que não houve data retroativa. Não haverá comparação dinâmica
com a data corrente durante a inicialização da aplicação.

`sunset` deve estar pelo menos 90 dias corridos depois de `since`. O decorator
valida essa relação durante a importação. A data de `sunset` não remove a rota
automaticamente.

### Remoção e versionamento

Uma rota só pode ser removida quando as duas condições forem satisfeitas:

1. a data de `sunset` chegou;
2. está sendo publicada uma versão SemVer incompatível.

Enquanto o projeto estiver em `0.x`, a mudança para a próxima versão minor,
como `0.1.x` para `0.2.0`, é considerada incompatível. A partir de `1.0.0`, a
remoção exige uma nova versão major.

Se `sunset` chegar antes da próxima versão incompatível, o endpoint permanece
funcional e continua emitindo os headers. Na remoção, a rota deixa de ser
registrada e passa a responder `404`; não será mantida uma resposta temporária
`410 Gone`.

O anúncio entra em `Deprecated`, sob `Unreleased`, no `docs/CHANGELOG.md`. A
retirada entra em `Removed` na versão que remover a rota.

Cada depreciação também aponta para um guia de migração em `docs/how-to/`, com
motivo, substituto quando houver, diferenças de request e response e exemplo de
migração.

### Retenção dos guias de migração

Os guias de migração são permanentes. Eles não são removidos junto com a rota,
porque changelogs, releases, integrações antigas e registros de suporte podem
continuar apontando para essas URLs.

Depois da remoção, o guia deve informar no topo a versão e a data em que o
endpoint deixou de existir. Sua URL permanece estável; reorganizações futuras
da documentação devem preservar redirects.

### Observabilidade

A primeira versão não cria métricas ou logs específicos para endpoints
depreciados. Essa instrumentação fica adiada e, se necessária, deve ser
introduzida por uma decisão posterior sem criar um segundo mecanismo de
depreciação.

## Consequências

- Runtime, OpenAPI e documentação usam uma única declaração junto ao handler.
- Consumidores de navegador conseguem ler os headers via CORS.
- A granularidade acompanha o roteamento real do DRF.
- Guias e links históricos continuam acessíveis após a remoção da rota.
- A aplicação não depende do relógio para continuar inicializando depois que
  uma depreciação envelhece.
- A garantia contra datas retroativas depende inicialmente de revisão e
  disciplina de release.
- Falhas produzidas fora do handler não carregam os headers de depreciação.
- A remoção continua sendo uma ação explícita de release.

## Alternativas rejeitadas

### Mixin de ViewSet

Centralizaria a escrita dos headers, mas ocultaria a depreciação longe do
handler e obrigaria herança específica.

### Middleware global

Cobriria respostas produzidas antes do handler, mas exigiria um registro
paralelo de rotas e métodos, contrariando a fonte única.

### Registro central de depreciações

Permitiria auditoria automatizada, mas duplicaria os metadados já presentes no
decorator e acrescentaria sincronização e infraestrutura prematuras.

### Remoção automática em `sunset`

Transformaria uma data de comunicação em mudança de roteamento fora do processo
SemVer e de release.
