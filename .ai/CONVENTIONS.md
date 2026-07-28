# Convenções e padrões de desenvolvimento

Documento formal de padrões e convenções adotadas no desenvolvimento de APIs Django.
Consolida decisões arquiteturais e diretrizes obrigatórias para manutenção da
consistência, legibilidade e qualidade do código.

---

## 1. Arquitetura da API

### 1.1. Localização dos Apps

Todos os apps da aplicação **devem** ficar sempre dentro de uma pasta `apps/`, na
raiz do projeto. Não é permitido manter apps soltos fora dessa pasta.

### 1.2. Organização Modular

Cada módulo da aplicação (`models`, `serializers`, `views`, `filters`, `handlers`,
`docs`, `dashboards`) **deve** ser um **arquivo Python** único dentro do app.
`tests` é a **única exceção**: sempre um **pacote** (pasta `tests/` com `__init__.py`),
para comportar múltiplos arquivos de teste.

```txt
/apps
    /app
        models.py
        serializers.py
        views.py
        docs.py
        filters.py
        handlers.py
        dashboards.py
        /tests
            __init__.py
            test_*.py
```

### 1.3. QuerySets

- **Obrigatório** utilizar `select_related` (e, quando aplicável, `prefetch_related`)
  para evitar consultas N+1.
- **Sempre** utilizar `only()` (ou `values()`) para limitar as colunas carregadas
  quando nem todos os campos forem necessários.
- Campos pesados e raramente lidos podem ser adiados no model via
  `queryset_deferred_fields` (aplicado automaticamente pelo `CustomManager` da `Base`).
- Otimizações de N+1 dependentes do serializer devem ser co-localizadas nele via
  `setup_eager_loading` (ver §6).

---

## 2. Models

### 2.1. Choices

- Sempre utilizar as classes de choices do Django, preferencialmente `IntegerChoices`.
- A docstring de cada classe de choices **deve** indicar o modelo e a coluna onde é
  originalmente utilizada.
- **Devem** ficar sempre no começo do arquivo `models.py` (não em arquivo separado).

**Exceção — códigos de erro da API:** códigos de erro são `models.TextChoices`, mas
ficam obrigatoriamente em `<app>/errors.py` (nunca em `models.py`), registrados em
`apps.api.core.errors.error_codes` via descoberta automática. Toda falha da API é
levantada com `APIError(code, status_code=...)`, nunca com uma string solta — ver
`docs/superpowers/specs/2026-07-28-api-errors-design.md`.

### 2.2. Ordem dos Argumentos dos Fields

Os argumentos de cada `Field` **devem** seguir uma ordem fixa, sempre declarada de
forma formatada (legível):

- **Ordem padrão:**
  `verbose_name (lazy_gettext)`, `validators`, `blank`, `null`, `default`, `help_text`.

- **`CharField`:**
  `verbose_name (lazy_gettext)`, `max_length`, `choices?`, `validators?`, `blank`, `null`, `default`, `help_text`.

- **`IntegerField`:**
  `verbose_name (lazy_gettext)`, `choices?`, `validators`, `blank`, `null`, `default`, `help_text`.

- **`ForeignKey`:**
  `verbose_name (lazy_gettext)`, `to`, `on_delete`, `related_name`, `limit_choices_to`, `blank`, `null`, `help_text`.

### 2.3. Classe Meta

A classe `Meta` de cada model **deve** declarar, no mínimo, os seguintes atributos:

- `db_table`
- `ordering = ("-id",)`
- `verbose_name`
- `verbose_name_plural`
- `permissions` — seguindo o padrão de código de permissões descrito abaixo.

#### 2.3.1. Padrão de Códigos de Permissão

- Codinames das permissões **sempre em inglês**.
- Utilizar os prefixos padrões do Django (`view`, `add`, `delete`, `change`) quando
  aplicável para permissões extras.
- Para ações que não se encaixam nos prefixos padrões do Django, usar o prefixo `can_`.
- Sufixo: nome do modelo **em minúsculas, sem separadores**.
  - Exemplos: `produto`, `pessoa`, `venda`.
- O nome derivado do modelo é interpolado a partir de `%(model_name)s` (como no
  `BaseModelViewSet`).
  - Exemplo concreto: as actions `ativar`/`inativar` do `BaseModelViewSet` usam a
    permissão `can_toggle_<model>` (ex.: `can_toggle_produto`).

### 2.4. Herança

