# Framework interno de guardrails — Design

## Objetivo

Adicionar um framework interno para observar a execução de funções síncronas e
detectar violações de limites operacionais. O primeiro check limitará a quantidade
de consultas SQL, mas o contrato deve permitir checks futuros de tempo de
execução, cache e integrações externas sem alterar o decorator.

Guardrails são diagnósticos internos para desenvolvedores. Eles não representam
erros de domínio nem respostas públicas da API.

## API pública

O uso básico emitirá warnings e preservará o retorno da função:

```python
from internal_frameworks.guardrails import TooManySqlQueriesCheck, guardrail


@guardrail(checks=[TooManySqlQueriesCheck(max_queries=10)])
def list_users():
    ...
```

O desenvolvedor poderá transformar violações em erro de forma explícita:

```python
@guardrail(
    checks=[TooManySqlQueriesCheck(max_queries=10)],
    raise_exception=True,
)
def list_users():
    ...
```

A assinatura do decorator será:

```python
guardrail(*, checks: Sequence[GuardrailCheck], raise_exception: bool = False)
```

`raise_exception` não dependerá do ambiente:

- `False`, o padrão, emite um warning por check violado e devolve normalmente o
  resultado da função;
- `True` agrega todas as violações e sempre levanta
  `GuardrailViolationError`.

Quando a política realmente depender do ambiente, o chamador poderá passar
explicitamente `settings.IN_DEVELOPMENT`. Esse valor é avaliado durante a
importação do módulo, limitação que deve ser considerada em testes que alteram
settings dinamicamente.

O pacote exportará sua API estável pela raiz:

```python
from internal_frameworks.guardrails import (
    GuardrailCheck,
    GuardrailViolation,
    GuardrailViolationError,
    GuardrailViolationWarning,
    TooManySqlQueriesCheck,
    guardrail,
)
```

## Contrato dos checks

Todos os checks concretos herdarão de `GuardrailCheck`, uma classe abstrata com
dois hooks obrigatórios:

```python
class GuardrailCheck(ABC):
    @abstractmethod
    def start(self) -> object:
        """Inicia a observação e devolve o estado exclusivo desta execução."""

    @abstractmethod
    def finish(self, state: object) -> GuardrailViolation | None:
        """Finaliza a observação e devolve uma violação, se houver."""
```

As instâncias guardarão somente configuração. Todo estado mutável criado para
uma chamada deve ser devolvido por `start()` e recebido por `finish()`. Isso
permite reutilizar com segurança a mesma instância configurada pelo decorator em
chamadas simultâneas.

Cada check produzirá no máximo uma violação por chamada. A agregação entre checks
é responsabilidade do decorator.

## Representação das violações

Uma violação será um valor estruturado com atributos imutáveis:

```python
@dataclass(frozen=True, slots=True)
class GuardrailViolation:
    check: str
    message: str
    context: Mapping[str, object]
```

`check` identificará a classe concreta, `message` será uma descrição humana
estável e `context` conterá apenas metadados escalares e seguros para
diagnóstico. A construção copiará o mapping recebido para impedir que alterações
posteriores do chamador modifiquem a violação. Segredos, payloads e SQL bruto não
devem ser incluídos.

`GuardrailViolationError` herdará de `RuntimeError`, e não de `APIError`. Uma
violação de guardrail é um problema interno de desenvolvimento e não deve ganhar
status HTTP, código público nem ser confundida com uma condição esperada da API.
A exceção conterá uma tupla ordenada com todas as violações.

`GuardrailViolationWarning` será uma categoria única e estável, derivada de
`Warning`. Cada instância conterá exatamente uma `GuardrailViolation`. Não serão
criadas subclasses dinâmicas por check: ferramentas de observabilidade agrupam
melhor eventos com categoria e formato estáveis e campos identificadores.

Para duas violações, o framework chamará `warnings.warn(...)` duas vezes, uma por
check. A apresentação ou supressão de chamadas repetidas continuará obedecendo
aos filtros configurados no módulo `warnings`.

## Fluxo de execução

Ao decorar a função, o framework:

1. materializa `checks` como uma tupla, impedindo que mutações posteriores da
   sequência alterem o comportamento;
2. exige pelo menos um check;
3. verifica que todos os itens são instâncias de `GuardrailCheck`;
4. exige que `raise_exception` seja estritamente um `bool`;
5. recusa coroutine functions e generator functions, que estão fora do escopo
   síncrono desta versão;
6. preserva nome, documentação e demais metadados com `functools.wraps`.

Em cada chamada, o wrapper:

1. executa `start()` na ordem declarada e guarda separadamente o estado de cada
   check;
