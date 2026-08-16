# Revogação de Conta Excluída — Plano de Implementação

> **Para agentes:** SKILL SUBOBRIGATÓRIA: use `superpowers:subagent-driven-development` (recomendado) ou `superpowers:executing-plans` para executar este plano tarefa a tarefa. Os passos usam checkboxes (`- [ ]`) para acompanhamento.

**Objetivo:** impedir imediatamente o uso de uma conta Django soft-deleted, revogando suas credenciais e invalidando a autorização em cache.

**Arquitetura:** `Usuario.delete()` passa a ser o único protocolo de revogação de conta: bloqueia a linha, marca `is_deleted` e `is_active` juntos, revoga credenciais e dispositivos confiáveis. Um queryset específico de usuários delega a exclusão em lote ao mesmo método; autenticação e autorização também recusam `is_deleted` como defesa contra registros legados.

**Tecnologias:** Django 5.2, Django REST Framework, django-knox, pytest-django, django-guardian, Redis permission cache.

## Restrições globais

- Preservar soft-delete e todos os registros de auditoria; não remover fisicamente usuário, sessão ou API key.
- A exclusão precisa ser idempotente e transacional por usuário.
- Sessões, API keys, pré-autenticações, tokens de reset de senha e dispositivos confiáveis deixam de ser utilizáveis no commit da exclusão.
- Autenticadores, resolvers e backends recusam `is_deleted=True`, mesmo quando a credencial ou um snapshot preexistente ainda existir.
- Não criar migration: a alteração usa apenas campos já existentes.
- Executar testes fora do Makefile com `uv run --group test pytest --nomigrations` e as variáveis `DATABASE_*` locais.
- Não tocar nos arquivos não rastreados `TODO.md` e `docs/research/2026-08-15-*.md`.

---

## Arquivos e responsabilidades

| Arquivo | Mudança | Responsabilidade |
|---|---|---|
| `apps/usuarios/models.py` | modificar | Definir queryset/manager de usuário e o protocolo transacional de soft-delete. |
| `apps/usuarios/tests/test_account_deletion.py` | criar | Cobrir exclusão individual, em lote e idempotência. |
| `apps/api/autenticacao/services.py` | modificar | Expor revogação em lote de todos os tipos de credencial de uma conta. |
| `apps/api/autenticacao/authentications.py` | modificar | Recusar responsável excluído nos caminhos DRF e Knox. |
| `apps/api/autenticacao/tests/tokens/test_account_deletion.py` | criar | Cobrir revogação de tokens, API keys e dispositivos e a recusa HTTP posterior. |
| `internal_frameworks/permission_cache/resolvers/django.py` | modificar | Não carregar snapshot para usuário excluído. |
| `internal_frameworks/permission_cache/resolvers/guardian.py` | modificar | Não carregar permissões de objeto para usuário excluído. |
| `internal_frameworks/permission_cache/backends.py` | modificar | Nunca conceder permissões de modelo/objeto para usuário excluído. |
| `apps/api/autenticacao/tests/test_permission_cache.py` | modificar | Fixar a matriz de permissões para usuário soft-deleted. |
| `internal_frameworks/permission_cache/tests/signals/test_django_signals.py` | modificar | Exigir invalidação no commit da exclusão lógica. |

## Interfaces entre tarefas

- `revoke_all_user_credentials(user: Usuario, *, actor: Usuario | None = None) -> int` será definido em `apps.api.autenticacao.services` e chamado por `Usuario.delete()`.
- `Usuario.delete(using: str | None = None, keep_parents: bool = False) -> tuple[int, dict[str, int]]` será idempotente; retorna a convenção de `QuerySet.delete()` (`0` ou `1` e o mapa do label).
- `UsuarioQuerySet.delete() -> tuple[int, dict[str, int]]` executará `instance.delete(using=self.db)` para cada PK vivo dentro de uma transação, em vez de `update(is_deleted=True)`.

### Task 1: Escrever regressões do protocolo de exclusão

**Arquivos:**

- Criar: `apps/usuarios/tests/test_account_deletion.py`
- Criar: `apps/api/autenticacao/tests/tokens/test_account_deletion.py`
- Modificar: `apps/api/autenticacao/tests/test_permission_cache.py`
- Modificar: `internal_frameworks/permission_cache/tests/signals/test_django_signals.py`

**Consome:** os factories existentes `criar_usuario`, `AuthToken.objects.create`, `TokenMetaData.objects.create` e `create_trusted_device`.

