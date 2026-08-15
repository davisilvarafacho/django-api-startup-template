# Validação da spec do mini MCP de saúde do Django

Data da validação: 2026-08-15

Spec avaliada: `docs/superpowers/specs/2026-08-13-mini-mcp-django-health-design.md`

Escopo das fontes: somente PyPI, documentação, releases e código oficiais do
Model Context Protocol e documentação oficial do Django 5.2.

## Conclusão

A proposta é viável, mas a spec **não deve ser implementada literalmente**.
Existe um erro bloqueante: a linha estável `mcp` 2 removeu `FastMCP`; a classe de
alto nível atual é `MCPServer`. Também é necessário atualizar a forma descrita
para o teste em memória, explicitar a ordem de bootstrap do Django e fechar
algumas ambiguidades de segurança, serialização e ciclo de vida das conexões.

Depois das correções abaixo, o desenho pode seguir sem mudança de objetivo:
servidor local, um único tool `health`, transporte `stdio`, dependência base do
SDK, consultas `SELECT 1` e round-trip de cache.

## Conflitos com o repositório atual

Além da quebra do SDK, a spec antecede contratos que hoje são normativos no
repositório e não deve ser implementada literalmente por quatro motivos.

### O health já existe

`apps/api/core/health_check.py` já implementa liveness e readiness para banco,
cache, broker e storage, expostos em `/health/` e `/health/ready/`. Criar toda a
checagem novamente dentro do adapter MCP produziria duas implementações para o
mesmo mecanismo.

A spec corrigida deve preservar a interface MCP pequena e extrair/reutilizar no
`core` os primitivos de verificação de banco e cache. O adapter MCP pode ter
política própria de agregação e sanitização, mas não deve duplicar cursor,
round-trip, timeout e limpeza. O endpoint atual inclui mensagens de exceção na
resposta; essa representação não pode atravessar a interface MCP, que promete
expor somente `error_type`.

Consequentemente, o item fora de escopo "não haverá endpoint REST de saúde" é
factualmente falso no estado atual. A redação correta é que esta entrega **não
criará, substituirá nem mudará o contrato** dos endpoints REST existentes.

### O pacote de topo não pertence ao mapa de diretórios

`docs/reference/estrutura-de-diretorios.md` é autoritativo e atribui health e
infraestrutura global a `apps/api/core/`; a raiz não prevê um pacote Python
operacional `mcp_server/`. A localidade mais forte é manter o adapter em
`apps/api/core/mcp_server.py`, seus testes em
`apps/api/core/tests/test_mcp_server.py` e executar:

```bash
uv run --frozen python -m apps.api.core.mcp_server
```

Isso concentra o adapter junto ao mecanismo que ele expõe, sem criar uma nova
camada de topo ou um app Django artificial.

### O bootstrap proposto não carrega `.env`

As entradas atuais `manage.py`, `api/asgi.py` e `api/wsgi.py` chamam
`dotenv.load_dotenv()` antes de inicializar Django. `api/settings.py` lê apenas
`os.environ`; portanto, `setdefault` seguido diretamente de `django.setup()` não
carrega o `.env` local e pode iniciar o MCP com banco/cache/defaults diferentes
dos usados pela aplicação.

O bootstrap deve seguir:

1. `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")`;
2. `dotenv.load_dotenv(override=False)`;
3. `django.setup()`;
4. imports dependentes de Django e registro do tool;
5. `mcp.run(transport="stdio")` sob o guard de `__main__`.

O `cwd` documentado para o host MCP deve ser a raiz do projeto, para que a
descoberta de `.env` seja determinística. Variáveis fornecidas pelo launcher
continuam tendo precedência porque `override=False`.

### A dependência é de desenvolvimento

O objetivo declara um servidor exclusivamente local. Para não levar o SDK MCP
e suas dependências ao runtime/imagem de produção, a spec deve mandar executar
`uv add --group dev "mcp>=2,<3"`, atualizando `pyproject.toml` e `uv.lock`. A
documentação completa pertence a um how-to em `docs/`; o README pode apenas
apontar para ele.

## Veredito por afirmação

