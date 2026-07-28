# Padronização de erros da API

**Status:** aprovado em 2026-07-28.

## Objetivo

Criar uma base única para erros HTTP da API, com códigos estáveis para clientes,
mensagens traduzíveis para pessoas, correlação operacional e documentação
OpenAPI. O formato é inspirado no conceito usado pelo Saleor (`field`,
`message`, `code`), adaptado para REST.

Essa base é um pré-requisito para os novos fluxos de autenticação do Batch 5.

## Escopo

- Respostas de erro produzidas pelo DRF, Django e middleware.
- Erros de validação simples, aninhados e em listas.
- Erros de autenticação, autorização, tenancy, throttling e domínio.
- Erros inesperados, sem exposição de detalhes internos.
- Registry central de códigos e enums organizados por domínio.
- Representação dos erros no schema OpenAPI.
- Localização da mensagem via `Accept-Language`.

Respostas de sucesso não serão envelopadas. Paginação e payloads de sucesso
continuarão usando o formato natural de cada endpoint.

## Contrato HTTP

Toda resposta de erro terá o mesmo envelope:

```json
{
  "errors": [
    {
      "code": "auth.invalid_credentials",
      "message": "E-mail ou senha inválidos.",
      "field": null,
      "path": null,
      "context": {}
    }
  ],
  "request_id": "01J..."
}
```

`errors` será sempre uma lista, inclusive quando houver um único erro.

### Campos

- `code`: identificador estável e não traduzido, no formato
  `dominio.erro`.
- `message`: explicação traduzível e adequada para exibição.
- `field`: nome do campo de primeiro nível, quando aplicável.
- `path`: caminho completo para campos aninhados e itens de listas.
- `context`: dados estruturados e seguros que ajudem o cliente a tratar o
  erro. Nunca poderá conter segredo, token, senha, traceback ou detalhes
  internos.
- `request_id`: correlation ID da requisição, igual ao usado nos logs,
  Sentry e traces.

Exemplo de validação aninhada:

```json
{
  "errors": [
    {
      "code": "validation.required",
      "message": "Este campo é obrigatório.",
      "field": "email",
      "path": ["members", 0, "email"],
      "context": {}
    }
  ],
  "request_id": "01J..."
}
```

## Códigos

Os códigos serão definidos em enums por domínio, registrados num registry
central:

- `ValidationErrorCode`
- `AuthErrorCode`
- `OrganizationErrorCode`
- demais enums adicionados pelos apps

Exemplos:

```text
validation.required
validation.invalid
auth.invalid_credentials
auth.invalid_token
auth.expired_token
auth.insufficient_scope
auth.reauthentication_required
organizations.invitation_expired
organizations.tenant_mismatch
```

O registry deve:

- rejeitar códigos duplicados;
- validar o formato `dominio.erro`;
- permitir descoberta para documentação;
- falhar por system check antes de servir a aplicação quando houver
  inconsistência.

Mensagens poderão ser traduzidas. Clientes devem tomar decisões pelo `code`,
nunca pelo conteúdo de `message`.

## Status HTTP

O código de domínio complementa, mas não substitui, o status HTTP:

- `400 Bad Request`: JSON malformado ou requisição estruturalmente inválida;
- `401 Unauthorized`: autenticação ausente, inválida ou expirada;
- `403 Forbidden`: credencial válida sem autorização suficiente;
- `404 Not Found`: recurso inexistente ou não visível ao tenant;
- `409 Conflict`: duplicidade, conflito de estado ou operação incompatível;
- `422 Unprocessable Entity`: validação de campos ou regra de negócio;
- `429 Too Many Requests`: throttling;
- `500 Internal Server Error`: falha inesperada sem detalhe sensível.

Os `ValidationError` do DRF serão normalizados para `422`.

## Componentes

### `APIError`

Exceção base para erros deliberados da aplicação. Deve receber:

- código registrado;
- status HTTP;
- mensagem traduzível;
- campo/caminho opcionais;
- contexto opcional.

Erros de domínio poderão especializá-la, mas o payload final será sempre
produzido pelo handler central.

### Handler global

Um exception handler do DRF normalizará:

- `APIError`;
- `rest_framework.exceptions.APIException`;
- `serializers.ValidationError`;
- `django.core.exceptions.ValidationError`;
- falhas de parsing;
- autenticação e permissions;
- throttling;
- `Http404` e `PermissionDenied`;
- exceções inesperadas.

Erros aninhados do serializer serão percorridos recursivamente para formar
`field` e `path`.

### Middleware e handlers de status

O middleware de autenticação e os handlers `400/401/403/404/500` deixarão de
montar payloads próprios. Todos usarão o mesmo construtor/renderer de erros,
inclusive quando a resposta ocorrer antes do dispatch do DRF.

### Observabilidade

- O envelope sempre inclui `request_id`.
- O erro registrado em log carrega `code`, status e path, quando seguro.
- Respostas `500` usam mensagem genérica.
- Tracebacks permanecem somente nos canais internos de observabilidade.
- Tokens, credenciais e valores de campos sensíveis serão removidos de
  `context`, logs e eventos.

## OpenAPI

Haverá schemas reutilizáveis para:

- `APIErrorItem`;
- `APIErrorResponse`;
- erros de validação;
- erros comuns `401`, `403`, `404`, `422` e `429`.

Quando possível, cada operação documentará os códigos de domínio que pode
produzir. O registry será a fonte de descoberta dos códigos disponíveis.

## Compatibilidade

O projeto ainda não foi lançado e não manterá o formato legado de
`{"detail": ...}` ou `{"mensagem": ...}`. Os endpoints existentes serão
normalizados para o novo contrato.

## Testes

Os testes devem cobrir:

- unicidade e formato dos códigos;
- normalização das exceções suportadas;
- validações simples, aninhadas e em listas;
- múltiplos erros na mesma resposta;
- tradução conforme `Accept-Language`;
- inclusão do `request_id`;
- mapeamento de status HTTP;
- throttling;
- handlers executados fora do DRF;
- documentação OpenAPI;
- ausência de traceback e dados sensíveis em respostas `500`;
- ausência de segredos em `context` e logs.