**Produz:** uma especificação executável de que exclusão de conta desativa usuário, revoga credenciais e elimina autorização.

- [ ] **Passo 1: Criar o teste de exclusão individual e em lote**

```python
# apps/usuarios/tests/test_account_deletion.py
import pytest

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from tests.support.usuarios import criar_usuario


@pytest.mark.django_db(transaction=True)
def test_delete_desativa_e_revoga_todas_as_credenciais(usuario, organizacao):
    session, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    api_key, _ = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    pre_auth, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.PRE_AUTH)
    reset, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.RESET_PASSWORD)
    for token in (session, api_key, pre_auth, reset):
        TokenMetaData.objects.create(token=token)

    usuario.delete()

    usuario.refresh_from_db(from_queryset=type(usuario).all_objects.all())
    assert usuario.is_deleted is True
    assert usuario.is_active is False
    assert not AuthToken.objects.filter(responsavel=usuario, revoked_at__isnull=True).exists()


@pytest.mark.django_db(transaction=True)
def test_queryset_delete_usa_o_mesmo_protocolo():
    usuario = criar_usuario()

    deleted_count, deleted_by_model = type(usuario).objects.filter(pk=usuario.pk).delete()

    usuario.refresh_from_db(from_queryset=type(usuario).all_objects.all())
    assert deleted_count == 1
    assert deleted_by_model == {usuario._meta.label: 1}
    assert usuario.is_deleted is True
    assert usuario.is_active is False
```

Use fixtures existentes ou declare `usuario`/`organizacao` localmente quando não houver fixture compartilhada disponível.

- [ ] **Passo 2: Criar a regressão HTTP de credencial emitida antes da exclusão**

```python
# apps/api/autenticacao/tests/tokens/test_account_deletion.py
import pytest
from rest_framework.test import APIClient

from apps.api.autenticacao.models import AuthToken, TokenMetaData, TokenType
from apps.organizacoes.models import Organizacao, Vinculo
from tests.support.usuarios import criar_usuario


@pytest.mark.django_db(transaction=True)
def test_api_key_emitida_antes_da_exclusao_nao_autentica():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Org", slug="org-conta-excluida")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao)
    token, plain_token = AuthToken.objects.create(
        responsavel=usuario,
        type=TokenType.API_KEY,
        created_by=usuario,
        organization=organizacao,
        name="Integração",
        scopes=["teams:read"],
    )
    TokenMetaData.objects.create(token=token)
    usuario.delete()

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {plain_token}")
    response = client.get("/times/")

    assert response.status_code == 401
```

- [ ] **Passo 3: Estender a matriz de resolução de permissões**

```python
# acrescentar em apps/api/autenticacao/tests/test_permission_cache.py
def test_usuario_excluido_nao_tem_permissoes_de_modelo_nem_objeto():
    user = criar_usuario()
    user.user_permissions.add(permission("view_organizacao"))
    user.delete()
    user.refresh_from_db(from_queryset=Usuario.all_objects.all())
    backend = CachedModelBackend()

    assert backend.get_all_permissions(user) == set()
    assert backend.has_perm(user, "organizacoes.view_organizacao") is False
```

Inclua também um teste equivalente para `CachedObjectPermissionBackend` com uma permissão Guardian atribuída antes da exclusão.

- [ ] **Passo 4: Ajustar o teste de sinal para a semântica nova**

Em `test_user_delete_bumps_all_user_layers`, além dos bumps no commit, recarregue o usuário por `Usuario.all_objects` e exija `is_deleted is True` e `is_active is False`.

- [ ] **Passo 5: Rodar as regressões e confirmar a falha inicial**

Execute:

```console
uv run --group test pytest --nomigrations \
  apps/usuarios/tests/test_account_deletion.py \
  apps/api/autenticacao/tests/tokens/test_account_deletion.py \
  apps/api/autenticacao/tests/test_permission_cache.py \
  internal_frameworks/permission_cache/tests/signals/test_django_signals.py -q
```

Esperado: falha porque `Usuario.delete()` ainda preserva `is_active`, exclusão por queryset ainda faz `update()` e resolvers ainda aceitam `is_deleted=True`.

### Task 2: Implementar revogação transacional e exclusão por queryset

**Arquivos:**

- Modificar: `apps/api/autenticacao/services.py`
- Modificar: `apps/usuarios/models.py`
- Teste: `apps/usuarios/tests/test_account_deletion.py`
- Teste: `apps/api/autenticacao/tests/tokens/test_account_deletion.py`

