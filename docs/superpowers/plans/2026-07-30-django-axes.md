# Proteção contra força bruta com django-axes — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ativar o `django-axes` para bloquear força bruta no `POST /auth/login/` e no login do `/admin/`, com resposta JSON adequada a uma API e sem criar vetor de negação de serviço por bloqueio de conta.

**Architecture:** O `AxesStandaloneBackend` entra como primeiro backend de autenticação e apenas verifica bloqueio, delegando a autenticação real à cadeia existente (`rules` → `ModelBackend` → `guardian`). O `AxesMiddleware` substitui a resposta na volta da request quando o bloqueio dispara. A chave de bloqueio é o par `username + ip_address` (E lógico), e o IP vem de um callable do próprio projeto, para que o registro do axes e o do `TokenMetaData` concordem.

**Tech Stack:** Django 5, Django REST Framework, django-axes 8.0.0, django-rest-knox, Celery + django-celery-beat, PostgreSQL, pytest + pytest-django + factory_boy, uv.

## Global Constraints

- Design de referência: `docs/superpowers/specs/2026-07-30-django-axes-design.md`. Toda decisão já foi tomada lá; este plano não reabre nenhuma.
- Trabalhar no worktree `.worktrees/django-axes`, branch `feat/django-axes`.
- Código, comentários e documentação em **português**; nomes de migração em **inglês**.
- Dependências e execução **sempre** via `uv`, nunca `pip`/`python` direto.
- Leitura de variável de ambiente **sempre** por `utils/env.py` (`get_env_var`, `get_bool_from_env`), nunca `os.environ` cru.
- Nenhuma dependência nova. O `django-axes==8.0.0` já está no `pyproject.toml:19` e no `uv.lock`; o `django-ipware` **não** deve ser instalado.
- Não alterar o `ENGINE` do banco (`django_rls.backends.postgresql`).
- Docstrings no padrão Google.
- Commits seguem Conventional Commits (validados por commitlint no hook `commit-msg`).
- A suíte exige Postgres e Redis rodando: `make up` antes de qualquer `make test`.
- Rodar um teste isolado exige o grupo `test` explícito: `uv run --group test pytest <caminho> -q`.

---

## Estado da execução (2026-07-31)

Executado com `superpowers:subagent-driven-development`. **Tasks 1 a 6 concluídas e
revisadas; Task 7 está em verificação final.** Suíte em 210 testes passando (baseline antes
da branch: 191). Árvore limpa, nada pendente de commit.

| Task | Commits | Suíte | Revisão |
|---|---|---|---|
| 1 — IP do cliente | `037d8c6..0bc87d2` | 196 | limpa, 1 Minor adiado |
| 2 — ativar o axes | `0bc87d2..3347c71` | 200 | limpa, 2 desvios do plano |
| 3 — resposta JSON | `3347c71..2e5924c` | 203 | limpa, 3 Minor adiados |
| 4 — política | `2e5924c..6df5a82` | 207 | limpa, 1 Minor adiado |
| 5 — expurgo | `6df5a82..72c6f6c` | 210 | 1 Important corrigido |

### O que falta

- **Task 6 — documentação.** Concluída: how-to, entrada no `nav` do `mkdocs.yml`,
  parágrafo no `CLAUDE.md` e registro dos desvios na spec.
- **Task 7 — verificação final.** Suíte, migrations, documentação e `check` executados;
  lint global ainda encontra dois `UP017` preexistentes e a verificação manual via
  stack permanece pendente.
- **Revisão final da branch inteira** e a triagem dos Minor adiados listados abaixo.
- **Atualizar a spec** (`docs/superpowers/specs/2026-07-30-django-axes-design.md`) com
  os dois desvios da Task 2, que ela ainda não reflete.

### Desvios do plano já aplicados no código

Ambos foram confirmados no código-fonte do django-axes e do DRF, e cada um tem teste
que falha sem a correção. **A spec ainda não os reflete.**

1. **`AXES_USERNAME_FORM_FIELD = "username"`** precisou entrar no `api/settings.py`. O
   default do axes é `get_user_model().USERNAME_FIELD` (`axes/conf.py:46-47`), que
   neste projeto é `"email"` — mas o `AuthTokenSerializer` do DRF sempre chama
   `authenticate()` com a chave literal `"username"`. Sem o override, todo
   `AccessAttempt` gravaria `username=None` e a chave E `[["username", "ip_address"]]`
   degeneraria para bloqueio só por IP. A docstring de `get_client_username` afirma que
   o default é `"username"` e **está errada**.
2. **`apps/api/autenticacao/views.py` usa `context={"request": request._request}`**, e
   não `request`. O `Request` do DRF só implementa `__getattr__` (proxy de leitura), então
   o `axes_locked_out` que o backend atribui ficaria preso no wrapper e nunca chegaria ao
   `HttpRequest` que o `AxesMiddleware` inspeciona. O axes contaria certo no banco e
   nunca devolveria 429 pela HTTP.

Além desses, a Task 5 teve um achado Important corrigido em `72c6f6c`: a migração de
agendamento dependia de `django_celery_beat.0001_initial`, o que a posicionava antes das
outras 18 migrações do app; passou a depender de `0019_alter_periodictasks_options`.

### Minor adiados, para a revisão final triar

