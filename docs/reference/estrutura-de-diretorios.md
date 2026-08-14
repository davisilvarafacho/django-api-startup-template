# Estrutura de diretórios

Mapa de onde cada tipo de código mora neste repositório e por quê. As regras
obrigatórias derivadas daqui — o que **deve** ser feito ao criar um app — estão
em [Convenções](convencoes.md) §1; esta página descreve a árvore, aquela impõe a
forma.

## Mapa da raiz

A raiz não é uma lista plana de pastas: são oito camadas, cada uma com um
critério de admissão diferente.

| Camada | Onde | O que entra |
|---|---|---|
| Configuração do projeto | `api/`, `manage.py`, `gunicorn.conf.py`, `conftest.py` | Django puro: `settings`, `urls`, `wsgi`/`asgi`, `celery`, `logging_config`, `telemetry`, `configure_enviroment`. |
| Domínio | `apps/` | Todo app instalado, sem exceção. |
| Frameworks internos | `internal_frameworks/` | Infraestrutura reutilizável escrita no projeto, sem model de domínio. |
| Utilitários | `utils/` | Helpers genéricos, sem dependência de Django. |
| Testes transversais | `tests/` | O que não pertence a um app só. |
| Infraestrutura e operação | `docker/`, `observability/`, `.devcontainer/`, `.ci/`, `.github/`, `scripts/`, `logs/` | Proxy, stack de observabilidade, CI, utilitários de linha de comando. |
| Documentação | `docs/`, `.ai/`, `.claude/`, `*.md` da raiz | Documentação do projeto, instruções de agente e roteadores. |
| Gerado e ignorado | `site/`, `coverage.xml`, `__pycache__/`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`, `.examples/` | Nada aqui é editado à mão. |

### `api/` — configuração, não código de domínio

É o pacote de configuração do Django, e o único fora de `apps/` que carrega
nomes canônicos do framework. Nunca receba regra de negócio aqui.

Um detalhe importante: `api/configure_enviroment.py` é a **fonte única** de apps,
middlewares e storages por ambiente. Um app novo que só existe em
desenvolvimento é declarado lá, nunca com um `if` dentro de `settings.py`.

### `internal_frameworks/` — implementações próprias

Mecanismos de infraestrutura que o projeto escreve em vez de consumir prontos de
uma biblioteca. Hoje: `guardrails/` (limites de execução verificáveis),
`permission_cache/` (cache semântico de autorização, ver ADR 0005) e
`sensitive_fields/` (cifragem de campos em repouso).

Cada implementação é um pacote próprio, nomeado pelo que implementa, com sua
própria suíte de testes. O que **não** entra:

- regra de negócio — pertence a um app em `apps/`;
- helper avulso e genérico — pertence a `utils/`.

O critério prático: se o código precisa conhecer um model do domínio, ele não é
um framework interno.

### `utils/` — helpers genéricos

Funções pequenas e sem estado, que não conhecem nem o domínio nem, na maior
parte, o Django: leitura de ambiente (`env`), formatação de log (`logs`),
reprodução de request como cURL (`curl`), `signals`, `singleton`.

A leitura de variáveis de ambiente é sempre por `utils/env.py`
(`get_env_var`, `get_bool_from_env`, `get_list_from_env`) — nunca `os.environ`
cru.

### `tests/` na raiz

Só o que não cabe dentro de um app:

- `tests/architecture/` — invariantes que atravessam mais de um app, verificados
  como teste (marcador `architecture`);
- `tests/support/` — construtores de dados compartilhados pela suíte
  (ex.: `criar_usuario`).

Código de produção nunca importa `tests`. Fixture específica de um app fica no
`conftest.py` mais próximo de quem a consome, não aqui.

## `apps/`

### Dois níveis semânticos

`apps/` mistura, deliberadamente, dois tipos de app:

- **Infraestrutura da API** (`apps/api/*`) — `base` (models, serializers e views
  base), `core` (registries, cache, erros, health, management commands),
  `autenticacao` e `metadata`. É o que o template entrega pronto.
- **Identidade, tenancy e domínio** (`apps/organizacoes/`, `apps/usuarios/`,
  `apps/logs/`) — os apps que herdam de `BaseGlobal` por serem lidos antes de
  existir contexto RLS, mais o app de trilha de auditoria.

Apps de negócio novos nascem no primeiro nível de `apps/`, ao lado de
`organizacoes/` e `usuarios/`.

### `apps/api/` é um agrupador, não um app

`apps/api/` **não é um app instalado**: não tem `apps.py`, não aparece em
`BUSINESS_APPS` e não tem `models.py`. É um agrupador — um diretório cuja única
função é reunir os apps de infraestrutura da API sob um prefixo comum. Quem está
em `BUSINESS_APPS` são os filhos, pelo dotted path completo:

```python
BUSINESS_APPS = [
    "apps.api.autenticacao",
    "apps.api.base",
    "apps.api.core",
    "apps.logs",
    "apps.organizacoes",
    "apps.api.metadata",
    "apps.usuarios",
]
```

Consequência técnica: `apps/` e `apps/api/` **não têm `__init__.py`**. São
_namespace packages_ implícitos (PEP 420), e é isso que os torna importáveis sem
serem, eles próprios, pacotes de código. Não adicione `__init__.py` a nenhum dos
dois.

Um agrupador não é a mesma coisa que um app pai, e a distinção define onde o
filho mora:

| | Agrupador | App pai |
|---|---|---|
| Exemplo | `apps/api/` | `apps/vendas/` |
| Tem `apps.py`? | não | sim |
| Está em `BUSINESS_APPS`? | não | sim |
| Onde ficam os filhos | direto dentro dele | dentro de `subapps/` |

Crie um agrupador apenas quando vários apps compartilharem um prefixo de
responsabilidade sem que exista uma entidade real acima deles. Na dúvida, use o
primeiro nível de `apps/`.

### Anatomia de um app

Cada módulo é **um arquivo Python**. `tests/` é a única exceção: sempre um
pacote.

```txt
apps/vendas/
├── __init__.py
├── admin.py
├── apps.py
├── docs.py
├── filters.py
├── handlers.py
├── models.py
├── serializers.py
├── urls.py
├── views.py
├── migrations/
│   └── __init__.py
├── tests/
│   └── __init__.py
└── subapps/          (vazio, para apps do mesmo domínio)
```

Essa lista é o que `manage.py start_api_app` gera, e a fonte da verdade dela é
executável: o diretório `app_template/` em
`apps/api/core/management/commands/`. Ao mudar a forma de um app, mude o
template — não só esta página.

Arquivos com nome fixo que aparecem conforme a necessidade, não no template:

| Arquivo | Quando |
|---|---|
| `errors.py` | O app define códigos de erro da API (`TextChoices`, descobertos automaticamente). Nunca em `models.py`. |
| `tenant_free_routes.py` | O app tem rota autenticada que dispensa organização. |
| `constants.py`, `rules.py`, `permissions.py`, `context.py`, `tasks.py` | Conforme o app precisa. |
| `management/commands/` | O app expõe comandos de linha de comando. |

Nomes de arquivo são sempre em **inglês e no plural** (`validators.py`, não
`validator.py`); nomes impostos pelo Python ou pelo Django (`__init__.py`,
`admin.py`, `apps.py`) conservam a forma que a ferramenta reconhece. Classes,
campos e conceitos de domínio permanecem em português.

### A regra recursiva: `subapps/`

Um app que pertence ao domínio de outro app mora dentro do `subapps/` do pai —
em qualquer profundidade. O contrato é que **o caminho físico é igual ao dotted
path**:

```txt
apps/vendas/                              → apps.vendas
apps/vendas/subapps/pedidos/              → apps.vendas.subapps.pedidos
apps/vendas/subapps/pedidos/subapps/itens/ → apps.vendas.subapps.pedidos.subapps.itens
```

Não existe subapp solto na raiz do app pai: `apps/vendas/pedidos/` é inválido, o
lugar é `apps/vendas/subapps/pedidos/`. Cada app gerado nasce com um `subapps/`
vazio; como o git não versiona diretório vazio, ele é criado pelo comando, não
copiado do template.

A recursividade vale para `tests/` também. Quando a suíte de um app cresce, ela
se divide em subpacotes por tema, e não em arquivos cada vez maiores — como em
`apps/api/autenticacao/tests/`, que tem `login/`, `mfa/`, `tokens/`,
`api_keys/` e `passwords/`, cada um com seu `__init__.py`.

### Criação de apps

Sempre pelo comando do projeto, nunca pelo `startapp` do Django — o comando
gera a estrutura acima e registra o app em `BUSINESS_APPS`, em ordem alfabética:

```bash
python manage.py start_api_app vendas                    # apps/vendas/
python manage.py start_api_app pedidos --parent vendas   # apps/vendas/subapps/pedidos/
python manage.py start_api_app vendas apps/vendas        # usa um diretório já existente
```

## Onde colocar um código novo

```txt
Precisa conhecer um model do domínio?
├── sim → é regra de negócio?
│         ├── sim → apps/<app>/          (ou apps/<pai>/subapps/<app>/)
│         └── não → apps/api/core/       (registry, infraestrutura da API)
└── não → é um mecanismo com estado, configuração ou ciclo de vida próprio?
          ├── sim → internal_frameworks/<nome>/
          └── não → utils/<nome>.py
```

Configuração de ambiente nunca entra nessa árvore: vai para
`api/configure_enviroment.py`.

## Como um app estende o comportamento global

A estrutura de arquivos existe para servir a um padrão recorrente: o
comportamento é global por padrão, e cada app **declara suas exceções em um
arquivo de nome fixo**, coletado no boot. É por isso que os nomes de arquivo são
convenção rígida e não preferência de estilo.

| Declarado em | Coletado por | Efeito |
|---|---|---|
| `urls.py` → `PUBLIC_ROUTES` | `RouteRegistry` | Rota sem token. |
| `tenant_free_routes.py` → `TENANT_FREE_ROUTES` | `RouteRegistry` | Rota com token, sem organização. |
| `errors.py` | `apps.api.core.errors.error_codes` | Códigos de erro da API. |
| `@lookup(...)` no model | Registry de lookup | Expõe o model em `GET /lookup/<chave>/`. |
| `api_scope_resource` no model | `ScopeRegistry` | Expõe o recurso como `resource:action`. |

## Infraestrutura e operação

| Diretório | Conteúdo |
|---|---|
| `docker/nginx/` | `nginx.conf` (bloco http comum), `snippets/` (headers, proxy, websocket) e `sites/<ambiente>/`, montados sobre `/etc/nginx/conf.d`. |
| `observability/` | Prometheus, Loki, Tempo, Alloy e o provisionamento do Grafana (dashboards, datasources, alertas). |
| `.devcontainer/` | Ambiente de desenvolvimento reproduzível. |
| `.ci/` | `Jenkinsfile`, para quem roda CI fora do GitHub. |
| `.github/` | Workflows, templates de issue e PR, dependabot. |
| `scripts/` | Utilitários de linha de comando fora do Django (ex.: `check_version.py`). |
| `logs/` | Destino dos logs em disco em desenvolvimento; versionado vazio, com `.gitkeep`. |

## Documentação e instruções

Quatro lugares, separados por **gênero de texto**, não por quem lê:

| Onde | Gênero |
|---|---|
| `docs/` | Documentação do projeto, em Diátaxis (ADR 0001): `tutorial/`, `how-to/`, `reference/`, `explanation/`, `adr/`. Publicado pelo MkDocs. |
| `AGENTS.md`, `CLAUDE.md` | Roteador: carregado automaticamente por agentes, aponta para o resto. |
| `.ai/` | Instrução destinada exclusivamente a agentes, sem valor para quem desenvolve. |
| `.claude/skills/` | Instruções especializadas, carregadas sob demanda pelo agente conforme o assunto. |

Conteúdo que serve aos dois públicos — convenções, glossário, esta página — mora
em `docs/reference/` e é apontado a partir do roteador. Não existe uma segunda
cópia mantida em paralelo para agentes.

Dois diretórios em `docs/` estão fora da taxonomia Diátaxis, por serem registro
histórico de trabalho e não documentação do produto: `docs/superpowers/`
(specs, plans e handoffs, datados) e `docs/research/`.

## Gerado, ignorado e fora das ferramentas

| Caminho | Situação |
|---|---|
| `site/` | Build do MkDocs. Fora do git. |
| `.examples/` | Projeto de referência externo. Fora do git, do ruff e da coleta do pytest (`norecursedirs`). |
| `coverage.xml`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/` | Artefatos de execução. |
| `.venv/` | Ambiente virtual gerido pelo `uv`. |
| `*/migrations/*` | Versionado e obrigatório, mas fora do ruff. |
