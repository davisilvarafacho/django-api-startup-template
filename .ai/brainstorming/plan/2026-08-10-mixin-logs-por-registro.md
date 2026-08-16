# Mixin de Logs por Registro — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Substituir o endpoint global `GET /logs_alteracao/` por uma action `GET /<recurso>/<id>/logs/` herdada por todo `BaseModelViewSet`, que devolve a trilha de auditoria daquele registro.

**Architecture:** Um `LogsViewSetMixin` em `apps/api/base/views.py` entra na base do `BaseModelViewSet` (ativo por padrão, sem opt-in). A action resolve o objeto pelo `get_object()` — que já passa pelo RLS/tenancy — e consulta `LogAlteracao.objects.get_for_object(instance)`, paginando pelo `CustomPagination` do projeto. Sem filtros e sem ordenação configurável: o recorte é o próprio objeto da URL. O app `logs` perde view, urls, filterset e schema, ficando só com `models.py` e `serializers.py`.

**Tech Stack:** Django 5 + DRF, `django-auditlog` (model `apps.logs.models.LogAlteracao`), `serpy` para leitura, `drf-spectacular` para o schema, pytest.

## Global Constraints

- Código, comentários, docstrings e documentação em **português**; nomes de permissão Django em inglês.
- Toda dependência/comando roda por `uv` (`uv run ...`), nunca `pip`/`python` direto.
- A suíte exige Postgres e Redis rodando: `make up` antes de qualquer `pytest`.
- Comando de teste isolado: `uv run --group test pytest <caminho> -q` (o grupo `test` é obrigatório fora do Makefile).
- Docstrings no padrão Google. Cada tipo de módulo é **um arquivo** (`views.py`, `schema.py`, ...); `tests/` é a única exceção (pacote).
- Imports diretos do módulo que declara o objeto; nada de `shared.py` de reexport (ADR 0006).
- Commits em Conventional Commits (validado por commitlint no `commit-msg`).
- Antes de considerar o trabalho pronto: `make lint`, `make test`, `make docs` e `uv run python manage.py makemigrations --check --dry-run`.

## Contexto que o implementador precisa saber

1. **O auditlog hoje não grava em `LogAlteracao`.** `AUDITLOG_LOGENTRY_MODEL` nunca foi definido em `api/settings.py`, então `auditlog.get_logentry_model()` cai no default `auditlog.LogEntry` (tabela `auditlog_logentry`). Verificado neste repositório:

   ```
   modelo usado pelo auditlog -> auditlog.LogEntry auditlog_logentry
   ```

   Ou seja, a tabela `log_alteracao` está vazia e o endpoint `/logs_alteracao/` sempre respondeu lista vazia. Sem a Task 1 a nova action nasce morta. As linhas já gravadas em `auditlog_logentry` **não** são migradas (projeto em `0.1.0`, sem consumidores); a tabela antiga fica órfã.

2. **A remoção de `/logs_alteracao/` contraria [docs/reference/deprecacao-api.md](../../reference/deprecacao-api.md)**, que exige janela de `sunset` de 90 dias antes de retirar uma rota. Decisão tomada e aprovada: como o template está em `0.1.0` e a rota nunca devolveu dado real, ela sai direto, sinalizada como breaking change no commit da Task 4.

3. **`self.get_serializer()` e `self.filter_queryset()` não servem na action `logs`** — resolvem para o serializer/filterset do *recurso* (ex.: `Usuario`), não do log. O serializer do log é instanciado diretamente.

---

### Task 1: Ligar o auditlog no model do projeto

**Files:**
- Modify: `api/settings.py` (bloco `# base`, junto de `BASE_AUDITLOG_EXCLUDE_FIELDS`, ~linha 417)
- Test: `apps/api/base/tests/test_auditlog.py`

**Interfaces:**
- Consumes: nada.
- Produces: a partir daqui `auditlog.get_logentry_model()` retorna `apps.logs.models.LogAlteracao`, e todo `post_save`/`post_delete` de model registrado grava em `log_alteracao`. As tasks seguintes dependem disso.

- [ ] **Step 1: Escrever os testes que falham**

Acrescente ao final de `apps/api/base/tests/test_auditlog.py`:

```python
def test_auditlog_grava_no_model_de_log_do_projeto():
    assert get_logentry_model() is LogAlteracao


@pytest.mark.django_db
def test_criacao_e_alteracao_de_registro_geram_linhas_em_log_alteracao():
    usuario = criar_usuario(email="auditoria@exemplo.com", first_name="Antes")
    usuario.first_name = "Depois"
    usuario.save()

    registros = LogAlteracao.objects.get_for_object(usuario).order_by("timestamp")

    assert [registro.action for registro in registros] == [
        LogAlteracao.Action.CREATE,
        LogAlteracao.Action.UPDATE,
    ]
    assert registros[1].changes["first_name"] == ["Antes", "Depois"]
```

O arquivo já importa `pytest`, `auditlog.registry.auditlog` e `criar_usuario`. Faltam dois imports, que entram nos grupos existentes (bibliotecas de terceiros e apps do projeto):

```python
from auditlog import get_logentry_model

from apps.logs.models import LogAlteracao
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `uv run --group test pytest apps/api/base/tests/test_auditlog.py -q -k "log_alteracao or model_de_log"`
Expected: FAIL — o primeiro teste falha porque `get_logentry_model()` devolve `auditlog.LogEntry`; o segundo falha com lista vazia (`[] != [0, 1]`), porque nada é gravado em `log_alteracao`.

- [ ] **Step 3: Definir a setting**

Em `api/settings.py`, no bloco `# base`, logo acima de `BASE_AUDITLOG_EXCLUDE_FIELDS`:

```python
# base
# O auditlog escreve no model apontado por esta setting. Sem ela os signals
# gravariam em `auditlog.LogEntry` e a tabela `log_alteracao` — que a API expõe
# em `GET /<recurso>/<id>/logs/` — ficaria permanentemente vazia. O model
# apontado é excluído do próprio registro pelo auditlog, então não há recursão.
AUDITLOG_LOGENTRY_MODEL = "logs.LogAlteracao"

BASE_AUDITLOG_EXCLUDE_FIELDS = [
    "created_at",
    "last_modified_at",
]
```

- [ ] **Step 4: Rodar os testes e confirmar que passam**

Run: `uv run --group test pytest apps/api/base/tests/test_auditlog.py -q`
Expected: PASS em todos, inclusive os que já existiam (`test_registra_todos_os_modelos_concretos_dos_apps` continua verde — `LogAlteracao` não tem o decorator `utils.logs.register` e ainda entra no `DEFAULT_EXCLUDE_MODELS` do auditlog).

- [ ] **Step 5: Confirmar que nenhuma migration nova é exigida**

Run: `uv run python manage.py makemigrations --check --dry-run`
Expected: exit 0, sem mudanças pendentes (a tabela `log_alteracao` já existe desde `apps/logs/migrations/0001_initial.py`).

- [ ] **Step 6: Commit**

```bash
git add api/settings.py apps/api/base/tests/test_auditlog.py
git commit -m "fix: gravar trilha de auditoria em log_alteracao"
```

---

### Task 2: Action `logs` no `BaseModelViewSet`

**Files:**
- Modify: `apps/api/base/views.py` (novo mixin depois de `CacheInvalidationViewSetMixin`, ~linha 281; bases do `BaseModelViewSet`, ~linha 287)
- Modify: `apps/api/base/tests/test_view_mixins.py`
- Test: `apps/api/base/tests/test_view_logs.py` (criar)

**Interfaces:**
- Consumes: `LogAlteracao` gravado de fato (Task 1).
- Produces: `apps.api.base.views.LogsViewSetMixin`, com a action `logs` (`@action(methods=["get"], detail=True)`), presente em `BaseModelViewSet.get_extra_actions()`. As Tasks 3 e 4 referenciam o nome de action `"logs"` e o método `LogsViewSetMixin.logs`.

- [ ] **Step 1: Escrever o teste de comportamento que falha**

Crie `apps/api/base/tests/test_view_logs.py`:

```python
"""Testes da action de histórico de auditoria herdada pelo `BaseModelViewSet`."""

from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory

import pytest

from apps.api.base.serializers import BaseModelSerializer
from apps.api.base.views import BaseModelViewSet
from apps.logs.models import LogAlteracao
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario


class _UsuarioSerializer(BaseModelSerializer):
    class Meta:
        model = Usuario
        fields = ["id", "email", "first_name"]


class _UsuarioViewSet(BaseModelViewSet):
    queryset = Usuario.objects.all()
    serializer_class = _UsuarioSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    filter_backends = []


def _pedir_logs(usuario):
    request = APIRequestFactory().get(f"/usuarios/{usuario.pk}/logs/")
    view = _UsuarioViewSet.as_view({"get": "logs"})
    return view(request, pk=usuario.pk)


@pytest.mark.django_db
def test_logs_devolve_o_historico_do_registro_do_mais_recente_ao_mais_antigo():
    usuario = criar_usuario(email="alvo@exemplo.com", first_name="Antes")
    usuario.first_name = "Depois"
    usuario.save()

    response = _pedir_logs(usuario)

    assert response.status_code == 200
    registros = response.data["resultados"]
    assert [registro["action"] for registro in registros] == [
        LogAlteracao.Action.UPDATE,
        LogAlteracao.Action.CREATE,
    ]
    assert registros[0]["changes"]["first_name"] == ["Antes", "Depois"]
    assert registros[0]["model"] == "usuarios.usuario"


@pytest.mark.django_db
def test_logs_nao_vaza_o_historico_de_outro_registro():
    alvo = criar_usuario(email="alvo@exemplo.com")
    outro = criar_usuario(email="outro@exemplo.com")
    outro.first_name = "Renomeado"
    outro.save()

    response = _pedir_logs(alvo)

    assert {registro["object_id"] for registro in response.data["resultados"]} == {alvo.pk}


@pytest.mark.django_db
def test_logs_e_paginado_pelo_paginator_do_projeto():
    usuario = criar_usuario(email="alvo@exemplo.com", first_name="Nome0")
    for indice in range(1, 4):
        usuario.first_name = f"Nome{indice}"
        usuario.save()

    request = APIRequestFactory().get(f"/usuarios/{usuario.pk}/logs/", {"size": 2})
    response = _UsuarioViewSet.as_view({"get": "logs"})(request, pk=usuario.pk)

    assert response.data["total"] == 4
    assert len(response.data["resultados"]) == 2
    assert response.data["proxima"] is not None


@pytest.mark.django_db
def test_logs_recusa_metodos_de_escrita():
    usuario = criar_usuario(email="alvo@exemplo.com")
    request = APIRequestFactory().post(f"/usuarios/{usuario.pk}/logs/", {}, format="json")

    response = _UsuarioViewSet.as_view({"get": "logs"})(request, pk=usuario.pk)

    assert response.status_code == 405
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `uv run --group test pytest apps/api/base/tests/test_view_logs.py -q`
Expected: FAIL — `AttributeError`/`ImportError` ao montar a view, porque `logs` não existe no `BaseModelViewSet`.

- [ ] **Step 3: Implementar o mixin**

Em `apps/api/base/views.py`, adicione os imports (junto dos que já existem no topo, respeitando os grupos de import do ruff):

```python
from apps.logs.models import LogAlteracao
from apps.logs.serializers import LogAlteracaoSerpySerializer
```

E o mixin logo depois de `CacheInvalidationViewSetMixin`:

```python
class LogsViewSetMixin:
    """Mixin responsável pela action de histórico de auditoria do registro."""

    @action(methods=["get"], detail=True)
    def logs(self, request, *args, **kwargs):
        """Devolve a trilha de auditoria do registro, do mais recente ao mais antigo.

        Não aceita filtros: o recorte já é o objeto da URL. `self.filter_queryset()`
        e `self.get_serializer()` são deliberadamente ignorados aqui — ambos
        resolvem para o filterset/serializer do recurso, não do log.

        A autorização é dupla: `view_<model>` (declarada em
        `PermissionsViewSetMixin.base_permissions`) e o próprio `get_object()`,
        que passa pelo RLS e responde 404 para objeto de outra organização.

        Args:
            request: Request da action.
            *args: Argumentos posicionais do roteamento.
            **kwargs: Argumentos nomeados do roteamento (inclui `pk`).

        Returns:
            Response paginada com os registros de `LogAlteracao` do objeto.
        """
        instance = self.get_object()
        queryset = LogAlteracao.objects.get_for_object(instance).select_related("content_type", "actor")
        page = self.paginate_queryset(queryset)

        if page is None:
            return Response(LogAlteracaoSerpySerializer(queryset, many=True).data)

        serializer = LogAlteracaoSerpySerializer(page, many=True)
        return self.get_paginated_response(serializer.data)