- **Task 1** — `get_client_ip` devolve `""` se o primeiro elemento do `X-Forwarded-For`
  vier vazio (ex.: `", 1.2.3.4"`), em vez de cair no `REMOTE_ADDR`. Inalcançável atrás do
  nginx atual, que sobrescreve o header; vira alcançável com ALB/Cloudflare na borda, e
  daria ao axes uma chave de bloqueio degenerada compartilhada.
- **Task 3** — `get_cool_off()` é chamado sem `request` em `handlers.py`. Inócuo enquanto
  `AXES_COOLOFF_TIME` for um `timedelta` estático; divergiria se virasse callable.
- **Task 3** — o cálculo do prazo restante pega a linha mais recente por OR entre combos,
  enquanto o axes decide por combo. Divergência teórica, só apareceria se
  `AXES_LOCKOUT_PARAMETERS` ganhasse múltiplos combos.
- **Task 3** — `test_bloqueio_nao_revela_o_prazo_no_corpo` é quase tautológico, porque a
  mensagem é constante sem interpolação.
- **Tasks 2-4** — a fixture `ambiente_axes` e os helpers de POST em `/auth/login/` estão
  duplicados nos três arquivos de teste do axes. Cabe um `conftest.py` em
  `apps/api/autenticacao/tests/`.
- **Task 5** — `desagendar()` remove o `PeriodicTask` mas deixa o `CrontabSchedule` órfão
  no rollback.
- **Task 5** — `update_or_create` usa `task` como chave, que não é `unique` no schema do
  `django_celery_beat` (só `name` é).

### Ambiente: como rodar a suíte nesta máquina

A porta 5432 está ocupada por um container `postgres` avulso, alheio ao projeto (ao lado
da stack do n8n/dify/vault), então o `db` do `docker-compose.yml` **não sobe** e o
`make up` falha. O container `drf-base-api-db-1` ficou parado em estado `Created`; o
volume `postgres_data` está intacto e ele volta com `docker compose up -d db` assim que a
porta for liberada.

A suíte roda contra o postgres avulso, no qual foi criado o banco `base`:

```bash
docker exec postgres psql -U postgres \
    -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='test_base';" \
    -c "DROP DATABASE IF EXISTS test_base;"

DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 DATABASE_USER=postgres \
DATABASE_PASSWORD=postgres DATABASE_NAME=base \
    uv run --group test pytest -p no:cacheprovider -q --no-cov
```

O `DROP DATABASE` prévio não é opcional: o teardown do pytest não consegue derrubar o
`test_base` enquanto sobrarem conexões abertas, e aí a execução seguinte falha inteira
na criação do banco (191 erros, todos com a mesma causa).

### Pendência pré-existente, não causada por esta branch

`make lint` acusa 2 erros `UP017` em `apps/api/core/b2_storage.py` e
`apps/api/core/tests/test_b2_storage.py`. Confirmado via `git stash` por três
implementadores diferentes como anterior a esta branch.

---

### Task 1: Endurecer a resolução de IP do cliente

O `get_client_ip` atual pega o primeiro elemento do `X-Forwarded-For` incondicionalmente. Isso é correto atrás do nginx de borda — que sobrescreve o header com `$remote_addr` em `docker/nginx/snippets/proxy.conf:23` — e **spoofável fora dele**. Como esta função vai virar o `AXES_CLIENT_IP_CALLABLE`, um deploy exposto diretamente permitiria rotacionar o header, nunca acumular tentativa e passar pelo axes sem bloqueio.

**Files:**
- Modify: `apps/api/autenticacao/utils.py:62-70`
- Test: `apps/api/autenticacao/tests/test_client_ip.py` (criar)

**Interfaces:**
- Consumes: `settings.BEHIND_PROXY`, já definido em `api/settings.py:80`.
- Produces: `apps.api.autenticacao.utils.get_client_ip(request) -> str | None`. As Tasks 2, 3 e 4 dependem desta assinatura; a Task 2 a registra em `AXES_CLIENT_IP_CALLABLE`.

- [x] **Step 1: Escrever os testes que falham**

Criar `apps/api/autenticacao/tests/test_client_ip.py`:

```python
"""Testes da resolução de IP do cliente."""
from apps.api.autenticacao.utils import get_client_ip


class RequestFalsa:
    def __init__(self, **meta):
        self.META = meta


def test_usa_o_forwarded_for_quando_esta_atras_do_proxy(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa(
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="172.18.0.5",
    )

    assert get_client_ip(request) == "203.0.113.10"


def test_ignora_o_forwarded_for_quando_nao_esta_atras_do_proxy(settings):
    settings.BEHIND_PROXY = False
    request = RequestFalsa(
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="172.18.0.5",
    )

    assert get_client_ip(request) == "172.18.0.5"


def test_cai_no_remote_addr_quando_nao_ha_forwarded_for(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa(REMOTE_ADDR="172.18.0.5")

    assert get_client_ip(request) == "172.18.0.5"


def test_descarta_espacos_e_usa_o_primeiro_elemento_da_lista(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa(HTTP_X_FORWARDED_FOR=" 203.0.113.10 , 172.18.0.5 ")

    assert get_client_ip(request) == "203.0.113.10"


def test_devolve_none_quando_nao_ha_origem_identificavel(settings):
    settings.BEHIND_PROXY = True
    request = RequestFalsa()

    assert get_client_ip(request) is None
```

- [x] **Step 2: Rodar os testes e confirmar que falham**

