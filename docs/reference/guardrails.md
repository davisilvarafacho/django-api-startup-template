# Guardrails internos

`internal_frameworks.guardrails` observa funções síncronas e sinaliza limites
operacionais para desenvolvimento. Não produz erros públicos da API.

```python
from internal_frameworks.guardrails import TooManySqlQueriesCheck, guardrail


@guardrail(checks=[TooManySqlQueriesCheck(max_queries=10)])
def list_users():
    ...
```

Por padrão, cada violação gera um `GuardrailViolationWarning` e a função mantém
seu retorno. Para falhar explicitamente, use `raise_exception=True`; todas as
violações são reunidas em `GuardrailViolationError`.

```python
@guardrail(
    checks=[TooManySqlQueriesCheck(max_queries=10)],
    raise_exception=True,
)
def list_users():
    ...
```

## Criando um check

Checks devem herdar de `GuardrailCheck`. Guarde apenas configuração na instância:
`start()` deve criar e devolver o estado exclusivo da chamada, que será entregue
a `finish()`. Este último retorna uma `GuardrailViolation` ou `None`.

```python
class MyCheck(GuardrailCheck):
    def start(self) -> object:
        return {"started": True}

    def finish(self, state: object) -> GuardrailViolation | None:
        return None
```

Funções assíncronas e generators não são suportados nesta versão. Para enviar os
warnings à plataforma de observabilidade, habilite o encaminhamento de warnings
no ambiente, por exemplo com `logging.captureWarnings(True)`.