```

Notas para quem implementa:
- `get_for_object()` é do manager do auditlog e já escolhe entre `object_id` (PK numérica) e `object_pk` (PK textual).
- A ordem `-timestamp` vem do `Meta.ordering` de `LogAlteracao`; não reordene na action.
- Não há ciclo de import: `apps.logs.serializers` importa `apps.api.base.serializers`, que não importa `views`.

- [ ] **Step 4: Ativar por padrão no `BaseModelViewSet`**

Altere a assinatura da classe:

```python
class BaseModelViewSet(UtilsViewSetMixin, LogsViewSetMixin, ModelViewSet):
```

- [ ] **Step 5: Rodar o teste e confirmar que passa**

Run: `uv run --group test pytest apps/api/base/tests/test_view_logs.py -q`
Expected: PASS nos 4 testes.

- [ ] **Step 6: Atualizar os testes de mixins que codificam o inventário de actions**

Em `apps/api/base/tests/test_view_mixins.py`:

1. Acrescente `("LogsViewSetMixin", ["logs"]),` ao final da lista do `@pytest.mark.parametrize` de `test_view_mixins_expoem_metodos_de_sua_responsabilidade`.
2. Troque o corpo de `test_base_model_viewset_expoe_apenas_crud_padrao` — a action `logs` agora é herdada por padrão, e é isso que o teste passa a proteger:

```python
def test_base_model_viewset_expoe_logs_alem_do_crud_padrao():
    action_names = {action.__name__ for action in views.BaseModelViewSet.get_extra_actions()}

    assert action_names == {"logs"}
    assert not hasattr(views.BaseModelViewSet, "has_is_active_field")
```

3. Em `test_actions_de_modelo_sao_opt_in`, o conjunto esperado passa a incluir `"logs"`, porque `ViewSetComActions` herda de `BaseModelViewSet`. Acrescente `"logs",` ao `assert action_names == {...}`.

- [ ] **Step 7: Rodar a suíte da base inteira**

Run: `uv run --group test pytest apps/api/base -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/api/base/views.py apps/api/base/tests/test_view_logs.py apps/api/base/tests/test_view_mixins.py
git commit -m "feat: expor historico de auditoria por registro"
```

---

### Task 3: Autorização da action (permission de modelo e scope de API key)

**Files:**
- Modify: `apps/api/base/views.py` (`PermissionsViewSetMixin.base_permissions`, ~linha 24)
- Modify: `apps/api/core/scope_mixins.py` (`SCOPE_ACTIONS_BY_VIEWSET_ACTION`, ~linha 11)
- Modify: `apps/api/base/tests/test_scopes.py`
- Test: `apps/api/base/tests/test_view_logs.py` (acrescentar)

**Interfaces:**
- Consumes: a action `logs` da Task 2.
- Produces: `logs` exige a permission `<app_label>.view_<model_name>` e o scope `<recurso>:read`.

- [ ] **Step 1: Escrever os testes que falham**

Em `apps/api/base/tests/test_scopes.py`, acrescente `("logs", "users:read"),` à lista do `@pytest.mark.parametrize` de `test_viewset_deriva_scope_por_action` (logo depois de `("form", "users:read"),`).

Em `apps/api/base/tests/test_view_logs.py`, acrescente ao final:

```python
def test_logs_exige_a_permission_de_leitura_do_recurso():
    from apps.api.base.views import PermissionsViewSetMixin

    assert PermissionsViewSetMixin.base_permissions["logs"] == ["%(app_label)s.view_%(model_name)s"]
```

- [ ] **Step 2: Rodar os testes e confirmar que falham**

Run: `uv run --group test pytest apps/api/base/tests/test_scopes.py apps/api/base/tests/test_view_logs.py -q`
Expected: FAIL — `KeyError: 'logs'` no teste de permission e `assert [] == ["users:read"]` no de scope.

- [ ] **Step 3: Declarar a permission da action**

Em `apps/api/base/views.py`, dentro de `PermissionsViewSetMixin.base_permissions`, acrescente a entrada mantendo a ordem das demais:

```python
    base_permissions = {
        "grid": ["%(app_label)s.view_%(model_name)s"],
        "form": ["%(app_label)s.view_%(model_name)s"],
        "logs": ["%(app_label)s.view_%(model_name)s"],
        "bulk_create": ["%(app_label)s.add_%(model_name)s"],
        ...
    }
