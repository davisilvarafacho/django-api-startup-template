# Convenções e padrões de desenvolvimento

Documento formal de padrões e convenções adotadas no desenvolvimento de APIs Django.
Consolida decisões arquiteturais e diretrizes obrigatórias para manutenção da
consistência, legibilidade e qualidade do código.

Este documento impõe a forma. A descrição da árvore de diretórios — o que é cada
pasta da raiz e onde colocar um código novo — está em
[Estrutura de diretórios](estrutura-de-diretorios.md).

---

## 1. Arquitetura da API

### 1.1. Localização dos Apps

Todos os apps da aplicação **devem** ficar sempre dentro de uma pasta `apps/`, na
raiz do projeto. Não é permitido manter apps soltos fora dessa pasta.

Subapps **devem** ficar sempre dentro do diretório `subapps/` do app pai. Não é
permitido criar um subapp diretamente na raiz do app pai. Essa regra vale em
qualquer profundidade da hierarquia.

```txt
apps/
└── assinaturas/
    └── subapps/
        └── faturamento/
```

O dotted path acompanha a estrutura física, por exemplo
`apps.assinaturas.subapps.faturamento`.

#### 1.1.1. Agrupadores

Um **agrupador** é um diretório que reúne apps sob um prefixo comum sem ser, ele
próprio, um app: não tem `apps.py`, não tem `models.py` e não aparece em
`BUSINESS_APPS`. `apps/api/` é o agrupador do template, e é por isso que
`autenticacao`, `base`, `core` e `metadata` ficam diretamente dentro dele, e não
sob um `subapps/`.

A regra do `subapps/` vale para **app pai**, não para agrupador. A distinção é a
existência do `apps.py`: se o diretório é um app instalado, os filhos vão para
`subapps/`; se é só um prefixo, os filhos ficam dentro dele.

Um agrupador **não deve** ter `__init__.py` — é um namespace package implícito
(PEP 420). Vale para `apps/` e para `apps/api/`.

Crie um agrupador apenas quando vários apps compartilharem uma responsabilidade
comum sem que exista uma entidade real acima deles. Na dúvida, use o primeiro
nível de `apps/`.

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
        /tests
            __init__.py
            test_*.py