```bash
uv run --group test pytest apps/api/autenticacao/tests/test_client_ip.py -q
```

Esperado: FAIL em `test_ignora_o_forwarded_for_quando_nao_esta_atras_do_proxy` com `AssertionError: assert '203.0.113.10' == '172.18.0.5'`. Os demais passam, porque a implementação atual já cobre esses casos.

- [x] **Step 3: Implementar**

Em `apps/api/autenticacao/utils.py`, adicionar `from django.conf import settings` ao bloco de imports do Django (junto de `from django.core.cache import cache` e `from django.http import HttpRequest`), e substituir a função inteira:

```python
def get_client_ip(request: HttpRequest) -> str | None:
    """Resolve o IP de origem da request.

    Atrás do proxy de borda o `X-Forwarded-For` é sobrescrito pelo nginx
    (`docker/nginx/snippets/proxy.conf`), então chega com valor único e não
    forjável. Fora do proxy o header é escolhido pelo cliente e precisa ser
    ignorado, sob pena de o bloqueio por IP do django-axes virar contornável.

    Args:
        request: Request HTTP em processamento.

    Returns:
        O IP de origem, ou `None` se não for possível determiná-lo.
    """
    if settings.BEHIND_PROXY:
        forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")
```

O retorno `None` é seguro para os dois consumidores: `TokenMetaData.ip_address` é `null=True, blank=True` (`apps/api/autenticacao/models.py:96-101`) e o `AccessAttempt.ip_address` do axes também aceita nulo.

- [x] **Step 4: Rodar os testes e confirmar que passam**

```bash
uv run --group test pytest apps/api/autenticacao/tests/test_client_ip.py -q
```

Esperado: 5 passed.

- [x] **Step 5: Rodar o lint**

```bash
make lint
```

Esperado: sem erros.

- [x] **Step 6: Commit**

```bash
git add apps/api/autenticacao/utils.py apps/api/autenticacao/tests/test_client_ip.py
git commit -m "fix: ignorar X-Forwarded-For quando a API não está atrás do proxy"
```

---

### Task 2: Ativar o django-axes

Aqui o axes sai da inércia. Junto vem a correção do bloqueador: o `AuthTokenSerializer` é instanciado sem `context`, então o `authenticate()` recebe `request=None` e o `AxesStandaloneBackend` levanta `AxesBackendRequestParameterRequired` — um `ValueError`, que o `authenticate()` do Django não captura. Sem a correção, **todo login vira 500** no instante em que o axes é ativado. Por isso as duas mudanças ficam na mesma task: o teste de login bem-sucedido só falha depois que o axes é ligado, e é ele que prova a correção.

A resposta de bloqueio nesta task ainda é a padrão do axes (texto puro, status 429). A Task 3 a substitui por JSON.

**Files:**
- Modify: `api/settings.py` (imports, `LIBS_APPS`, `MIDDLEWARE`, `AUTHENTICATION_BACKENDS`, bloco novo de settings)
- Modify: `utils/env.py` (tupla `ENVS` e `Literal` `EnviromentVar`)
- Modify: `apps/api/autenticacao/views.py:37`
- Test: `apps/api/autenticacao/tests/test_axes_login.py` (criar)

**Interfaces:**
- Consumes: `apps.api.autenticacao.utils.get_client_ip` (Task 1).
- Produces: settings `AXES_FAILURE_LIMIT` (5), `AXES_COOLOFF_TIME` (30 min), `AXES_LOCKOUT_PARAMETERS` (`[["username", "ip_address"]]`), `AXES_ENABLED` (falso em teste). As Tasks 3, 4 e 6 dependem desses valores.

- [x] **Step 1: Escrever os testes que falham**

Criar `apps/api/autenticacao/tests/test_axes_login.py`:

```python
"""Testes da ativação do django-axes no fluxo de login."""
from rest_framework import status
from rest_framework.test import APIClient

import pytest
from axes.models import AccessAttempt
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"
SENHA = "senha-de-teste"


@pytest.fixture(autouse=True)
def ambiente_axes(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.AXES_ENABLED = True
    settings.BEHIND_PROXY = True
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def tentar_login(email, senha, ip="203.0.113.10"):
    client = APIClient()
    return client.post(
        URL_LOGIN,
        {"username": email, "password": senha},
        format="json",
        HTTP_X_FORWARDED_FOR=ip,
    )


def test_login_valido_continua_funcionando_com_o_axes_ligado():
    usuario = UsuarioFactory()

    resposta = tentar_login(usuario.email, SENHA)

    assert resposta.status_code == status.HTTP_200_OK


def test_falhas_abaixo_do_limite_retornam_400(settings):
    usuario = UsuarioFactory()

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        resposta = tentar_login(usuario.email, "senha-errada")
        assert resposta.status_code == status.HTTP_400_BAD_REQUEST


def test_falha_no_limite_bloqueia_com_429(settings):
    usuario = UsuarioFactory()

    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        tentar_login(usuario.email, "senha-errada")

    resposta = tentar_login(usuario.email, "senha-errada")

    assert resposta.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_bloqueio_registra_a_tentativa_no_banco(settings):
    usuario = UsuarioFactory()

    for _ in range(settings.AXES_FAILURE_LIMIT):
        tentar_login(usuario.email, "senha-errada")

    tentativa = AccessAttempt.objects.get(username=usuario.email)

    assert tentativa.ip_address == "203.0.113.10"
    assert tentativa.failures_since_start == settings.AXES_FAILURE_LIMIT
```