2. chama a função decorada uma única vez;
3. executa `finish()` em ordem inversa, como ocorre na liberação de recursos
   aninhados;
4. organiza as violações conforme a ordem original dos checks;
5. devolve o resultado sem alteração quando não há violações;
6. emite warnings individuais ou levanta a exceção agregada conforme
   `raise_exception`.

Se a função decorada levantar uma exceção, todos os checks iniciados serão
finalizados para liberar recursos. Violações encontradas durante essa
finalização serão descartadas, e a exceção original será propagada sem ser
substituída por `GuardrailViolationError`.

Falhas levantadas pelo próprio `start()` ou `finish()` não serão convertidas em
violações: elas indicam um check defeituoso ou uma falha da infraestrutura usada
para observar a execução. O framework ainda tentará finalizar todos os checks que
já tenham sido iniciados.

A primeira exceção da função ou de `start()` será sempre a exceção principal;
eventuais falhas posteriores de `finish()` serão registradas nela com
`BaseException.add_note()` e não a mascararão. Quando apenas `finish()` falhar, o
framework concluirá as demais finalizações, levantará a primeira falha e anexará
as seguintes como notes. Essas falhas internas independem de `raise_exception`.

## Check de consultas SQL

O primeiro check concreto será configurado por instância:

```python
TooManySqlQueriesCheck(max_queries=10, using="default")
```

- `max_queries` será um inteiro maior ou igual a zero;
- `using` identificará um alias presente em `django.db.connections` e terá
  `"default"` como valor padrão;
- o check usará `CaptureQueriesContext` apenas para a conexão indicada;
- executar exatamente `max_queries` será permitido;
- executar mais que o limite produzirá uma violação.

O contexto observável será limitado a:

```python
{
    "database": "default",
    "max_queries": 10,
    "observed_queries": 14,
}
```

A mensagem e o contexto não conterão comandos SQL, parâmetros de queries nem
resultados do banco.

## Observabilidade

O framework emitirá warnings Python, sem depender diretamente de logging,
Sentry ou outro fornecedor. A categoria estável, o nome do check e o contexto
estruturado tornam os eventos identificáveis, mas `warnings.warn` por si só não
garante entrega a uma plataforma externa.

A configuração operacional poderá encaminhar warnings para logging com
`logging.captureWarnings(True)` ou usar a integração equivalente da ferramenta
adotada. Esse encaminhamento fica fora do núcleo e fora da primeira
implementação.

## Organização do código

O framework ficará em um pacote Python comum, sem `AppConfig`, models ou
migrations:

```text
internal_frameworks/guardrails/
├── __init__.py
├── base.py
├── decorator.py
├── violations.py
├── checks/
│   ├── __init__.py
│   └── sql.py
└── tests/
    ├── __init__.py
    ├── test_decorator.py
    └── test_sql.py
```

- `base.py` definirá `GuardrailCheck`;
- `violations.py` definirá o valor, a exceção e o warning;
- `decorator.py` coordenará o ciclo de vida e aplicará a política escolhida;
- `checks/sql.py` implementará `TooManySqlQueriesCheck`;
- `__init__.py` definirá a superfície pública do pacote.

## Testes

Os testes unitários do decorator cobrirão:

- preservação de argumentos, retorno e metadados da função;
- ordem de `start()` e ordem inversa de `finish()`;
- rejeição de lista vazia, objetos que não são checks, booleano inválido,
  coroutine functions e generator functions;
- retorno normal quando não há violações;
- `raise_exception=False` como padrão;
- um warning por check violado;
- agregação na ordem declarada em `GuardrailViolationError`;
- comportamento de `raise_exception=True` independente do ambiente;
- finalização dos checks e preservação da exceção original;
- liberação dos checks já iniciados quando um `start()` falha;
- precedência e notes quando `finish()` também falha durante a limpeza;
- isolamento do estado entre chamadas da mesma função decorada.

Os testes de `TooManySqlQueriesCheck` cobrirão:

- limite exato aceito e excesso rejeitado;
- zero queries e limite zero;
- validação de `max_queries`;
- seleção do alias do banco;
- contagem restrita à conexão escolhida;
- ausência de SQL bruto na mensagem e no contexto.

## Documentação

A implementação adicionará uma página curta de referência com:

- exemplos nos modos warning e exceção;
- contrato para criação de novos checks;
- limitações para async e generators;
- orientação para encaminhar warnings à observabilidade.

## Fora de escopo

- funções assíncronas e generators;
- integração direta com Sentry ou outro fornecedor;
- logging duplicado de cada warning;
- subclasses dinâmicas de warning por check;
- configuração global de limites em Django settings;
- ativação ou desativação global do framework;
- checks adicionais além de `TooManySqlQueriesCheck`.
