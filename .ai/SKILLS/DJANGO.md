# Skill: Django

Boas práticas para desenvolvimento web com Django, incluindo models, views, templates e testes.

## Quando Usar

Aplique esta skill ao trabalhar com projetos Django — models, views, roteamento de URLs, templates, forms, admin e management commands.

## Estrutura do Projeto

- Siga a seguinte estrutura padrão de apps django para api: `models.py`, `views.py`, `urls.py`, `admin.py`, `tests/*.py`, `handlers.py`.
- Mantenha cada app focado em um único conceito de domínio; evite "god apps" com models não relacionados entre si.
- Mantenha subapps

## Models

- Sempre defina `__str__` nos models para facilitar leitura no admin e em debug.
- Use `Meta.ordering` com moderação — isso adiciona `ORDER BY` em toda query. Prefira `.order_by()` explícito no queryset.
- Use índices de banco de dados (`db_index=True`, `Meta.indexes`) para campos usados em `filter()` / `order_by()`.
- Quando houver um modelo que representa lógicamente vários models, utilize indexes compostos e parciais.
- Prefira `PositiveSmallIntegerField` com `IntegerChoices` em vez de valores soltos para campos com valores restritos.
- Use expressões `F()` e objetos `Q()` para queries complexas, evitando race conditions e melhorando a legibilidade.
- Sempre definir `db_commet` e `help_text` e com os mesmos textos

## Views

- Use `select_related()` e `prefetch_related()` para evitar problemas de N+1 queries.
- Sempre defina explicitamente `queryset` ou sobrescreva `get_queryset()` — nunca dependa de estado mutável em nível de classe.

## Testes

- Use `pytest-django` com `@pytest.mark.django_db` para acesso ao banco de dados.
- Prefira `TestCase` ou `TransactionTestCase` apenas quando houver necessidade de controle explícito de transação; caso contrário, use fixtures do pytest.
- Use `RequestFactory` ou `Client` para testar views sem precisar iniciar um servidor.
- Use `baker.make()` (model-bakery) ou factories em vez de construção manual de models nos testes.

## Pitfaitls

- Nunca faça I/O bloqueante em views assíncronas sem envolver em `sync_to_async`.
- Evite importar models no nível de módulo em `settings.py` ou `urls.py` (importações circulares).
- Nunca armazene segredos em `settings.py` — use variáveis de ambiente.
- Evite SQL puro, a menos que o ORM genuinamente não consiga expressar a query.

## Organização

- A lógica da regra negócio deve ficar apenas dentro de `handlers.py` (service layer).


## Para organizar

- Usar `Queryset.update` faz com que os logs do auditlog não sejam gravados. Evite usar em contextos onde é importante ele ser auditável


## Dogmas

- SRP
- OPC
- Simples e centralizado