O parâmetro `settings` é a fixture do pytest-django, a mesma já usada no `ambiente_axes` — ela também é o mecanismo que reverte as alterações ao fim de cada teste.

Nota sobre a contagem: com `AXES_FAILURE_LIMIT = 5`, as tentativas 1 a 4 retornam 400 e a **quinta** já retorna 429 — o handler marca `request.axes_locked_out` assim que `failures_since_start >= limite`, e o middleware troca aquela mesma resposta.

- [x] **Step 2: Rodar os testes e confirmar que falham**

```bash
make up
uv run --group test pytest apps/api/autenticacao/tests/test_axes_login.py -q
```

Esperado: FAIL. `test_falha_no_limite_bloqueia_com_429` falha com `assert 400 == 429` (o axes ainda não está instalado) e `settings.AXES_FAILURE_LIMIT` levanta `AttributeError`.

- [x] **Step 3: Registrar as variáveis de ambiente**

Em `utils/env.py`, adicionar as três chaves **nas duas listas** — a tupla `ENVS` e o `Literal` `EnviromentVar`. São listas duplicadas que precisam andar juntas. Inserir em ambas, após o bloco `# api`, um bloco novo:

```python
    # axes
    "AXES_ENABLED",
    "AXES_FAILURE_LIMIT",
    "AXES_COOLOFF_MINUTES",
```

- [x] **Step 4: Ligar o axes no settings**

Em `api/settings.py`:

1. No topo do módulo, adicionar `from datetime import timedelta` como primeiro import da stdlib (antes de `import os`).

2. Em `LIBS_APPS` (linha 109 em diante), adicionar `"axes",` mantendo a ordem alfabética — entre `"anymail"` e `"corsheaders"`.

3. Em `MIDDLEWARE`, inserir logo **após** `"apps.api.autenticacao.middleware.AuthenticationMiddleware"` (linha 158):

```python
    # Troca a resposta por 429 na volta da request quando o bloqueio dispara.
    # Precisa vir depois da autenticação, que é quem popula `axes_locked_out`.
    "axes.middleware.AxesMiddleware",
```

4. Em `AUTHENTICATION_BACKENDS` (linha 243), adicionar como **primeiro** item:

```python
AUTHENTICATION_BACKENDS = [
    # Só verifica bloqueio e devolve `None`, delegando a autenticação real aos
    # backends seguintes. Precisa ser o primeiro para interromper antes deles.
    "axes.backends.AxesStandaloneBackend",
    "rules.permissions.ObjectPermissionBackend",
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
]
```

5. Logo após o bloco `ANONYMOUS_USER_NAME = None` (linha 251), adicionar:

```python
# Proteção contra força bruta no login. A chave de bloqueio é o par
# usuário + IP (E lógico): bloquear só por usuário permitiria que qualquer um
# trancasse a conta alheia, e bloquear só por IP puniria clientes atrás de NAT.
# Ver docs/superpowers/specs/2026-07-30-django-axes-design.md.
AXES_ENABLED = get_bool_from_env("AXES_ENABLED", CONFIG_ENVIRONMENT != "test")
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_FAILURE_LIMIT = int(get_env_var("AXES_FAILURE_LIMIT", 5))
AXES_COOLOFF_TIME = timedelta(minutes=int(get_env_var("AXES_COOLOFF_MINUTES", 30)))
AXES_RESET_ON_SUCCESS = True
# Sem isso, um cliente que faz retry automático (app com credencial salva
# desatualizada) reinicia o cooloff a cada tentativa e nunca sai do bloqueio.
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_HANDLER = "axes.handlers.database.AxesDatabaseHandler"
AXES_CLIENT_IP_CALLABLE = "apps.api.autenticacao.utils.get_client_ip"
AXES_HTTP_RESPONSE_CODE = 429
# O admin do axes é a única via de desbloqueio manual antes do fim do cooloff.
AXES_ENABLE_ADMIN = True
# Mantido porque o `TokenMetaData` só registra login de API; sem o AccessLog o
# login bem-sucedido no /admin/ não deixaria trilha nenhuma.
AXES_DISABLE_ACCESS_LOG = False
AXES_ENABLE_ACCESS_FAILURE_LOG = False
```

- [x] **Step 5: Corrigir o bloqueador do LoginView**

Em `apps/api/autenticacao/views.py:37`, trocar:

```python
        serializer = AuthTokenSerializer(data=request.data)
```

por:

```python
        # O `context` é obrigatório: sem ele o `authenticate()` recebe
        # `request=None` e o backend do axes levanta
        # `AxesBackendRequestParameterRequired`, transformando o login em 500.
        serializer = AuthTokenSerializer(data=request.data, context={"request": request})
```

- [x] **Step 6: Aplicar as migrações do axes**

```bash
make migrate
```

Esperado: as migrações do app `axes` são aplicadas. Nenhuma migração nova precisa ser criada.

- [x] **Step 7: Rodar os testes e confirmar que passam**

```bash
uv run --group test pytest apps/api/autenticacao/tests/test_axes_login.py -q
```

Esperado: 4 passed.

- [x] **Step 8: Confirmar que não há migração pendente e que a suíte inteira passa**

