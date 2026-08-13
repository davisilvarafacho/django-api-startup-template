# Framework interno de variáveis de contexto — Design

## Objetivo

Criar um framework interno, genérico e tipado para armazenar objetos no contexto
de execução corrente por meio de `contextvars.ContextVar`. Cada instância da
classe pública representará uma variável independente e oferecerá uma API que o
IntelliSense e os verificadores estáticos consigam compreender.

O framework substituirá o uso de `django-threadlocals` no projeto, mas não
conhecerá request, usuário, autenticação, Django ou qualquer outro domínio. As
variáveis específicas da aplicação serão apenas instâncias declaradas fora da
classe genérica.

Apesar de substituir variáveis de thread legadas, a nova abstração será uma
variável de **contexto de execução**. Isso preserva o isolamento tanto entre
threads quanto entre tasks assíncronas concorrentes.

## API pública

O pacote ficará em `internal_frameworks/context/` e exportará:

```python
from internal_frameworks.context import (
    ContextVariable,
    ContextVariableNotSetError,
)
```

Uma variável será criada pela factory tipada `from_var()`:

```python
usuario_atual = ContextVariable[Usuario].from_var("usuario_atual")
contador = ContextVariable[int].from_var("contador", default=0)
```

Chamadas distintas sempre criarão instâncias e `ContextVar`s independentes,
mesmo quando receberem o mesmo nome. Não haverá registro global por nome nem
resolução implícita de variáveis.

Cada instância oferecerá estas operações:

```python
token = usuario_atual.set(usuario)

usuario_atual.get()
usuario_atual.get(raise_exception=True)
usuario_atual.is_set()

usuario_atual.reset(token)
usuario_atual.clear()

with usuario_atual.use(outro_usuario):
    ...

ContextVariable.clear_context()
```

`set()` devolverá o token nativo necessário para `reset()`. Esse token será
tratado como opaco pelos consumidores. `use()` será um context manager que fará
o `set()` na entrada e o `reset()` no `finally`, restaurando corretamente o
estado anterior em blocos aninhados e quando o bloco levantar uma exceção.

## Ausência, valor padrão e `None`

Um sentinela privado distinguirá uma variável ausente de uma variável definida
explicitamente como `None`.

Quando a variável estiver ausente:

- `get()` devolverá o `default` configurado em `from_var()`;
- o `default` será `None` quando não for informado;
- `get(raise_exception=True)` levantará
  `ContextVariableNotSetError`, mesmo que a instância tenha outro `default`;
- `is_set()` devolverá `False`.

Depois de `set(None)`, a variável será considerada definida. Portanto,
`is_set()` devolverá `True` e `get(raise_exception=True)` devolverá `None` se o
tipo da instância admitir esse valor, por exemplo:

```python
usuario_opcional = ContextVariable[Usuario | None].from_var("usuario_opcional")
usuario_opcional.set(None)
```

`clear()` voltará a variável ao estado ausente apenas no contexto de execução
corrente. Uma leitura posterior observará novamente o `default`.

## Tipagem e IntelliSense

`ContextVariable` será genérica no tipo do valor armazenado. `from_var()`
preservará o parâmetro aplicado à classe:

```python
exemplo = ContextVariable[MeuTipo].from_var("exemplo")
```

`get()` terá overloads baseados em `Literal`:

```python
exemplo.get()                       # MeuTipo | None
exemplo.get(raise_exception=False) # MeuTipo | None
exemplo.get(raise_exception=True)  # MeuTipo
```

Se `MeuTipo` incluir `None`, o retorno com `raise_exception=True` também poderá
ser `None`, pois esse pode ser um valor explicitamente definido. O valor
informado em `default` deverá ser compatível com o parâmetro genérico.

As assinaturas de `set()` e `use()` aceitarão somente esse mesmo tipo. `reset()`
aceitará o token produzido por `set()`. A implementação usará `Self` na factory
e anotações compatíveis com a versão mínima de Python do projeto.

## Registro e limpeza coletiva

Cada instância encapsulará seu próprio `ContextVar`. Além disso, a classe
manterá um registro fraco das instâncias vivas para implementar:

```python
ContextVariable.clear_context()
```

O registro será um `WeakSet`, de modo que não prolongue a vida de variáveis que
deixaram de ser usadas. Um lock protegerá somente a inclusão e a obtenção de uma
fotografia estável do registro. As operações sobre os valores continuarão sob a
semântica nativa e context-local de `ContextVar`.

`clear_context()` percorrerá essa fotografia e chamará `clear()` em cada
instância. A operação:

- limpará valores somente no contexto de execução que fez a chamada;
- não excluirá instâncias;
- não afetará outras threads ou tasks assíncronas;
- não modificará os valores padrão das instâncias;
- retornará `None` e será segura quando nenhuma variável estiver definida.

O custo será linear na quantidade de instâncias vivas. Essa escolha mantém uma
`ContextVar` real por instância e evita o estado mutável compartilhado que seria
necessário para guardar todas as variáveis em um único dicionário contextual.

Uma task assíncrona criada enquanto há valores definidos recebe sua própria
cópia do contexto segundo a semântica de `contextvars`. Limpar posteriormente o
contexto pai não apaga a cópia da task filha, e limpar a filha não afeta o pai.

## Erros