- Todo model **deve** herdar da classe base do projeto (`Base`).
- A herança garante, entre outros:
  - Manager de "ativos" (`ativos`) além do `objects` (`CustomManager`, que aplica
    `defer` dos `queryset_deferred_fields`).
  - Campo `ativo` para soft-active (ligado às actions `ativar`/`inativar`).
  - `created_by` preenchido automaticamente com o usuário corrente no `save()`
    (via `CreationAuditMixin`); não existe `last_modified_by` — quem alterou por
    último vive no `history`, não num FK.
  - Histórico de auditoria (`history`, via `django-auditlog`).
  - Timestamps `created_at`/`last_modified_at`.
  - Utilitários: `clonar()`, `as_dict()`, `get_fields()` e afins.
  - `api_scope_resource = None` por padrão — ver §2.7 para expor o model como
    recurso público de scopes/permissions.

### 2.5. Métodos Obrigatórios

- Todo model **deve** implementar o método `__str__`.

### 2.6. Campos Internos e Somente-Leitura

- Campos que não devem ser expostos pelos serializers são declarados em
  `extra_internal_fields` no model (somados aos `internal_fields` da `Base`).
- Campos expostos porém não editáveis são declarados em `extra_read_only_fields`
  (somados aos `read_only_fields` da `Base`). O `BaseModelSerializer` consome ambos
  automaticamente.

### 2.7. Scopes e Permissions Públicas (`resource:action`)

- A interface pública e estável de scopes de API key e permissions humanas é
  `resource:action` (ex.: `teams:read`, `invitations:accept`); codenames Django
  (`app_label.codename`) são um detalhe interno, nunca expostos a clientes.
- Um model expõe seu recurso com `api_scope_resource = "recurso"`; `None`
  (o default) significa que o model não é exposto.
- Um ViewSet pode sobrescrever com `scope_resource = "recurso"` quando a
  superfície pública diverge do model consultado (ex.: sem `queryset` estático).
  Regra: **default no model, override na view** — só sobrescreva quando
  necessário.
- Actions CRUD (`read`/`create`/`update`/`delete`) são derivadas automaticamente
  da action do ViewSet; actions customizadas declaram o scope com
  `@require_token_scopes("recurso:action")`.
- Wildcards: `resource:*` (qualquer action do recurso) e `*` (qualquer recurso).
  Delegar `*` a uma API key exige superuser ou a permission
  `autenticacao.grant_unrestricted_apikey` — ver
  `apps.api.autenticacao.scope_delegation.validate_scope_delegation`.
- Fonte da verdade: `apps.api.core.scope_registry` (`ScopeRegistry`,
  `parse_scope`, `matches_scope`, `required_django_permissions`).

---

## 3. Serializers

### 3.1. Herança

- Todo serializer **deve** herdar da classe base apropriada do projeto.

### 3.2. Serializer Externo Padrão

- Cada recurso **deve** expor um serializer externo padrão, contendo apenas
  informações básicas e essenciais.
- Quando for necessário expor mais campos, o serializer adicional **deve herdar**
  desse serializer externo padrão, evitando duplicação.

### 3.3. Tipos de Serializers

- **Serializers de visualização (leitura):** devem herdar de `BaseModelSerpySerializer`
  (baseado em `serpy`, otimizado para leitura).
- **Serializers de validação / criação / alteração (escrita):** devem herdar de
  `BaseModelSerializer`.

---

## 4. Views

### 4.1. Herança

- Toda view de modelo **deve** herdar da classe `BaseModelViewSet` do projeto.

### 4.2. Serializer por Ação

- Quando uma única classe atende todas as ações, defina `serializer_class`.
- Quando ações diferentes exigem serializers diferentes, defina
  `serializer_classes = {"<action>": SerializerClass, ...}`.
- **Não** declarar `serializer_class` e `serializer_classes` ao mesmo tempo
  (`serializer_classes` é ignorado com aviso nesse caso).

### 4.3. Filtros

- Toda view **deve** definir explicitamente um `filterset_class`.

### 4.4. Actions Herdadas do `BaseModelViewSet`

Todo ViewSet herda, de graça:

| Action | Método | Descrição |
|---|---|---|
| `grid` | GET | Listagem paginada serializada. |
| `form` | GET (detail) | Instância única serializada. |
| `values` | GET | Projeção via `?values=campo1,campo2`. |
| `bulk_create` | POST | Criação em lote. |
| `bulk_update` | PATCH | Atualização parcial em lote (atômica). |
| `clonar` | GET (detail) | Clona o registro (`Base.clonar`). |
| `invalidate_cache` | POST | Vira a versão do namespace de cache (ver §5). |
| `ativar` / `inativar` | GET (detail) | Soft-active (quando `has_ativo_field`). |