```bash
uv run python manage.py makemigrations --check --dry-run
make test
make lint
```

Esperado: nenhuma migração pendente, suíte verde, lint limpo. Se algum teste de autenticação já existente quebrar, a causa provável é o axes ficar ligado onde não deveria — conferir que `AXES_ENABLED` resolve para `False` sob `pytest`.

- [x] **Step 9: Commit**

```bash
git add api/settings.py utils/env.py apps/api/autenticacao/views.py apps/api/autenticacao/tests/test_axes_login.py
git commit -m "feat: ativar django-axes contra força bruta no login"
```

---

### Task 3: Resposta de bloqueio em JSON

Sem `AXES_LOCKOUT_CALLABLE`, o axes só emite JSON quando a request carrega o header `X-Requested-With: XMLHttpRequest`; no resto dos casos devolve `HttpResponse` com texto puro (`axes/helpers.py:447-500`). Uma API JSON precisa do callable.

O corpo segue a forma usada em `apps/api/core/status_handlers.py`: `{"mensagem": "..."}`. A mensagem **não** informa o prazo em texto — a informação de tempo vai só no header `Retry-After`, para consumo do cliente.

**Files:**
- Create: `apps/api/autenticacao/handlers.py`
- Modify: `api/settings.py` (adicionar `AXES_LOCKOUT_CALLABLE` ao bloco criado na Task 2)
- Test: `apps/api/autenticacao/tests/test_axes_resposta.py` (criar)

**Interfaces:**
- Consumes: settings da Task 2 (`AXES_COOLOFF_TIME`, `AXES_FAILURE_LIMIT`, `AXES_LOCKOUT_PARAMETERS`).
- Produces: `apps.api.autenticacao.handlers.resposta_de_bloqueio(request, credentials) -> JsonResponse`. É a assinatura que o axes invoca em `get_lockout_response` (`axes/helpers.py:450-457`) — dois argumentos posicionais.

- [x] **Step 1: Escrever os testes que falham**

Criar `apps/api/autenticacao/tests/test_axes_resposta.py`:

```python
"""Testes da resposta de bloqueio devolvida ao cliente."""
from rest_framework import status
from rest_framework.test import APIClient

import pytest
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"


@pytest.fixture(autouse=True)
def ambiente_axes(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.AXES_ENABLED = True
    settings.BEHIND_PROXY = True
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def bloquear(email, ip="203.0.113.10"):
    """Erra a senha até o bloqueio disparar e devolve a última resposta."""
    from django.conf import settings

    client = APIClient()
    resposta = None
    for _ in range(settings.AXES_FAILURE_LIMIT):
        resposta = client.post(
            URL_LOGIN,
            {"username": email, "password": "senha-errada"},
            format="json",
            HTTP_X_FORWARDED_FOR=ip,
        )
    return resposta


def test_bloqueio_responde_json_no_formato_do_projeto():
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    assert resposta.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resposta["Content-Type"] == "application/json"
    assert resposta.json() == {"mensagem": "Muitas tentativas de login."}


def test_bloqueio_nao_revela_o_prazo_no_corpo():
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    assert "minuto" not in resposta.json()["mensagem"]


def test_bloqueio_informa_o_prazo_no_header_retry_after(settings):
    usuario = UsuarioFactory()

    resposta = bloquear(usuario.email)

    segundos = int(resposta["Retry-After"])
    assert 0 < segundos <= settings.AXES_COOLOFF_TIME.total_seconds()
```

- [x] **Step 2: Rodar os testes e confirmar que falham**

```bash
uv run --group test pytest apps/api/autenticacao/tests/test_axes_resposta.py -q
```

Esperado: FAIL. O `Content-Type` é `text/html` e não há header `Retry-After`, porque a resposta ainda é a padrão do axes.

- [x] **Step 3: Implementar o handler**

Criar `apps/api/autenticacao/handlers.py`:

```python
"""Handlers do app de autenticação."""
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from rest_framework import status

from axes.helpers import (
    get_client_ip_address,
    get_client_parameters,
    get_client_user_agent,
    get_client_username,
    get_cool_off,
)
from axes.models import AccessAttempt

MENSAGEM_DE_BLOQUEIO = "Muitas tentativas de login."


def segundos_ate_o_desbloqueio(request: HttpRequest, credentials: dict | None) -> int:
    """Calcula quanto falta para o bloqueio expirar.

    O `attempt_time` é sobrescrito a cada falha, mas não durante o bloqueio,
    porque `AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT` é `False`. Logo o
    relógio congela na falha que causou o bloqueio e o prazo é exato.

    O filtro é montado por `get_client_parameters` em vez de codificar a chave
    de bloqueio aqui, para não quebrar caso `AXES_LOCKOUT_PARAMETERS` mude.

    Args:
        request: Request HTTP que sofreu o bloqueio.
        credentials: Credenciais enviadas na tentativa, quando disponíveis.

    Returns:
        Segundos restantes, com piso de 1. Devolve o cooloff completo se não
        houver tentativa registrada, que é o valor conservador.
    """
    cooloff = get_cool_off()
    if cooloff is None:
        return 0

    filtros = get_client_parameters(
        get_client_username(request, credentials),
        get_client_ip_address(request),
        get_client_user_agent(request),
        request,
        credentials,
    )

    consulta = Q()
    for filtro in filtros:
        consulta |= Q(**filtro)

    tentativa = AccessAttempt.objects.filter(consulta).order_by("-attempt_time").first()
    if tentativa is None:
        return int(cooloff.total_seconds())

    restante = (tentativa.attempt_time + cooloff) - timezone.now()
    return max(1, int(restante.total_seconds()))


def resposta_de_bloqueio(request: HttpRequest, credentials: dict | None = None) -> JsonResponse:
    """Monta a resposta devolvida quando o django-axes bloqueia a tentativa.

    Registrada em `AXES_LOCKOUT_CALLABLE`. O prazo restante vai só no header
    `Retry-After`; a mensagem é genérica de propósito.

    Args:
        request: Request HTTP que sofreu o bloqueio.
        credentials: Credenciais enviadas na tentativa, quando disponíveis.

    Returns:
        Resposta JSON com status 429.
    """
    resposta = JsonResponse(
        {"mensagem": MENSAGEM_DE_BLOQUEIO},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )

    segundos = segundos_ate_o_desbloqueio(request, credentials)
    if segundos:
        resposta["Retry-After"] = str(segundos)

    return resposta
```