`ContextVariableNotSetError` será a exceção própria para a leitura obrigatória
de uma variável ausente. A mensagem identificará o nome recebido por
`from_var()`, sem conter conhecimento de domínio.

`reset()` delegará ao `ContextVar` encapsulado as validações de token. Dessa
forma, token pertencente a outra variável, token criado em outro contexto e
token reutilizado manterão os erros nativos da biblioteca padrão.

Erros de tipo em valores são responsabilidade da análise estática; o framework
não executará validação por `isinstance` nem receberá uma classe de tipo em
runtime.

## Integração com Django

As instâncias específicas da aplicação ficarão em um módulo da camada de API,
fora de `internal_frameworks.context`. A forma conceitual será:

```python
request_atual = ContextVariable[HttpRequest].from_var("request_atual")
usuario_atual = ContextVariable[Usuario | AnonymousUser].from_var("usuario_atual")
token_atual = ContextVariable[AuthToken].from_var("token_atual")
request_id = ContextVariable[str].from_var("request_id")
```

Esses nomes representam o uso da aplicação, não membros especiais ou
conhecimento embutido em `ContextVariable`.

Um middleware Django fino controlará o ciclo de vida da request:

```python
def __call__(self, request):
    request_atual.set(request)
    try:
        return self.get_response(request)
    finally:
        ContextVariable.clear_context()
```

O middleware ficará externamente aos middlewares que publicam `request_id`,
usuário e token. Assim, seu `finally` abrangerá respostas normais, respostas
antecipadas de autenticação e exceções. O framework continuará independente de
Django; somente o middleware importará tanto Django quanto a classe genérica.

A limpeza coletiva será uma barreira final contra vazamentos, não um mecanismo
de propagação. Valores que precisem atravessar a fronteira para uma task Celery
continuarão sendo serializados explicitamente nos headers da mensagem.

## Migração do código legado

A implementação migrará os consumidores atuais sem adicionar casos especiais à
classe genérica:

1. o request atual será publicado pelo novo middleware;
2. a autenticação publicará o usuário e o token resolvidos nas instâncias da
   aplicação;
3. a autoria automática dos models lerá o usuário atual, mantendo fora do
   framework o fallback controlado para `request_atual.get().user` necessário à
   compatibilidade;
4. o filtro de logging lerá a instância do request atual;
5. o contexto adicional dos serializers lerá o token atual;
6. `request_id.py` usará a instância genérica internamente e preservará seus
   helpers públicos atuais, evitando uma mudança de API sem relação com o
   objetivo;
7. os sinais do Celery continuarão propagando o request ID explicitamente e
   limparão o contexto ao encerrar a task;
8. testes que hoje manipulam `set_current_user()` ou
   `set_thread_variable()` passarão a usar as instâncias, preferencialmente por
   meio de `use()`;
9. `threadlocals.middleware.ThreadLocalMiddleware` será removido da cadeia de
   middlewares;
10. `django-threadlocals` será removido de `pyproject.toml` e `uv.lock`.

A migração será considerada completa somente quando não restarem imports ou
referências executáveis a `django-threadlocals`. Documentação e comentários que
descrevam o mecanismo antigo também serão atualizados.

## Organização do código

A organização prevista é:

```text
internal_frameworks/context/
├── __init__.py
├── variable.py
└── tests/
    ├── __init__.py
    └── test_variable.py
```

`variable.py` concentrará o sentinela privado, a exceção e a classe genérica.
`__init__.py` definirá a superfície pública estável. O middleware e as instâncias
de domínio ficarão em módulos da aplicação, e não no pacote do framework.

## Testes

Os testes unitários do framework cobrirão:

- `None` como padrão implícito e um padrão explícito;
- `get(raise_exception=True)` em variável ausente, com e sem padrão;
- distinção entre ausência e `set(None)`;
- `set()`, `get()`, `reset()`, `clear()` e `is_set()`;
- restauração por `use()` na saída normal, em blocos aninhados e após exceção;
- independência de instâncias com o mesmo nome;
- erros nativos ao usar token inválido ou reutilizado;
- limpeza de todas as instâncias vivas no contexto corrente;
- remoção automática de instâncias sem referências do registro fraco;
- isolamento de valores e da limpeza entre threads;
- isolamento de valores e da limpeza entre tasks `asyncio` concorrentes;
- inferência estática do retorno opcional e do retorno obrigatório.

Os testes de integração cobrirão:

- publicação e limpeza do request em resposta normal e exceção;
- limpeza após respostas antecipadas da autenticação;
- publicação de usuário e token autenticados;
- autoria automática por usuário explícito e pelo fallback do request;
- preenchimento do contexto de logging;
- disponibilidade e limpeza do request ID;
- propagação explícita do request ID ao Celery;
- ausência de vazamento entre requests e entre tasks sequenciais;
- comportamento existente dos endpoints afetados pela migração.

Ao final, lint, testes focados, suíte aplicável e busca por referências a
`django-threadlocals` validarão a remoção do legado.

## Fora do escopo

Esta entrega não incluirá:

- variáveis especiais codificadas dentro de `ContextVariable`;
- registro ou lookup de variáveis pelo nome;
- serialização ou propagação automática para Celery, subprocessos ou serviços;
- validação runtime do tipo genérico;
- limpeza global de outras threads ou tasks;
- integração do framework com RLS, cujo contexto pertence a outra biblioteca e
  possui semântica transacional própria.