```

- [ ] **Step 4: Declarar o scope da action**

Em `apps/api/core/scope_mixins.py`, dentro de `SCOPE_ACTIONS_BY_VIEWSET_ACTION`, logo depois de `"form"`:

```python
    "logs": ScopeAction.READ,
```

- [ ] **Step 5: Rodar os testes e confirmar que passam**

Run: `uv run --group test pytest apps/api/base/tests/test_scopes.py apps/api/base/tests/test_view_logs.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/base/views.py apps/api/core/scope_mixins.py apps/api/base/tests/test_scopes.py apps/api/base/tests/test_view_logs.py
git commit -m "feat: exigir permissao e scope de leitura na action de logs"
```

---

### Task 4: Schema OpenAPI da action e remoção do endpoint `/logs_alteracao/`

**Files:**
- Create: `apps/api/base/schema.py`
- Modify: `apps/api/base/views.py` (decorar a action `logs`)
- Delete: `apps/logs/views.py`, `apps/logs/urls.py`, `apps/logs/filtersets.py`, `apps/logs/schema.py`
- Test: `apps/api/base/tests/test_view_logs.py` (acrescentar)

**Interfaces:**
- Consumes: a action `logs` (Task 2) e `LogAlteracaoSerpySerializer` (`apps/logs/serializers.py`, que **permanece**).
- Produces: `apps.api.base.schema.LOGS_ACTION_SCHEMA` (um `extend_schema` pronto para aplicar na action) e `LogAlteracaoSchema`/`LogAlteracaoActorSchema` como espelhos de documentação. Depois desta task o app `logs` contém apenas `models.py`, `serializers.py`, `migrations/` e os módulos vazios da convenção.

- [ ] **Step 1: Escrever o teste que falha**

Acrescente ao final de `apps/api/base/tests/test_view_logs.py`:

```python
def test_endpoint_global_de_logs_nao_existe_mais():
    from django.urls import NoReverseMatch, reverse

    with pytest.raises(NoReverseMatch):
        reverse("log-alteracao-list")
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `uv run --group test pytest apps/api/base/tests/test_view_logs.py::test_endpoint_global_de_logs_nao_existe_mais -q`
Expected: FAIL — a rota ainda resolve, então `pytest.raises` não vê exceção alguma.

- [ ] **Step 3: Criar `apps/api/base/schema.py` com o espelho de documentação**

O conteúdo vem de `apps/logs/schema.py`, adaptado para uma action de detalhe. Crie `apps/api/base/schema.py`:

```python
"""Documentação OpenAPI das actions herdadas do `BaseModelViewSet`.

A leitura do histórico é servida por `LogAlteracaoSerpySerializer`, e serpy é
invisível para o `AutoSchema` — sem o serializer-espelho abaixo a action `logs`
apareceria no schema sem corpo de resposta. Ele existe só para documentar:
nenhuma request é serializada por ele, então mantenha-o em sincronia com o
serializer de leitura ao mexer nos campos.
"""

from rest_framework import serializers

from drf_spectacular.utils import OpenApiExample, extend_schema

from apps.api.autenticacao.errors import AuthErrorCode
from apps.api.core.errors import CoreErrorCode
from apps.api.core.schema import document_error_codes
from apps.organizacoes.errors import OrganizationErrorCode

ACOES_DESCRIPTION = (
    "`action` é numérico e ordenado por intrusividade — comparações com "
    "`__gt`/`__lt` fazem sentido:\n\n"
    "- `0`: criação\n"
    "- `1`: alteração\n"
    "- `2`: exclusão\n"
    "- `3`: acesso\n"
)

CHANGES_DESCRIPTION = (
    "`changes` é um objeto `{campo: [valor_antes, valor_depois]}`. Campos "
    "sensíveis aparecem mascarados e campos técnicos ficam de fora."
)

SOMENTE_LEITURA_DESCRIPTION = (
    "O histórico é **somente leitura** e não aceita filtros: o recorte é o "
    "próprio registro da URL, do mais recente ao mais antigo. Os registros são "
    "escritos pelo auditlog durante a request original e nunca reescritos — "
    "editá-los invalidaria a trilha de auditoria."
)


class LogAlteracaoActorSchema(serializers.Serializer):
    """Espelho do bloco `user` — nulo quando a alteração não teve ator autenticado."""

    id = serializers.IntegerField()
    nome = serializers.CharField()
    email = serializers.EmailField()


class LogAlteracaoSchema(serializers.Serializer):
    """Espelho de `LogAlteracaoSerpySerializer` para o schema OpenAPI."""

    id = serializers.IntegerField(read_only=True)
    user = LogAlteracaoActorSchema(allow_null=True)
    action = serializers.IntegerField(help_text="0=criação, 1=alteração, 2=exclusão, 3=acesso.")
    model = serializers.CharField(help_text="Modelo auditado, no formato `app_label.model`.")
    object_id = serializers.IntegerField(allow_null=True, help_text="PK numérica do objeto auditado.")
    object_repr = serializers.CharField(help_text="Representação textual do objeto no momento do registro.")
    changes = serializers.DictField(help_text="Diff no formato `{campo: [antes, depois]}`.")
    created_at = serializers.CharField(help_text="Momento do registro, em ISO 8601.")


EXEMPLO_REGISTRO = OpenApiExample(
    "Alteração de um campo",
    value={
        "id": 8421,
        "user": {"id": 12, "nome": "Maria Souza", "email": "maria@example.com"},
        "action": 1,
        "model": "organizacoes.organizacao",
        "object_id": 3,
        "object_repr": "Acme",
        "changes": {"nome": ["Acme LTDA", "Acme"]},
        "created_at": "2026-08-09T14:12:03.415Z",
    },
    response_only=True,
)

LOGS_ACTION_SCHEMA = extend_schema(
    summary="Lista o histórico de auditoria do registro",
    description="\n\n".join([SOMENTE_LEITURA_DESCRIPTION, ACOES_DESCRIPTION, CHANGES_DESCRIPTION]),
    responses={
        200: LogAlteracaoSchema(many=True),
        401: document_error_codes(
            AuthErrorCode.NOT_AUTHENTICATED,
            AuthErrorCode.INVALID_TOKEN,
            AuthErrorCode.EXPIRED_TOKEN,
        ),
        403: document_error_codes(AuthErrorCode.PERMISSION_DENIED),
        404: document_error_codes(CoreErrorCode.NOT_FOUND),
        422: document_error_codes(
            OrganizationErrorCode.HEADER_REQUIRED,
            OrganizationErrorCode.MEMBERSHIP_REQUIRED,
        ),
    },
    examples=[EXEMPLO_REGISTRO],
)
```

- [ ] **Step 4: Aplicar o schema na action**

Em `apps/api/base/views.py`, importe e decore. `LOGS_ACTION_SCHEMA` fica **acima** de `@action` — é a ordem documentada pelo drf-spectacular para actions de ViewSet:

```python
from .schema import LOGS_ACTION_SCHEMA
```

```python
    @LOGS_ACTION_SCHEMA
    @action(methods=["get"], detail=True)
    def logs(self, request, *args, **kwargs):
```

Depois de aplicar, confirme que a action continua roteável (o `extend_schema` não pode ter comido os atributos do `@action`):

Run: `uv run --group test pytest apps/api/base/tests/test_view_mixins.py -q`
Expected: PASS — `get_extra_actions()` ainda enxerga `logs`.

- [ ] **Step 5: Remover o endpoint global**

```bash
git rm apps/logs/views.py apps/logs/urls.py apps/logs/filtersets.py apps/logs/schema.py
```

Sem `urls.py`, o app deixa de ser coletado pelo `apps_urls` de `api/urls.py` automaticamente — não há nada a editar lá. `apps.logs` continua em `BUSINESS_APPS` (o model precisa dele).

- [ ] **Step 6: Confirmar que nada mais referencia os módulos removidos**

Run: `grep -rn "logs_alteracao\|LogAlteracaoViewSet\|LogAlteracaoFilterSet\|LOG_ALTERACAO_SCHEMA" --include="*.py" --include="*.md" apps api docs README.md CLAUDE.md .ai`
Expected: nenhuma ocorrência em código; ocorrências em `docs/` são tratadas na Task 5.