- [x] **Step 4: Registrar o callable no settings**

Em `api/settings.py`, no bloco do axes criado na Task 2, adicionar logo após a linha `AXES_CLIENT_IP_CALLABLE`:

```python
AXES_LOCKOUT_CALLABLE = "apps.api.autenticacao.handlers.resposta_de_bloqueio"
```

- [x] **Step 5: Rodar os testes e confirmar que passam**

```bash
uv run --group test pytest apps/api/autenticacao/tests/test_axes_resposta.py -q
```

Esperado: 3 passed.

- [x] **Step 6: Confirmar que a Task 2 não regrediu**

```bash
uv run --group test pytest apps/api/autenticacao/ -q
make lint
```

Esperado: tudo verde. O `test_falha_no_limite_bloqueia_com_429` continua passando, agora com corpo JSON.

- [x] **Step 7: Commit**

```bash
git add apps/api/autenticacao/handlers.py api/settings.py apps/api/autenticacao/tests/test_axes_resposta.py
git commit -m "feat: responder bloqueio de login em JSON com Retry-After"
```

---

### Task 4: Testes da política de bloqueio

A política foi configurada na Task 2, mas nada ainda prova que ela se comporta como projetado. Estes são os testes que impedem uma regressão silenciosa de settings — trocar a chave de bloqueio ou o flag de cooloff não quebra nenhum teste das tasks anteriores, mas muda o comportamento de segurança.

**Files:**
- Test: `apps/api/autenticacao/tests/test_axes_politica.py` (criar)

**Interfaces:**
- Consumes: settings da Task 2 e o handler da Task 3. Nenhum código novo de produção.

- [x] **Step 1: Escrever os testes**

Criar `apps/api/autenticacao/tests/test_axes_politica.py`:

```python
"""Testes da política de bloqueio configurada para o django-axes."""
from rest_framework import status
from rest_framework.test import APIClient

import pytest
from axes.models import AccessAttempt
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory

pytestmark = pytest.mark.django_db

URL_LOGIN = "/auth/login/"
SENHA = "senha-de-teste"
IP_A = "203.0.113.10"
IP_B = "198.51.100.20"


@pytest.fixture(autouse=True)
def ambiente_axes(settings):
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.AXES_ENABLED = True
    settings.BEHIND_PROXY = True
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


def tentar(email, senha, ip=IP_A):
    client = APIClient()
    return client.post(
        URL_LOGIN,
        {"username": email, "password": senha},
        format="json",
        HTTP_X_FORWARDED_FOR=ip,
    )


def esgotar_tentativas(email, ip=IP_A):
    from django.conf import settings

    for _ in range(settings.AXES_FAILURE_LIMIT):
        tentar(email, "senha-errada", ip=ip)


def test_bloqueio_nao_alcanca_o_mesmo_usuario_em_outro_ip():
    usuario = UsuarioFactory()
    esgotar_tentativas(usuario.email, ip=IP_A)

    resposta = tentar(usuario.email, SENHA, ip=IP_B)

    assert resposta.status_code == status.HTTP_200_OK


def test_bloqueio_nao_alcanca_outro_usuario_no_mesmo_ip():
    vitima = UsuarioFactory()
    outro = UsuarioFactory()
    esgotar_tentativas(vitima.email, ip=IP_A)

    resposta = tentar(outro.email, SENHA, ip=IP_A)

    assert resposta.status_code == status.HTTP_200_OK


def test_login_bem_sucedido_zera_o_contador(settings):
    usuario = UsuarioFactory()
    for _ in range(settings.AXES_FAILURE_LIMIT - 1):
        tentar(usuario.email, "senha-errada")

    tentar(usuario.email, SENHA)

    assert not AccessAttempt.objects.filter(username=usuario.email).exists()


def test_tentativa_durante_o_bloqueio_nao_estende_o_cooloff():
    usuario = UsuarioFactory()
    esgotar_tentativas(usuario.email)
    momento_do_bloqueio = AccessAttempt.objects.get(username=usuario.email).attempt_time

    tentar(usuario.email, "senha-errada")

    tentativa = AccessAttempt.objects.get(username=usuario.email)
    assert tentativa.attempt_time == momento_do_bloqueio
```

