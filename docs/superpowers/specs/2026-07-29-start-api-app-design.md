# `start_api_app` — Design

## Objetivo

Criar um management command que gere apps já no formato exigido por
`.ai/CONVENTIONS.md`, eliminando o `startapp` cru do Django como ponto de partida.
O `startapp` padrão produz um app que viola a convenção em três pontos: nasce fora
de `apps/` (§1.1), traz `tests.py` em vez do pacote `tests/` (§1.2) e não cria os
módulos previstos (`serializers`, `filters`, `handlers`, `docs`, `urls`). O
diretório `testets/` na raiz do repositório é o resultado concreto desse problema.

O comando também resolve o passo manual de registro: um app só responde nas rotas
depois de entrar em `BUSINESS_APPS`, porque `api/urls.py` monta `apps_urls` varrendo
essa lista.

## Interface

```text
python manage.py start_api_app <nome> [--parent <app_pai>]
```

```text
start_api_app vendas                      -> apps/vendas/
start_api_app pedidos --parent vendas     -> apps/vendas/subapps/pedidos/
start_api_app itens --parent pedidos      -> apps/vendas/subapps/pedidos/subapps/itens/
```

Sem `--parent`, o destino é sempre `apps/<nome>`, derivado de `settings.BASE_DIR`.
O comando nunca cria app na raiz do projeto.

## Decisões

### Abordagem: subclasse do `startapp`

O comando herda de `django.core.management.commands.startapp.Command`, que é um
`TemplateCommand`, e aponta para um diretório de template próprio. Isso reaproveita,
sem reimplementação:

- validação do nome do app (identificador válido, não é keyword, não colide com
  módulo já importável);
- erro quando o diretório de destino já existe;
- renderização de `{{ camel_case_app_name }}` no `apps.py`.

O `TemplateCommand` insere `**options` no contexto do template. O comando calcula
`app_dotted_path` e o adiciona às options antes de delegar para `super().handle()`,
de modo que o template resolve o dotted path completo sem pós-processamento de
arquivo gerado.

A alternativa descartada foi escrever o comando do zero com `pathlib`: exigiria
reimplementar a validação de nome e a checagem de colisão, que é justamente a
proteção contra o erro que originou o `testets/`.

### Estrutura gerada

```text
apps/vendas/
├── __init__.py
├── admin.py                 vazio
├── apps.py                  AppConfig
├── docs.py                  vazio
├── filters.py               vazio
├── handlers.py              vazio
├── models.py                vazio
├── serializers.py           vazio
├── urls.py                  boilerplate do router
├── views.py                 vazio
├── migrations/__init__.py
├── tests/__init__.py
└── subapps/                 diretório vazio
```

Os módulos criados são os da convenção §1.2 menos `dashboards.py`, que fica de fora
por ser o menos frequente. `tests` é pacote, não arquivo — a exceção explícita da
§1.2.

Os arquivos nascem **vazios**, com duas exceções. Nenhum import especulativo das
classes base do projeto é gerado: um `from apps.api.base.models import Base` não
utilizado seria erro de lint no minuto seguinte à criação do app.

Primeira exceção, `urls.py`:

```python
from django.urls import include, path

from rest_framework.routers import DefaultRouter

router = DefaultRouter()

urlpatterns = [
    path("", include(router.urls)),
]
```

Segunda exceção, `apps.py`:

```python
from django.apps import AppConfig


class VendasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.vendas"
```

`verbose_name` não é gerado. Apenas `organizacoes` o declara hoje, e um valor
placeholder vazio seria pior que a ausência.

### `subapps/`

Cada app gerado recebe um `subapps/` vazio, destinado a apps do domínio daquele app.
É um diretório puro: **não** recebe `__init__.py`, nem na criação nem depois de
povoado.

Isso é seguro porque `apps/` e `apps/api/` já são namespace packages neste projeto —
nenhum dos dois tem `__init__.py`, e `apps.api.core` funciona normalmente em
`BUSINESS_APPS`. Um `subapps/` sem `__init__.py` é exatamente o mesmo caso: um
diretório intermediário com um único caminho no filesystem, que o Django resolve
sem reclamar.

Consequência a registrar: o git não versiona diretório vazio, então um `subapps/`
recém-criado não aparece em `git status`. Ele passa a ser versionado quando o
primeiro subapp entrar. Nenhum `.gitkeep` é gerado.

### Resolução do `--parent`

`--parent` identifica o app pai **pelo nome, em qualquer profundidade**. O comando
varre `apps/` recursivamente buscando um diretório com aquele nome que contenha
`apps.py` — a marca de app neste projeto, presente em todos eles. A varredura ignora
`__pycache__`, `migrations/` e `tests/`.

Encontrado o pai, o destino é `<pai>/subapps/<nome>`. O `subapps/` é criado se não
existir, em qualquer nível.

A profundidade é ilimitada e sem caso especial: o dotted path é o caminho relativo a
`settings.BASE_DIR` com `/` substituído por `.`. Assim
`apps/vendas/subapps/pedidos/subapps/itens` produz
`apps.vendas.subapps.pedidos.subapps.itens`.