- [ ] **Step 7: Rodar os testes e o schema**

Run: `uv run --group test pytest apps/api/base apps/logs -q`
Expected: PASS.

Run: `uv run python manage.py spectacular --fail-on-warn > /dev/null`
Expected: exit 0, sem warning — confirma que a action `logs` documenta corpo de resposta e que nenhuma referência ao schema removido ficou pendurada.

- [ ] **Step 8: Commit**

```bash
git add apps/api/base/schema.py apps/api/base/views.py apps/api/base/tests/test_view_logs.py
git commit -m "feat!: trocar /logs_alteracao/ por /<recurso>/<id>/logs/

BREAKING CHANGE: o endpoint global GET /logs_alteracao/ foi removido. O
histórico passa a ser lido em GET /<recurso>/<id>/logs/, herdado por todo
BaseModelViewSet e restrito a quem tem view_<model> sobre o recurso."
```

---

### Task 5: Documentação

**Files:**
- Modify: `.ai/CONVENTIONS.md` (tabela da §4.4, ~linha 291)
- Modify: `CLAUDE.md` (~linha 135)
- Modify: `README.md` (seção de Cache/actions, ~linha 162)
- Modify: `docs/reference/api.md`

**Interfaces:**
- Consumes: a action `logs` e a remoção da Task 4.
- Produces: nada de código.

- [ ] **Step 1: Atualizar a tabela de actions herdadas em `.ai/CONVENTIONS.md`**

Na tabela da §4.4, acrescente a linha depois de `form`:

```markdown
| `logs` | GET (detail) | Histórico de auditoria do registro (paginado, sem filtros). |
```

- [ ] **Step 2: Atualizar `CLAUDE.md`**

Na seção "Camada base da API", a frase que lista as actions passa a incluir `logs`:

```markdown
- `BaseModelViewSet` entrega de graça as actions `grid`, `form`, `values`, `logs`,
  `bulk_create`, `bulk_update`, `clonar`, `invalidate_cache` e `ativar`/`inativar`.
```

E acrescente, ao final da mesma seção:

```markdown
- `GET /<recurso>/<id>/logs/` devolve a trilha de auditoria do registro
  (`apps.logs.models.LogAlteracao`), exigindo `view_<model>`. Não aceita filtros,
  e o `get_object()` garante que só há log de objeto visível pelo RLS. O app
  `logs` não publica rota própria: guarda apenas o model e o serializer.
```

- [ ] **Step 3: Atualizar `README.md`**

Na seção que descreve o `BaseModelViewSet`, acrescente ao lado da menção a `invalidate_cache`:

```markdown
- `GET .../<id>/logs/` devolve o histórico de auditoria do registro, paginado.
```

- [ ] **Step 4: Atualizar `docs/reference/api.md`**

O arquivo organiza as rotas em tabelas por assunto (`## Autenticação`, `## Senha`). Acrescente uma seção nova, logo antes de `## Autenticação`:

```markdown
## Histórico de auditoria

Todo recurso servido por um `BaseModelViewSet` publica o próprio histórico.

| Rota | Descrição |
| --- | --- |
| `GET /<recurso>/<id>/logs/` | Trilha de auditoria do registro, paginada e ordenada do mais recente ao mais antigo |

Exige a mesma permissão de leitura do recurso (`view_<model>`) e não aceita
filtros — o recorte é o próprio registro da URL. Como a resolução passa pelo
`get_object()`, um registro de outra organização responde `404`, não `403`.
Não existe endpoint global de logs: o app `logs` guarda só o model
`LogAlteracao` e seu serializer.
```

- [ ] **Step 5: Validar a documentação**

Run: `uv run mkdocs build --strict`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add .ai/CONVENTIONS.md CLAUDE.md README.md docs/reference/api.md
git commit -m "docs: documentar a action de logs por registro"
```

---

### Verificação final

- [ ] **Step 1: Suíte completa**

Run: `make test`
Expected: PASS.

- [ ] **Step 2: Lint e migrations**

Run: `make lint && uv run python manage.py makemigrations --check --dry-run`
Expected: exit 0 nos dois.

- [ ] **Step 3: Docs e versão**

Run: `make docs && make version-check`
Expected: exit 0 nos dois.