**Consome:** `AuthToken`, `TokenType` e `revoke_trusted_devices` já existentes.

**Produz:** `revoke_all_user_credentials()` e o protocolo canônico de exclusão de `Usuario`.

- [ ] **Passo 1: Escrever o teste unitário do serviço de revogação**

```python
def test_revoke_all_user_credentials_revoga_todos_os_tipos(usuario):
    tokens = [
        AuthToken.objects.create(responsavel=usuario, type=token_type)[0]
        for token_type in TokenType
        if token_type != TokenType.API_KEY
    ]

    changed = revoke_all_user_credentials(usuario)

    assert changed == len(tokens)
    assert not AuthToken.objects.filter(pk__in=[token.pk for token in tokens], revoked_at__isnull=True).exists()
```

Crie uma API key válida separadamente, com organização, nome, criador e scopes, e inclua-a na asserção.

- [ ] **Passo 2: Implementar o serviço sem apagar registros**

```python
# apps/api/autenticacao/services.py
def revoke_all_user_credentials(user, *, actor=None) -> int:
    """Revoga todas as credenciais de um usuário preservando a auditoria."""
    auth_token_model = get_token_model()
    return auth_token_model.objects.filter(
        responsavel=user,
        revoked_at__isnull=True,
    ).update(revoked_at=timezone.now(), revoked_by=actor)
```

Não use `delete()` para `PRE_AUTH` e `RESET_PASSWORD`: os tokens ainda ativos
precisam deixar rastro de revogação consistente com sessões e API keys.

- [ ] **Passo 3: Substituir a exclusão lógica de `Usuario` pelo protocolo**

Em `apps/usuarios/models.py`, crie `UsuarioQuerySet(BaseQuerySet)` antes dos
managers. Seu `delete()` deve materializar somente PKs vivos, abrir
`transaction.atomic(using=self.db)`, recuperar cada linha por
`self.model.all_objects.using(self.db).select_for_update().get(pk=user_id)` e
chamar `usuario.delete(using=self.db)`. Retorne o total e
`{self.model._meta.label: total}`.

Faça `UsuarioManager` e o manager `all_objects` usarem esse queryset. Preserve
os filtros atuais: `objects` exclui `is_deleted=True`; `all_objects` não filtra;
`ativos` continua exigindo `is_active=True` e `is_deleted=False`.

Sobrescreva `Usuario.delete()` com a estrutura abaixo:

```python
def delete(self, using=None, keep_parents=False):
    del keep_parents
    database_alias = using or self._state.db or "default"
    with transaction.atomic(using=database_alias):
        locked = type(self).all_objects.using(database_alias).select_for_update().get(pk=self.pk)
        if locked.is_deleted:
            return 0, {}
        locked.is_deleted = True
        locked.is_active = False
        locked.save(using=database_alias, update_fields=["is_deleted", "is_active"])

        from apps.api.autenticacao.mfa import revoke_trusted_devices
        from apps.api.autenticacao.services import revoke_all_user_credentials

        revoke_all_user_credentials(locked)
        revoke_trusted_devices(locked)
        self.is_deleted = True
        self.is_active = False
    return 1, {self._meta.label: 1}
```

Use imports locais para evitar ciclo de carregamento entre usuário,
autenticação e MFA. Preserve o alias em toda query e atualização.

- [ ] **Passo 4: Rodar os testes de exclusão e credenciais**

Execute:

```console
uv run --group test pytest --nomigrations \
  apps/usuarios/tests/test_account_deletion.py \
  apps/api/autenticacao/tests/tokens/test_account_deletion.py \
  apps/api/autenticacao/tests/tokens/test_token_services.py -q
```

Esperado: todos passam. Repetir `usuario.delete()` retorna `(0, {})`, não cria novas revogações e não muda `revoked_at` já persistido.

- [ ] **Passo 5: Criar o commit da mudança de ciclo de vida**

```console
git add apps/usuarios/models.py apps/usuarios/tests/test_account_deletion.py \
  apps/api/autenticacao/services.py apps/api/autenticacao/tests/tokens/test_account_deletion.py
git commit -m "fix(auth): revoke credentials for deleted users"
```

### Task 3: Fechar autenticação e autorização como defesa em profundidade

**Arquivos:**