Dois apps com o mesmo nome em profundidades diferentes é o caso que quebra a busca
por nome. A regra de desambiguação: se o valor de `--parent` contiver `.` ou `/`, é
tratado como caminho explícito do pai; caso contrário, é buscado por nome.

O caminho explícito é interpretado **relativo a `apps/`**, e aceita tanto `.` quanto
`/` como separador — `vendas.subapps.pedidos` e `vendas/subapps/pedidos` apontam para
o mesmo lugar. Um prefixo `apps.` ou `apps/` é aceito e descartado, de forma que
`apps.vendas.subapps.pedidos` (o dotted path como aparece em `BUSINESS_APPS`) também
funcione.

Nome ambíguo falha listando os candidatos:

```text
CommandError: mais de um app chamado 'pedidos':
  apps/vendas/subapps/pedidos
  apps/estoque/subapps/pedidos
use o caminho completo: --parent vendas.subapps.pedidos
```

### Registro em `BUSINESS_APPS`

O comando edita o `settings.py` do projeto, inserindo o dotted path do app novo na
posição alfabética dentro do bloco `BUSINESS_APPS`. As demais linhas da lista são
preservadas byte a byte — nenhuma reordenação global. Depois disso o app está
completo: `api/urls.py` inclui `<app>.urls` automaticamente para todo item de
`BUSINESS_APPS` que tenha um `urls.py`.

O arquivo de settings é localizado via `DJANGO_SETTINGS_MODULE`, não por caminho
fixo `api/settings.py`.

A edição é idempotente: app já presente na lista não é duplicado, e o comando avisa
que o registro já existia.

A lógica de edição fica numa função pura, `insert_business_app(source, dotted_path)`,
que recebe e devolve texto. Isola o único trecho arriscado do comando atrás de uma
interface trivial e permite testá-lo sem I/O.

### Erros

| Situação | Comportamento |
| --- | --- |
| Nome inválido, keyword, ou colidindo com módulo importável | `CommandError` (herdado do `startapp`) |
| Diretório de destino já existe | `CommandError` (herdado do `startapp`) |
| `--parent` não encontrado em `apps/` | `CommandError` |
| `--parent` ambíguo (2+ apps com o nome) | `CommandError` listando os candidatos e sugerindo o caminho completo |
| `--parent` como caminho explícito inexistente ou sem `apps.py` | `CommandError` |
| Bloco `BUSINESS_APPS` não localizado no settings | `CommandError` **antes** de criar qualquer arquivo, para não deixar app órfão |

A validação do `--parent` e a localização do bloco `BUSINESS_APPS` acontecem antes
da escrita de arquivos. O comando falha sem deixar app pela metade.

## Localização dos arquivos

| Arquivo | Papel |
| --- | --- |
| `apps/api/core/management/commands/start_api_app.py` | O comando, ao lado do `rotate_sensitive_fields.py` |
| `apps/api/core/management/commands/app_template/` | Diretório de template do `TemplateCommand` |
| `apps/api/core/tests/test_start_api_app.py` | Testes |

Os arquivos do template usam o sufixo `.py-tpl` do Django, o que mantém os stubs
fora do alcance do ruff e da coleta do pytest. O diretório não é confundido com um
command: `find_commands` do Django descarta pacotes, e `app_template/` não é nem um
módulo importável.

O `subapps/` vazio não pode ser versionado dentro do template — git não guarda
diretório vazio. Ele é criado em código, com `mkdir`, após a renderização do
template.

## Testes

Todos DB-less, conforme §9 — o comando é filesystem e manipulação de texto, sem
banco. Ficam em `apps/api/core/tests/test_start_api_app.py`.

Sobre `insert_business_app`, com strings puras:

1. Inserção na posição alfabética correta, preservando as demais linhas.
2. Idempotência: app já presente não é duplicado.
3. Bloco `BUSINESS_APPS` ausente é sinalizado.

Sobre o comando, com `settings.BASE_DIR` apontado para `tmp_path`:

1. App na raiz de `apps/`: árvore de arquivos exata, `urls.py` com o boilerplate do
   router, `tests/` é pacote, `tests.py` não existe, `subapps/` existe e está vazio,
   `apps.py` com o dotted path e o nome de classe corretos.
2. `--parent` num app de primeiro nível: destino em `<pai>/subapps/`, `subapps/`
   criado quando ausente, sem `__init__.py`.
3. `--parent` num app de segundo nível (`apps/vendas/subapps/pedidos`): terceiro
   nível criado, dotted path completo no `apps.py` e em `BUSINESS_APPS`.
4. `--parent` com dotted path explícito resolve direto, sem busca.
5. `--parent` inexistente, `--parent` ambíguo e destino já existente: `CommandError`,
   com os candidatos listados no caso ambíguo.

Para que esses testes não escrevam no `api/settings.py` real, a localização do
arquivo de settings fica numa função de módulo, `settings_module_path()`, que os
testes substituem por uma cópia temporária via `monkeypatch`. É a única costura de
I/O que o teste precisa interceptar — a busca de app pai e a criação de arquivos já
seguem `settings.BASE_DIR`.

## Fora de escopo

- `dashboards.py` no scaffold.
- Geração de classes de exemplo nos módulos.
- Execução de `makemigrations` após a criação.
- Migração ou remoção do `testets/` existente na raiz.