| Afirmação da spec | Veredito | Correção |
| --- | --- | --- |
| `mcp>=2,<3` é a linha estável | Correta em 2026-08-15 | A versão estável publicada é 2.0.0. Pode-se tornar o limite inferior explícito como `mcp>=2.0.0,<3`; o lock registrará a versão exata. |
| A interface da linha 2 é `FastMCP` | **Incorreta e bloqueante** | Usar `from mcp.server import MCPServer` e `mcp = MCPServer("django-health")`. O caminho `mcp.server.fastmcp` não existe em 2.0.0. |
| O extra `cli` não é necessário | Correta para execução direta de um módulo Python | `python -m apps.api.core.mcp_server` usa a API da biblioteca; `mcp[cli]` só é necessário para os comandos `mcp dev`, `mcp run` e `mcp install`. |
| Python 3.12 é compatível | Correta | O pacote exige Python 3.10+ e publica classifier para Python 3.12. |
| `stdio` pode ser iniciado com `run` | Correta | `MCPServer.run()` é síncrono, bloqueante e usa `stdio` por padrão; `mcp.run(transport="stdio")` também é válido. |
| Uma “sessão MCP em memória” testa registro e chamada | Conceito correto, API/termo imprecisos | Na linha 2, usar `Client(mcp)`. O protocolo 2026-07-28 não depende de sessão; `ClientSession` continua apenas como camada interna/baixo nível. |
| O retorno mostrado é serializável como JSON | Correta, mas insuficientemente especificada | Declarar um tipo de retorno estruturado (`TypedDict` ou modelo Pydantic) para o SDK publicar e validar `output_schema`. Um `dict` genérico não fixa o contrato aninhado. |
| O SDK evitará vazamento de erro | **Não é verdade automaticamente para tools** | Toda exceção de banco/cache deve ser capturada dentro do tool. Exceções comuns que escapam de um tool viram `is_error=True` com a mensagem original visível ao modelo. |