- Modificar: `apps/api/autenticacao/authentications.py`
- Modificar: `internal_frameworks/permission_cache/resolvers/django.py`
- Modificar: `internal_frameworks/permission_cache/resolvers/guardian.py`
- Modificar: `internal_frameworks/permission_cache/backends.py`
- Modificar: `apps/api/autenticacao/tests/test_passthrough.py`
- Modificar: `apps/api/autenticacao/tests/test_permission_cache.py`
- Modificar: `internal_frameworks/permission_cache/tests/signals/test_django_signals.py`

**Consome:** `Usuario.is_deleted`, os resolvers existentes e a invalidação por `post_save` já ligada a `is_deleted`.

**Produz:** nenhuma camada de autenticação/autorizações concede acesso a um usuário excluído, mesmo com objeto ou cache legado.

- [ ] **Passo 1: Escrever regressões de recusas diretas**

```python
# apps/api/autenticacao/tests/test_passthrough.py
def test_passthrough_recusa_usuario_excluido(rf):
    request = rf.get("/v1/pedidos/")
    setattr(request, REQUEST_ATTR_RESOLVED, RESOLVED_PRIVATE)
    request.user = UsuarioFalso(is_active=True, is_deleted=True)

    with pytest.raises(AuthenticationFailed):
        PassthroughAuthentication().authenticate(request)
```

Crie, nos testes de autenticação tipada, um responsável com
`is_active=True, is_deleted=True` e exija `APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=401)`.

- [ ] **Passo 2: Aplicar a guarda em autenticação e resolvers**

Em `PassthroughAuthentication.authenticate`, substitua a guarda por:

```python
if user is None or not user.is_active or getattr(user, "is_deleted", False):
    raise AuthenticationFailed("Usuário inativo ou inválido.")
```

Em `TypedTokenAuthentication.validate_user`, amplie a condição do responsável:

```python
if not auth_token.responsavel.is_active or auth_token.responsavel.is_deleted:
    raise APIError(AuthErrorCode.RESPONSIBLE_INACTIVE, status_code=401)
```

Em `DjangoPermissionResolver.resolve` e `GuardianPermissionResolver.resolve`,
adicione `getattr(user_obj, "is_deleted", False)` às condições de retorno do
snapshot vazio.

Em `CachedModelBackend.has_perm` e `has_module_perms`, requeira também
`not getattr(user_obj, "is_deleted", False)`. Em
`CachedObjectPermissionBackend.has_perm`, devolva `False` antes de tratar
superusuário quando o usuário for excluído.

- [ ] **Passo 3: Rodar a matriz de autenticação e autorização**

Execute:

```console
uv run --group test pytest --nomigrations \
  apps/api/autenticacao/tests/test_passthrough.py \
  apps/api/autenticacao/tests/test_permission_cache.py \
  apps/api/autenticacao/tests/api_keys/test_api_key_tenancy.py \
  internal_frameworks/permission_cache/tests/signals/test_django_signals.py -q
```

Esperado: todos passam; a API key emitida antes da exclusão retorna HTTP 401 e
nenhum backend concede permissão a uma conta excluída, inclusive superusuário.

- [ ] **Passo 4: Executar verificações de integração e migração**

Execute:

```console
make lint
uv run python manage.py makemigrations --check --dry-run
uv run --group test pytest --nomigrations apps/usuarios apps/api/autenticacao internal_frameworks/permission_cache -q
```

Esperado: Ruff limpo, `No changes detected` para migrations e toda a suíte
afetada aprovada.

- [ ] **Passo 5: Criar o commit de defesa em profundidade**

```console
git add apps/api/autenticacao/authentications.py \
  apps/api/autenticacao/tests/test_passthrough.py \
  apps/api/autenticacao/tests/test_permission_cache.py \
  internal_frameworks/permission_cache/resolvers/django.py \
  internal_frameworks/permission_cache/resolvers/guardian.py \
  internal_frameworks/permission_cache/backends.py \
  internal_frameworks/permission_cache/tests/signals/test_django_signals.py
git commit -m "fix(auth): deny authorization for deleted users"
```

## Revisão do plano

- Cobertura do spec: o protocolo transacional, idempotência, revogação de
  todas as credenciais e dispositivos, exclusão em lote, defesa em
  profundidade e invalidação de cache aparecem nas tarefas 1–3.
- Sem migration: nenhuma tarefa altera schema ou choices persistidos.
- Consistência de nomes: `revoke_all_user_credentials`, `Usuario.delete` e
  `UsuarioQuerySet.delete` são definidos na tarefa 2 e consumidos somente nas
  tarefas posteriores.
- Escopo: organizações e seus vínculos não são alterados; essa correção fecha
  apenas o acesso da conta excluída.