---

## 5. Cache

- O cache de resposta com TTL usa **namespace versionado por modelo**
  (`viewcache:<app>.<model>`).
- Montar chaves com `build_cache_key(...)`; o TTL padrão vem de `cache_timeout` no
  ViewSet.
- Invalidação em massa é **O(1)**: a action `invalidate_cache` (ou
  `bump_cache_version()`) incrementa a versão do namespace, tornando todas as chaves
  antigas inalcançáveis — sem depender de `delete_pattern`.
- **cachalot** é **opt-in**: usado deliberadamente onde compensa, não globalmente.
  A espinha dorsal do cache é HTTP/TTL + primitivos explícitos.

---

## 6. Lookup e tratamento de N+1

- FKs que precisam de autocomplete sem exigir permissão do modelo-alvo são expostas
  pelo endpoint global `GET /lookup/<chave>/`, via decorator `@lookup`.
- O registry é a fronteira de segurança: **modelo não registrado responde 404**.
- N+1 do lookup deve ser tratado de uma das duas formas:
  - `select_related`/`prefetch_related` no próprio `@lookup` (relação fixa e simples); ou
  - `setup_eager_loading(queryset)` **co-localizado no serializer** (recomendado quando
    a otimização depende dos campos do próprio serializer) — a view o aplica
    automaticamente.

---

## 7. Documentação e estilo de código

- **Docstrings** seguem a convenção **Google** (`ruff pydocstyle: google`), aplicada
  via lint.
- Prefira **variáveis intermediárias nomeadas** a chamadas aninhadas:
  use `r = f(); g(r)` em vez de `g(f())`. Melhora legibilidade e depuração.

---

## 8. Configuração e ambiente

- Leitura de ambiente **sempre** pelos helpers do projeto: `get_env_var` (com default
  opcional) e `get_list_from_env` — nunca `os.environ` cru.
- Em settings/configuração, prefira **tuplas** para coleções imutáveis (ex.:
  `ordering = ("-id",)`). Listas de campos mutáveis nos models (ex.:
  `extra_read_only_fields`) permanecem listas.

---

## 9. Testes

- Framework: **pytest** (`pytest-django`); testes ficam no pacote `tests/` de cada app.
- Dados de teste via **`factory_boy`** — um factory por app (ex.: `UsuarioFactory`).
- Tasks Celery rodam **eager** em teste (execução síncrona, sem broker).
- Prefira testes **DB-less** quando o comportamento não depende do banco (ex.: lookup
  registry, helpers de env).

---

## Resumo das Regras

| Categoria | Regra |
|---|---|
| Estrutura | Todos os apps sempre dentro da pasta `apps/` |
| Estrutura | Módulos como **arquivos** `.py`; `tests/` como **pacote** |
| QuerySets | `select_related`/`prefetch_related` obrigatórios; `only()`/`values()` para limitar campos |
| Models | Choices no topo do `models.py`, `IntegerChoices`, docstring referenciando modelo/coluna |
| Models | Ordem fixa de argumentos por tipo de Field |
| Models | `Meta` com `db_table`, `ordering`, `verbose_name`, `verbose_name_plural`, `permissions` |
| Models | Permissões em inglês, prefixos Django ou `can_`, sufixo = modelo em minúsculas |
| Models | Herança obrigatória de `Base` e implementação de `__str__` |
| Serializers | Serializer externo padrão enxuto, com herança para extensões |
| Serializers | Leitura → `BaseModelSerpySerializer`; escrita → `BaseModelSerializer` |
| Views | Herança de `BaseModelViewSet`; `serializer_class` **ou** `serializer_classes` |
| Views | Definição obrigatória de `filterset_class` |
| Cache | Namespace versionado por modelo; invalidação O(1); cachalot opt-in |
| Lookup | `@lookup` + `setup_eager_loading` co-localizado; não registrado → 404 |
| Estilo | Docstrings Google; `r = f(); g(r)` em vez de `g(f())` |
| Config | Env via `get_env_var`/`get_list_from_env`; tuplas para coleções imutáveis |
| Testes | pytest + `factory_boy`; Celery eager; DB-less quando possível |