Fontes: [release oficial 2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0),
[PyPI do `mcp` 2.0.0](https://pypi.org/project/mcp/2.0.0/),
[migração de `FastMCP` para `MCPServer`](https://py.sdk.modelcontextprotocol.io/whats-new/#fastmcp-is-now-mcpserver)
e [execução do servidor](https://py.sdk.modelcontextprotocol.io/run/).

## 1. SDK, interface e transporte

Em 28 de julho de 2026 foi publicado `mcp` 2.0.0 como release estável. O PyPI
o classifica como `Production/Stable`, exige Python 3.10+ e inclui Python 3.12
entre as versões suportadas. Portanto, o intervalo de dependência da spec é
compatível com o projeto.

O erro é o nome da interface. A documentação oficial diz que a troca não foi
uma depreciação: o import antigo foi removido. A forma correta é:

```python
from mcp.server import MCPServer

mcp = MCPServer("django-health")
```

Os decorators permanecem (`@mcp.tool()`), assim como `mcp.run()`. O `run()` sem
argumentos seleciona `stdio`; passar `transport="stdio"` é apenas mais
explícito.

Executar diretamente um módulo Python continua válido para o SDK. Ajustado à
estrutura deste repositório, o comando é:

```bash
uv run --frozen python -m apps.api.core.mcp_server
```

Para uma configuração de host mais reprodutível, vale documentar
`--frozen`, evitando qualquer resolução inesperada durante o boot. Isso é
endurecimento operacional, não requisito do SDK.

Fontes: [README oficial da linha 2](https://github.com/modelcontextprotocol/python-sdk/tree/v2.0.0#readme),
[guia de migração](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/docs/migration.md#fastmcp-renamed-to-mcpserver)
e [dependências/extras do pacote](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/pyproject.toml).

## 2. `stdio` e disciplina de `stdout`

O desenho está correto ao reservar `stdin` e `stdout` para o protocolo. Há,
porém, uma nuance importante na linha 2:

- enquanto o servidor está servindo, o SDK mantém o protocolo em descritores
  privados e desvia saídas indevidas de `stdout` para `stderr`;
- saída emitida e descarregada **antes** de `mcp.run()` ainda pode cair no fio;
- saída bufferizada que só seja descarregada no encerramento também pode
  corromper o protocolo.

Logo, a regra deve cobrir todo o bootstrap: o módulo
`apps.api.core.mcp_server`, imports do Django e registro do tool não podem usar
`print()` em `stdout`. `logging` é o caminho correto, mas a garantia deve ser
“handler em `stderr`”, não apenas “usar logging”. A configuração atual do
projeto usa `logging.StreamHandler` sem `stream` customizado, cujo destino
padrão é `stderr`; a implementação não deve mudar isso para o processo MCP.

Um teste somente em memória não exercita esse risco. Além dele, deve haver um
smoke test de subprocesso com `stdio_client(StdioServerParameters(...))` que
inicie `python -m apps.api.core.mcp_server` e ao menos execute `list_tools()`.

Fontes: [documentação oficial de `stdio`](https://py.sdk.modelcontextprotocol.io/run/#stdio),
[implementação do servidor `stdio` em 2.0.0](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/src/mcp/server/stdio.py)
e [transporte cliente `stdio`](https://py.sdk.modelcontextprotocol.io/client/transports/#stdio).

## 3. Bootstrap do Django e ordem de imports

`os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")` respeita o
ambiente fornecido pelo cliente e é a escolha correta. O Django exige que um
programa standalone configure as settings e chame `django.setup()` antes de
importar código que usa componentes dependentes do app registry.

A spec deve fixar a seguinte ordem dentro de
`apps.api.core.mcp_server`:

1. aplicar `setdefault`;
2. carregar `.env` sem sobrescrever variáveis do processo;
3. chamar `django.setup()`;
4. somente então importar código que acessa settings, conexões ou caches e
   registrar `MCPServer`;
5. chamar `mcp.run()` sob `if __name__ == "__main__":`.

Nos testes em memória, `pytest-django` prepara o Django antes da importação do
servidor. O módulo testável não deve iniciar `mcp.run()` ao ser importado.

Fonte: [uso standalone e `django.setup()` no Django 5.2](https://docs.djangoproject.com/en/5.2/topics/settings/#calling-django-setup-is-required-for-standalone-django-usage).

### Ciclo de vida das conexões

Há uma omissão adicional. O MCP é um processo longo fora do ciclo de
request/response do Django. A documentação do Django alerta que conexões
criadas assim permanecem abertas até fechamento explícito ou timeout. Além
disso, o `MCPServer` executa tools síncronos em uma worker thread do AnyIO.

A implementação deve:

- declarar `health` como função síncrona, apropriada para drivers Django
  síncronos;
- obter `connections[alias]` e `caches[alias]` dentro do corpo do tool, nunca
  capturar wrappers em import time ou em outra thread;
- fechar cada conexão de banco em `finally` depois do check, ou documentar e
  testar outra política explícita de reuso.

Fontes: [conexões em processos longos no Django](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections)
e [execução de callables síncronos pelo SDK](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/src/mcp/server/mcpserver/utilities/func_metadata.py#L98-L108).

## 4. Teste MCP em memória

A capacidade existe oficialmente, mas a redação deve ser atualizada para a
API da linha 2. O teste recomendado é:

```python
from mcp import Client

async with Client(mcp, raise_exceptions=True) as client:
    tools = await client.list_tools()
    assert [tool.name for tool in tools.tools] == ["health"]

    result = await client.call_tool("health")
    assert result.is_error is False
    assert result.structured_content == expected
```

Esse caminho atravessa a camada real do protocolo, registra, lista, valida e
invoca o tool, sem subprocesso ou porta. Não se deve usar a receita antiga
`create_connected_server_and_client_session()` nem importar
`mcp.client.session.ClientSession` para este teste.

`raise_exceptions=True` ajuda a revelar falhas fora do corpo do tool. Ele não
reverte exceções de tool: elas já chegam como resultado `is_error=True`.

Fonte: [guia oficial de testes do SDK 2.0.0](https://github.com/modelcontextprotocol/python-sdk/blob/v2.0.0/docs/get-started/testing.md).

## 5. Serialização e contrato de resposta

O JSON da spec é compatível com o SDK, mas o contrato deve aparecer no tipo de
retorno. O SDK 2 deriva `output_schema` da anotação, valida o valor retornado e
o entrega em dois canais:

- `structured_content`, para o cliente;
- `content`, como texto JSON para o modelo.

`TypedDict`s distintos para item saudável e não saudável preservam exatamente
a regra de que `error_type` só existe na falha:

```python
class HealthyItem(TypedDict):
    status: Literal["healthy"]


class UnhealthyItem(TypedDict):
    status: Literal["unhealthy"]
    error_type: str


CheckItem = HealthyItem | UnhealthyItem
```

Outro `TypedDict` ou modelo Pydantic deve fixar `checks.databases`,
`checks.caches` e o status geral. Apenas anotar `-> dict` ou
`-> dict[str, object]` deixa a forma aninhada frouxa demais para a promessa de
formato estável.

Uma saúde operacional `unhealthy` deve continuar sendo uma chamada MCP bem
sucedida (`result.is_error is False`); a falha faz parte dos dados do tool, não
do protocolo.

Fonte: [structured output no SDK 2](https://py.sdk.modelcontextprotocol.io/servers/structured-output/).

## 6. Segurança e tratamento de erros

A decisão de retornar apenas `type(exc).__name__` é adequada, desde que a
exceção nunca atravesse a fronteira do tool. O SDK não mascara uma exceção
comum levantada pelo corpo de um tool: ele produz `is_error=True` e coloca a
mensagem original em `content`, visível ao modelo. Mensagens de drivers podem
conter host, porta, nome do banco, URL ou outros detalhes operacionais.

Requisitos concretos para cumprir a promessa da spec:

- capturar exceções separadamente por alias;
- construir `error_type` somente com `type(exc).__name__`;
- nunca retornar, concatenar ou serializar `str(exc)`, `repr(exc)` ou traceback;
- testar com uma exceção cuja mensagem contenha um segredo sintético e afirmar
  que ele não aparece nem em `structured_content` nem em `content`;
- definir se a mesma regra vale para `stderr`. Se valer, não registrar
  `exc_info=True` nem a mensagem bruta; se não valer, limitar expressamente a
  garantia ao payload MCP.

O transporte `stdio` não possui autenticação. A documentação oficial define
como fronteira de segurança o processo que iniciou o servidor. Portanto,
“local” deve significar “iniciado por um host/processo confiável com as mesmas
permissões do operador”. O tool revela aliases e disponibilidade da
infraestrutura; ele não deve ser exposto a um launcher não confiável.

O cliente `stdio` oficial também não herda todo o ambiente: por segurança, usa
uma allow-list pequena e combina apenas as variáveis passadas em `env=`. O
README deve alertar que as variáveis de banco, Redis e ambiente Django precisam
ser fornecidas pela configuração do host quando não existirem por outro meio.
`setdefault` continuará respeitando `DJANGO_SETTINGS_MODULE` fornecido pelo
cliente.

Fontes: [tratamento de erros de tools](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/),
[fronteira de segurança de `stdio`](https://py.sdk.modelcontextprotocol.io/run/authorization/#what-you-get-over-http)
e [ambiente do cliente `stdio`](https://py.sdk.modelcontextprotocol.io/client/transports/#stdio).

## 7. Ambiguidades funcionais a resolver antes do TDD

### Falha simultânea do round-trip e da limpeza

O schema comporta um único `error_type`, mas o round-trip e o `delete()` podem
falhar na mesma execução. A spec precisa definir precedência. A regra mais
simples é preservar o primeiro erro do round-trip; uma falha de limpeza só
define `error_type` quando nenhuma falha anterior existia. Em todos os casos, o
item fica `unhealthy`.

### Retornos falsos sem exceção

O Django documenta que `cache.delete()` retorna `True` quando remove e `False`
caso contrário. Se “falha de limpeza” inclui `False`, a spec deve nomear um erro
sintético, por exemplo `CacheCleanupError`, assim como já faz com
`CacheRoundTripError`. O TTL curto continua necessário como contenção quando
a limpeza realmente falha.

Também convém fixar o timeout (à ordem de dezenas de segundos), o prefixo da
chave e um valor compatível com todos os backends configurados. Uma chave com
UUID e valor string simples atende Redis, `LocMemCache` e o serializer JSON do
alias `permissions`.

Fonte: [API básica do cache no Django 5.2](https://docs.djangoproject.com/en/5.2/topics/cache/#basic-usage).

## Correções mínimas recomendadas para a spec

1. Trocar toda referência a `FastMCP` por `MCPServer` e registrar o import
   `from mcp.server import MCPServer`.
2. Manter `mcp>=2,<3` ou explicitar `mcp>=2.0.0,<3`; manter o pacote sem o extra
   `cli` para a execução por módulo.
3. Colocar o adapter em `apps/api/core/mcp_server.py`, reutilizar os primitivos
   de health do `core` e executar o módulo pelo dotted path completo.
4. Fixar o bootstrap `setdefault` → `load_dotenv(override=False)` →
   `django.setup()` → imports dependentes do Django → `mcp.run()`.
5. Substituir “sessão MCP em memória” por “cliente MCP em memória com
   `Client(mcp)`” e testar `list_tools()` + `call_tool("health")`.
6. Acrescentar um smoke test real de `stdio`/subprocesso para validar o ponto de
   entrada e a pureza de `stdout`.
7. Definir o retorno com `TypedDict` ou Pydantic e afirmar sobre
   `result.structured_content`.
8. Tornar obrigatória a captura por alias e testar a ausência de mensagens e
   segredos nos dois canais de resultado.
9. Definir ciclo de vida das conexões de banco no processo standalone e
   precedência entre erro primário e erro de cleanup do cache.
10. Explicitar que `stdio` não autentica: o launcher local confiável é a
   fronteira de segurança e deve fornecer as variáveis de ambiente necessárias.
11. Instalar `mcp>=2,<3` no grupo `dev`, documentar o uso em `docs/how-to/` e
    esclarecer que os endpoints REST de saúde existentes não mudam.

## Evidência prática complementar

Em ambiente isolado com `mcp==2.0.0` e Python compatível com o projeto:

- `from mcp.server.fastmcp import FastMCP` produziu
  `ModuleNotFoundError`;
- `from mcp.server import MCPServer` funcionou;
- `Client(mcp)` listou o tool `health` e o chamou sem subprocesso;
- um retorno composto por `TypedDict` gerou `output_schema`,
  `structured_content` com o JSON esperado e `content` com a representação
  textual equivalente.

Esses ensaios apenas confirmam as APIs descritas nas fontes oficiais; não
substituem os testes que serão implementados no projeto.