O último teste checa o `attempt_time` no banco em vez de comparar dois valores de `Retry-After`: o relógio congelado é a causa, e o header é só a consequência. Assim o teste não depende de tempo decorrido entre as duas requisições.

- [x] **Step 2: Rodar os testes**

```bash
uv run --group test pytest apps/api/autenticacao/tests/test_axes_politica.py -q
```

Esperado: 4 passed. Se `test_bloqueio_nao_alcanca_o_mesmo_usuario_em_outro_ip` falhar com 429, a chave de bloqueio não está no formato E — conferir que `AXES_LOCKOUT_PARAMETERS` é `[["username", "ip_address"]]`, com a lista aninhada, e não `["username", "ip_address"]`.

- [x] **Step 3: Commit**

```bash
git add apps/api/autenticacao/tests/test_axes_politica.py
git commit -m "test: cobrir a política de bloqueio do django-axes"
```

---

### Task 5: Expurgo periódico do AccessLog

O `AccessAttempt` se autolimpa pelo cooloff e o `AccessFailureLog` está desligado. O `AccessLog` é a única tabela que cresce sem limite — um registro por login bem-sucedido, crescendo com o volume de tráfego e não com o de ataque.

O agendamento vai numa migração de dados porque o projeto usa o `DatabaseScheduler` (`api/settings.py:495`): sem a migração, a tarefa não existiria em nenhum deploy.

**Files:**
- Modify: `apps/api/core/tasks.py`
- Create: `apps/api/core/migrations/0001_schedule_access_log_cleanup.py`
- Test: `apps/api/core/tests/test_limpeza_logs_acesso.py` (criar)

**Interfaces:**
- Consumes: o comando `axes_reset_logs` do django-axes, disponível a partir da Task 2.
- Produces: `apps.api.core.tasks.limpar_logs_de_acesso_antigos(dias: int = 90) -> int`.

- [x] **Step 1: Escrever os testes que falham**

Criar `apps/api/core/tests/test_limpeza_logs_acesso.py`:

```python
"""Testes do expurgo periódico dos logs de acesso do django-axes."""
from datetime import timedelta

from django.utils import timezone

import pytest
from axes.models import AccessLog
from django_celery_beat.models import PeriodicTask

from apps.api.core.tasks import limpar_logs_de_acesso_antigos

pytestmark = pytest.mark.django_db


def criar_log(dias_atras):
    log = AccessLog.objects.create(
        username="alguem@exemplo.com",
        ip_address="203.0.113.10",
        user_agent="pytest",
        http_accept="application/json",
        path_info="/auth/login/",
    )
    # `attempt_time` é `auto_now_add`, então precisa ser reescrito na marra.
    AccessLog.objects.filter(pk=log.pk).update(
        attempt_time=timezone.now() - timedelta(days=dias_atras)
    )
    return log


def test_remove_apenas_os_logs_mais_velhos_que_o_prazo():
    antigo = criar_log(dias_atras=120)
    recente = criar_log(dias_atras=10)

    limpar_logs_de_acesso_antigos(dias=90)

    assert not AccessLog.objects.filter(pk=antigo.pk).exists()
    assert AccessLog.objects.filter(pk=recente.pk).exists()


def test_devolve_a_quantidade_removida():
    criar_log(dias_atras=120)
    criar_log(dias_atras=200)

    assert limpar_logs_de_acesso_antigos(dias=90) == 2


def test_a_tarefa_periodica_fica_agendada():
    tarefa = PeriodicTask.objects.get(
        task="apps.api.core.tasks.limpar_logs_de_acesso_antigos"
    )

    assert tarefa.enabled
    assert tarefa.crontab.hour == "3"
```

- [x] **Step 2: Rodar os testes e confirmar que falham**

```bash
uv run --group test pytest apps/api/core/tests/test_limpeza_logs_acesso.py -q
```

Esperado: FAIL com `ImportError: cannot import name 'limpar_logs_de_acesso_antigos'`.

- [x] **Step 3: Implementar a task**

Em `apps/api/core/tasks.py`, adicionar após a task `ping`:

```python
@shared_task
def limpar_logs_de_acesso_antigos(dias: int = 90) -> int:
    """Remove registros de `AccessLog` mais velhos que o prazo informado.

    O `AccessAttempt` não entra: ele se autolimpa quando o cooloff expira. O
    `AccessLog` é a única tabela do django-axes que cresce sem limite, porque
    grava um registro por login bem-sucedido.

    Args:
        dias: Idade máxima, em dias, dos registros preservados.

    Returns:
        Quantidade de registros removidos.
    """
    return AxesProxyHandler.reset_logs(age_days=dias)
```

E adicionar o import no topo do módulo, após `from celery import shared_task`:

```python
from axes.handlers.proxy import AxesProxyHandler
```

Chamar o handler direto em vez de `call_command("axes_reset_logs", age=dias)` porque é exatamente o que o comando faz (`axes/management/commands/axes_reset_logs.py`), sem passar por parsing de argumentos, e devolve a contagem para o teste e para o log do Celery.

- [x] **Step 4: Criar a migração de agendamento**

Criar `apps/api/core/migrations/0001_schedule_access_log_cleanup.py`:

```python
from django.db import migrations

TAREFA = "apps.api.core.tasks.limpar_logs_de_acesso_antigos"


def agendar(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    agenda, _ = CrontabSchedule.objects.get_or_create(
        minute="0",
        hour="3",
        day_of_week="*",
        day_of_month="*",
        month_of_year="*",
    )
    PeriodicTask.objects.update_or_create(
        task=TAREFA,
        defaults={
            "name": "Expurgar logs de acesso antigos",
            "crontab": agenda,
            "enabled": True,
        },
    )


def desagendar(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(task=TAREFA).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(agendar, desagendar),
    ]
```

- [x] **Step 5: Aplicar e rodar os testes**

```bash
make migrate
uv run --group test pytest apps/api/core/tests/test_limpeza_logs_acesso.py -q
```

Esperado: 3 passed.

- [x] **Step 6: Confirmar que não há migração pendente**

```bash
uv run python manage.py makemigrations --check --dry-run
make lint
```

Esperado: nenhuma migração pendente, lint limpo.

- [x] **Step 7: Commit**

```bash
git add apps/api/core/tasks.py apps/api/core/migrations/0001_schedule_access_log_cleanup.py apps/api/core/tests/test_limpeza_logs_acesso.py
git commit -m "feat: expurgar periodicamente os logs de acesso do axes"
```

---

### Task 6: Documentação

**Files:**
- Create: `docs/how-to/protecao-forca-bruta.md`
- Modify: `mkdocs.yml` (seção `how-to` do `nav`, linhas 22-27)
- Modify: `CLAUDE.md` (seção "Autorização em camadas")

**Interfaces:**
- Consumes: todas as tasks anteriores. Nenhum código novo.

- [x] **Step 1: Escrever o how-to**

Criar `docs/how-to/protecao-forca-bruta.md` cobrindo, nesta ordem:

1. **O que é protegido**: `POST /auth/login/` e o login do `/admin/`. Recuperação de senha e MFA ainda não.
2. **A política**: 5 falhas na combinação usuário + IP disparam bloqueio de 30 minutos; um login correto no meio da janela zera o contador; tentar durante o bloqueio não estende o prazo.
3. **Por que a chave é usuário + IP**: bloquear só por usuário deixaria qualquer pessoa trancar a conta alheia; só por IP puniria clientes atrás de NAT. Apontar para o spec para as lacunas aceitas.
4. **A resposta**: `429` com `{"mensagem": "Muitas tentativas de login."}` e o prazo no header `Retry-After` — o cliente deve respeitar o header em vez de fazer retry imediato.
5. **Variáveis de ambiente**: `AXES_ENABLED`, `AXES_FAILURE_LIMIT`, `AXES_COOLOFF_MINUTES`, com os defaults.
6. **Como desbloquear alguém**: pelo admin do Django, apagando o `AccessAttempt` correspondente; ou por linha de comando, `uv run python manage.py axes_reset_username <email>`.
7. **Dependência do proxy**: o `AXES_CLIENT_IP_CALLABLE` confia no `X-Forwarded-For` **apenas** quando `DJANGO_BEHIND_PROXY` está ligado, porque quem garante o valor é o nginx de borda (`docker/nginx/snippets/proxy.conf`). Expor a API sem esse nginx e manter a variável ligada torna o bloqueio por IP contornável. Referenciar `docs/how-to/proxy-nginx.md`.
8. **Retenção**: o `AccessLog` é expurgado aos 90 dias por tarefa periódica; o `AccessAttempt` se autolimpa.

- [x] **Step 2: Registrar a página no nav**

Em `mkdocs.yml`, na seção `how-to` do `nav`, adicionar após a linha `- Proxy reverso com nginx: how-to/proxy-nginx.md`:

```yaml
      - Proteção contra força bruta: how-to/protecao-forca-bruta.md
```

- [x] **Step 3: Atualizar o CLAUDE.md**

Na seção "Autorização em camadas", adicionar um parágrafo curto explicando que o `AxesStandaloneBackend` é o primeiro item de `AUTHENTICATION_BACKENDS`, que ele apenas verifica bloqueio e delega, e que a resposta de bloqueio é o `resposta_de_bloqueio` em `apps/api/autenticacao/handlers.py`. Apontar para `docs/how-to/protecao-forca-bruta.md`.

- [x] **Step 4: Validar a documentação**

```bash
make docs
```

Esperado: build sem erro em modo `--strict`.

- [x] **Step 5: Commit**

```bash
git add docs/how-to/protecao-forca-bruta.md mkdocs.yml CLAUDE.md
git commit -m "docs: documentar a proteção contra força bruta no login"
```

---

### Task 7: Verificação final

- [x] **Step 1: Rodar tudo o que a CI roda**

```bash
make up
make lint
make test
make docs
uv run python manage.py makemigrations --check --dry-run
```

Esperado: tudo verde. Colar a saída real na conclusão — não afirmar sucesso sem ela.

- [x] **Step 2: Conferir a configuração de deploy**

```bash
make check
```

Esperado: sem novos avisos relativos ao axes.

- [ ] **Step 3: Verificação manual do fluxo bloqueado**

```bash
make stack
```

Errar a senha seis vezes contra `http://localhost:8000/auth/login/` e confirmar que a sexta resposta traz `429`, corpo `{"mensagem": "Muitas tentativas de login."}` e header `Retry-After`. Confirmar que o `AccessAttempt` registrado no admin tem o IP real do cliente, e não o IP do container do nginx — é o sintoma que denuncia a resolução de IP quebrada.