```

Nomes de arquivo **devem** ser sempre em inglês e no plural (ex.: `models.py`,
`serializers.py`, `views.py`, `handlers.py`, `filters.py`), inclusive os que não
fazem parte da lista fixa acima (ex.: `validators.py`, não `validator.py`). Nomes
canônicos impostos pelo Python, Django ou outra ferramenta, como `__init__.py`,
`admin.py` e `manage.py`, são exceções e devem conservar o nome reconhecido pelo
framework. Nomes de classes, campos e conceitos do domínio podem permanecer em
português; esta regra se aplica aos nomes dos arquivos.

### 1.3. Criação de Apps

Apps **devem** ser criados pelo comando do projeto, não pelo `startapp` do Django:

```bash
python manage.py start_api_app vendas                    # apps/vendas/
python manage.py start_api_app pedidos --parent vendas   # apps/vendas/subapps/pedidos/
python manage.py start_api_app vendas apps/vendas        # usa o diretório já criado
```

O comando cria a estrutura desta seção (incluindo `tests/` como pacote e um
`subapps/` para apps do mesmo domínio), e registra o app em `BUSINESS_APPS`. O
`--parent` aceita o nome de qualquer app já existente, em qualquer profundidade.

O segundo argumento posicional é o destino, como no `startapp` do Django: o
diretório precisa **já existir** e ficar dentro de `apps/`, e o dotted path
registrado vem dele (não do nome do app). Arquivo já existente no destino não é
sobrescrito — o comando falha. É incompatível com `--parent`, que decide a mesma
coisa.

### 1.4. Pasta `internal_frameworks/`

A pasta `internal_frameworks/`, na raiz do projeto, é o lugar das **implementações
próprias** — código que o projeto escreve por conta própria em vez de consumir
pronto de uma lib.

Exemplos do que pertence a `internal_frameworks/`:

- cache de permissões (`permission_cache/`);
- encrypt/decrypt de fields (`sensitive_fields/`);
- limites de execução verificáveis (`guardrails/`);
- e demais mecanismos de infraestrutura escritos internamente.

Regras:

- Não é lugar para código de domínio: regra de negócio pertence ao app em `apps/`.
  O critério prático: se o código precisa conhecer um model do domínio, ele não é
  um framework interno.
- Não é depósito de helpers avulsos: utilitários genéricos continuam em `utils/`.
- Cada implementação **deve** ficar em seu próprio pacote, nomeado pelo que
  implementa, com a própria suíte de testes.

### 1.5. QuerySets

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
`.ai/brainstorming/spec/2026-07-28-api-errors-design.md`.

### 2.2. Ordem dos Argumentos dos Fields

Os argumentos de cada `Field` **devem** seguir uma ordem fixa, sempre declarada de
forma formatada (legível):

- **Ordem padrão:**
  `verbose_name (lazy_gettext)`, `validators`, `blank`, `null`, `default`, `help_text`, `db_comment`.

- **`CharField`:**
  `verbose_name (lazy_gettext)`, `max_length`, `choices?`, `validators?`, `blank`, `null`, `default`, `help_text`, `db_comment`.

- **`IntegerField`:**
  `verbose_name (lazy_gettext)`, `choices?`, `validators`, `blank`, `null`, `default`, `help_text`, `db_comment`.

- **`ForeignKey`:**
  `verbose_name (lazy_gettext)`, `to`, `on_delete`, `related_name`, `limit_choices_to`, `blank`, `null`, `help_text`, `db_comment`.

#### 2.2.1. Inteiros pequenos positivos

Valores inteiros não negativos que, pela regra do domínio, não serão superiores
a 32.000 **devem** usar `models.PositiveSmallIntegerField`. Exemplos incluem
quantidades de seats, dias de trial ou carência, números de versão e contadores
pequenos.

Essa é uma convenção interna de escolha do tipo do field. Não criar constante,
função auxiliar ou validator apenas para representar ou impor o limite de
32.000. Valores monetários e contadores com crescimento potencialmente superior
devem usar o tipo inteiro adequado ao seu domínio.

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

Credenciais swappable podem usar o nome do recurso administrado no sufixo
(`*_apikey`) em vez do nome técnico do model (`*_authtoken`). Isso mantém
separadas as permissions humanas de administração de API keys das permissions
default sobre a tabela unificada de tokens.

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

**Exceções arquiteturais explícitas:**

- Modelos globais de identidade, catálogo, tenancy ou roteamento do control
  plane podem herdar de `BaseGlobal` quando precisam ser consultados antes de
  existir contexto RLS. A exceção deve ser justificada na docstring do model;
  ela mantém `created_by`, `created_at` e `last_modified_at`. Exemplos existentes
  são `Usuario`, `Organizacao`, `Vinculo` e `Convite`.
- Um modelo com isolamento especial que não caiba no FK obrigatório de `Base`
  pode combinar `BaseGlobal` com `RLSModel` e policies próprias. A necessidade,
  o caminho privilegiado e testes sob um papel sujeito a RLS devem ser
  documentados explicitamente.
- Modelos de credencial que implementam contratos externos (como o
  `AuthToken` swappable do Knox) podem herdar diretamente do mixin mínimo
  necessário. A exceção deve estar documentada no model e não remove os campos
  comuns de autoria/timestamps aplicáveis.

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
- Cada action customizada também deve declarar no model a tradução interna para
  uma permission Django, via
  `api_scope_custom_actions = {"action": "can_action_model"}`. O registry nunca
  considera delegável uma action sem codename correspondente.
- Wildcards: `resource:*` (qualquer action do recurso) e `*` (qualquer recurso).
  Delegar `*` a uma API key exige superuser ou a permission
  `autenticacao.grant_unrestricted_apikey` — ver
  `apps.api.autenticacao.scope_delegation.validate_scope_delegation`.
- Fonte da verdade: `apps.api.core.scope_registry` (`ScopeRegistry`,
  `parse_scope`, `matches_scope`, `required_django_permissions`).

### 2.8. `help_text` e `db_comment`

Todo field **deve** declarar sempre `help_text` **e** `db_comment`, com o **mesmo
valor** nos dois argumentos. O `help_text` documenta o campo na API/admin; o
`db_comment` leva a mesma descrição para o comentário da coluna no banco, mantendo o
schema autoexplicativo para quem consulta o banco direto.

```python
quantidade = models.IntegerField(
    _("quantidade"),
    validators=[MinValueValidator(1)],
    blank=False,
    null=False,
    default=1,
    help_text=_("Quantidade de unidades do produto no item da venda."),
    db_comment=_("Quantidade de unidades do produto no item da venda."),
)
```

---

## 3. Serializers

### 3.1. Herança

- Todo serializer **deve** herdar da classe base apropriada do projeto.

Serializers de comandos, envelopes e credenciais que não representam um
`Base` persistente podem herdar de `serializers.Serializer`; devem declarar os
campos explicitamente e nunca expor digest, prefixo ou segredo de token.

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

**Exceção:** endpoints de infraestrutura/credenciais e ViewSets do control
plane pré-RLS podem usar as bases do DRF quando as actions genéricas herdadas
de `BaseModelViewSet` ampliariam indevidamente a superfície pública. Nesses
casos, queryset, permissions, scopes e métodos HTTP devem ser explícitos.

### 4.2. Recursos e endpoints de comando

- Use `ModelViewSet` ou `ViewSet` para recursos e coleções: quando as operações
  compartilham a mesma entidade, queryset e interface pública.
- Use `APIView` para endpoints de comando e fluxos transacionais sem um recurso
  central, como login, logout, redefinição de senha, reautenticação e desafios MFA.
  Cada endpoint deve expor explicitamente apenas os métodos HTTP, permissões,
  throttles, serializer e documentação que lhe cabem.
- Uma `@action` só deve ser usada quando o comando pertence claramente a um
  `ViewSet` de recurso existente, por exemplo, rotacionar uma API key ou encerrar
  uma sessão. Não crie um `ViewSet` de uma única action apenas para substituir uma
  `APIView`.
- Não agrupe fluxos distintos em um único `ViewSet` de autenticação apenas para
  padronização; preserve módulos coesos e com uma interface pequena.

### 4.3. Serializer por Ação

- Quando uma única classe atende todas as ações, defina `serializer_class`.
- Quando ações diferentes exigem serializers diferentes, defina
  `serializer_classes = {"<action>": SerializerClass, ...}`.
- **Não** declarar `serializer_class` e `serializer_classes` ao mesmo tempo
  (`serializer_classes` é ignorado com aviso nesse caso).

### 4.4. Filtros

- Toda view **deve** definir explicitamente um `filterset_class`.

A regra não se aplica a endpoints de comando sem filtros nem aos ViewSets
excepcionais acima quando não existe uma interface pública de filtragem.

### 4.5. Actions Herdadas do `BaseModelViewSet`

Todo ViewSet herda, de graça:

| Action | Método | Descrição |
|---|---|---|
| `grid` | GET | Listagem paginada serializada. |
| `form` | GET (detail) | Instância única serializada. |
| `logs` | GET (detail) | Histórico de auditoria do registro (paginado, sem filtros). |
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
- Construtores compartilhados de dados vivem em **`tests/support/`** e não fazem
  parte da interface de produção (ex.: `criar_usuario`).
- Tasks Celery rodam **eager** em teste (execução síncrona, sem broker).
- Prefira testes **DB-less** quando o comportamento não depende do banco (ex.: lookup
  registry, helpers de env).

---

## 10. Commits e Versionamento

- Commits seguem **Conventional Commits**, validados por commitizen e commitlint
  no hook `commit-msg`.
- O changelog segue **Keep a Changelog**.
- A versão do pacote **deve** ser igual à versão do schema OpenAPI —
  `make version-check` falha na CI se divergirem.

---

## Resumo das Regras

| Categoria | Regra |
|---|---|
| Estrutura | Todos os apps sempre dentro da pasta `apps/` |
| Estrutura | Subapps sempre dentro de `subapps/`, em qualquer profundidade |
| Estrutura | Agrupador (sem `apps.py`, fora de `BUSINESS_APPS`) reúne apps direto, sem `subapps/`, e não tem `__init__.py` |
| Estrutura | Módulos como **arquivos** `.py`; `tests/` como **pacote** |
| Estrutura | Nomes de arquivo em **inglês e no plural** (`models.py`, `validators.py`, etc.); nomes canônicos de ferramentas são exceção |
| Estrutura | `internal_frameworks/` = implementações próprias (cache de permissões, encrypt de fields, guardrails); domínio fica em `apps/`, helpers em `utils/` |
| QuerySets | `select_related`/`prefetch_related` obrigatórios; `only()`/`values()` para limitar campos |
| Models | Choices no topo do `models.py`, `IntegerChoices`, docstring referenciando modelo/coluna |
| Models | Ordem fixa de argumentos por tipo de Field |
| Models | Inteiros não negativos limitados pelo domínio a 32.000 → `PositiveSmallIntegerField`, sem abstração adicional |
| Models | Todo field com `help_text` **e** `db_comment` (mesmo valor) |
| Models | `Meta` com `db_table`, `ordering`, `verbose_name`, `verbose_name_plural`, `permissions` |
| Models | Permissões em inglês, prefixos Django ou `can_`, sufixo = modelo em minúsculas |
| Models | Herança obrigatória de `Base` e implementação de `__str__` |
| Serializers | Serializer externo padrão enxuto, com herança para extensões |
| Serializers | Leitura → `BaseModelSerpySerializer`; escrita → `BaseModelSerializer` |
| Views | Recursos usam `ModelViewSet`/`ViewSet`; comandos sem recurso central usam `APIView` |
| Views | Herança de `BaseModelViewSet`; `serializer_class` **ou** `serializer_classes` |
| Views | Definição obrigatória de `filterset_class` |
| Cache | Namespace versionado por modelo; invalidação O(1); cachalot opt-in |
| Lookup | `@lookup` + `setup_eager_loading` co-localizado; não registrado → 404 |
| Estilo | Docstrings Google; `r = f(); g(r)` em vez de `g(f())` |
| Config | Env via `get_env_var`/`get_list_from_env`; tuplas para coleções imutáveis |
| Testes | pytest + helpers em `tests/support/`; Celery eager; DB-less quando possível |
