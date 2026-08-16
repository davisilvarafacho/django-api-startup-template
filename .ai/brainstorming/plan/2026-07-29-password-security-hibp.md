# Password Security and HIBP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar validação HIBP, alteração autenticada e redefinição deslogada de senha sem enumeração de contas.

**Architecture:** Um validador Django delega a consulta k-anonymous a um cliente HIBP isolado. Um serviço único valida/define senha e revoga credenciais; views finas expõem change/reset, e tasks geram tokens e enviam e-mail sem transportar segredo no broker.

**Tech Stack:** Django 5.2 password validation, DRF 3.16, httpx 0.28, Celery 5.6, Knox custom token, pytest-django.

## Global Constraints

- Executar depois de `2026-07-29-auth-token-foundation.md` e
  `2026-07-29-mfa-api.md`, para que mudança de senha revogue desafios e trusted
  devices reais.
- HIBP bloqueia qualquer contagem maior que zero.
- HIBP usa SHA-1 local, prefixo de cinco caracteres e `Add-Padding: true`.
- Falhas de rede/timeout/resposta inválida são fail-open e observáveis.
- HIBP só roda quando uma senha é definida; nunca durante login.
- `create_superuser()` ignora todos os validadores; demais fluxos validam.
- Reset deslogado não exige MFA.
- Respostas de solicitação de reset não enumeram contas.

---

## File Structure

- `apps/usuarios/passwords.py`: validação e alteração transacional.
- `apps/usuarios/password_validation.py`: cliente e validator HIBP.
- `apps/api/autenticacao/passwords.py`: emissão/consumo do reset e revogações.
- `apps/api/autenticacao/tasks.py`: e-mail de reset e avisos.
- `apps/api/autenticacao/serializers.py`: inputs de change/reset.
- `apps/api/autenticacao/views.py`: endpoints finos.

### Task 1: Implementar cliente e validator HIBP

**Files:**
- Create: `apps/usuarios/password_validation.py`
- Create: `apps/usuarios/tests/test_password_validation.py`
- Modify: `api/settings.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `PwnedPasswordsClient.is_pwned(password: str) -> bool`.
- Produces: `PwnedPasswordValidator.validate(password, user=None) -> None`.

- [ ] **Step 1: Escrever testes RED**

```python
def test_cliente_envia_apenas_prefixo_e_padding(monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured.update(url=url, headers=headers, timeout=timeout)
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, text="ABCDEF:4\r\n")

    monkeypatch.setattr(httpx, "get", fake_get)
    client = PwnedPasswordsClient(base_url="https://example.test", timeout=0.1)
    client.is_pwned("senha")
    assert captured["headers"]["Add-Padding"] == "true"
    assert len(captured["url"].rsplit("/", 1)[-1]) == 5
    assert "senha" not in captured["url"]


def test_validator_recusa_qualquer_ocorrencia(monkeypatch):
    monkeypatch.setattr(PwnedPasswordsClient, "is_pwned", lambda self, password: True)
    with pytest.raises(ValidationError, match="vazamentos"):
        PwnedPasswordValidator().validate("senha")
```

Cobrir resposta sem match, count zero, timeout, HTTP 500 e payload malformado.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/usuarios/tests/test_password_validation.py -q`

Expected: FAIL por módulo inexistente.

- [ ] **Step 3: Implementar cliente**

Calcular `hashlib.sha1(password.encode("utf-8"), usedforsecurity=False)`,
enviar somente prefixo, descartar linhas de count zero e nunca cachear o range.
Capturar `httpx.HTTPError`, timeout e parse error; registrar classe/latência sem
prefixo e retornar `False`.

- [ ] **Step 4: Registrar validator**

Adicionar `apps.usuarios.password_validation.PwnedPasswordValidator` a
`AUTH_PASSWORD_VALIDATORS`. Definir `HIBP_PASSWORD_CHECK_ENABLED`,
`HIBP_PASSWORDS_URL` e `HIBP_TIMEOUT_SECONDS` pelos helpers de env.

- [ ] **Step 5: Verificar e commit**

Run: `uv run pytest apps/usuarios/tests/test_password_validation.py -q`

Expected: PASS sem rede real.

```bash
git add api/settings.py .env.example apps/usuarios/password_validation.py apps/usuarios/tests/test_password_validation.py
git commit -m "feat(auth): reject pwned passwords"
```

### Task 2: Centralizar definição de senha e exceção de superusuário

**Files:**
- Create: `apps/usuarios/passwords.py`
- Modify: `apps/usuarios/models.py`
- Modify: `apps/usuarios/factories.py`
- Create: `apps/usuarios/tests/test_password_service.py`

**Interfaces:**
- Produces: `set_validated_password(user, raw_password, *, save=True) -> Usuario`.
- Produces: `UsuarioManager.create_user()` validado e `create_superuser()` não validado.

- [ ] **Step 1: Escrever testes**

```python
@pytest.mark.django_db
def test_create_user_executa_validadores(monkeypatch):
    called = []
    monkeypatch.setattr("apps.usuarios.passwords.validate_password", lambda password, user: called.append(user))
    user = Usuario.objects.create_user(email="u@example.com", password="Senha123!")
    assert called == [user]


@pytest.mark.django_db
def test_create_superuser_ignora_validadores(monkeypatch):
    monkeypatch.setattr(
        "apps.usuarios.passwords.validate_password",
        lambda password, user: pytest.fail("não deve validar"),
        raising=False,
    )
    user = Usuario.objects.create_superuser(
        email="root@example.com",
        password="qualquer",
        first_name="Root",
        last_name="User",
    )
    assert user.check_password("qualquer")
```

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/usuarios/tests/test_password_service.py -q`

Expected: FAIL porque manager usa `make_password()` diretamente.

- [ ] **Step 3: Implementar serviço e manager**

`set_validated_password()` chama `validate_password(raw_password, user)`,
depois `user.set_password()` e `save(update_fields=["password"])`.
`create_user()` monta a instância, valida e salva. `create_superuser()` chama
um caminho privado com `validate=False`.

- [ ] **Step 4: Verificar e commit**

Run: `uv run pytest apps/usuarios/tests/test_password_service.py apps/usuarios/tests -q`

Expected: PASS.

```bash
git add apps/usuarios/models.py apps/usuarios/passwords.py apps/usuarios/factories.py apps/usuarios/tests/test_password_service.py
git commit -m "refactor(auth): centralize password updates"
```

### Task 3: Implementar reset deslogado

**Files:**
- Create: `apps/api/autenticacao/passwords.py`
- Modify: `apps/api/autenticacao/tasks.py`
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/views.py`
- Modify: `apps/api/autenticacao/urls.py`
- Modify: `apps/api/autenticacao/public_routes.py`
- Create: `apps/api/autenticacao/tests/test_password_reset.py`
- Modify: `api/settings.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `issue_password_reset(user) -> IssuedToken`.
- Produces: `consume_password_reset(plain_token, new_password) -> Usuario`.
- Produces: `POST /auth/password/reset/request/`.
- Produces: `POST /auth/password/reset/confirm/`.

- [ ] **Step 1: Escrever testes de segurança**

```python
@pytest.mark.django_db
def test_request_nao_enumera_conta(api_client, usuario):
    existing = api_client.post("/auth/password/reset/request/", {"email": usuario.email})
    missing = api_client.post("/auth/password/reset/request/", {"email": "none@example.com"})
    assert existing.status_code == missing.status_code == 202
    assert existing.json() == missing.json()


@pytest.mark.django_db
def test_confirm_consumo_unico(api_client, reset_token):
    payload = {
        "token": reset_token,
        "new_password": "NovaSenha123!",
        "new_password_confirmation": "NovaSenha123!",
    }
    assert api_client.post("/auth/password/reset/confirm/", payload).status_code == 204
    assert api_client.post("/auth/password/reset/confirm/", payload).status_code == 400
```

Cobrir TTL de 30 minutos, token anterior revogado, usuário inativo, throttle,
HIBP, ausência de MFA e payload Celery sem token puro.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_password_reset.py -q`

Expected: FAIL com 404 nas rotas.

- [ ] **Step 3: Implementar emissão na task**

O request enfileira somente `user.pk`. A task revoga resets ativos, chama
`issue_token(responsavel=user, token_type=TokenType.RESET_PASSWORD,
expiry=timedelta(minutes=30), metadata_input={})`, monta
`PASSWORD_RESET_FRONTEND_URL?token=<plain>` e envia e-mail. O token puro vive
somente na memória da task.

- [ ] **Step 4: Implementar consumo**

Autenticar o token explicitamente como `RESET_PASSWORD`, usar
`select_for_update()`, validar expiração/revogação, chamar
`set_validated_password()`, marcar token revogado e chamar
`revoke_credentials_after_password_change()` na mesma transação.

- [ ] **Step 5: Verificar e commit**

Run: `uv run pytest apps/api/autenticacao/tests/test_password_reset.py -q`

Expected: PASS.

```bash
git add api/settings.py .env.example apps/api/autenticacao
git commit -m "feat(auth): add password reset flow"
```

### Task 4: Implementar alteração autenticada e revogações

**Files:**
- Modify: `apps/api/autenticacao/passwords.py`
- Modify: `apps/api/autenticacao/tasks.py`
- Modify: `apps/api/autenticacao/serializers.py`
- Modify: `apps/api/autenticacao/views.py`
- Modify: `apps/api/autenticacao/urls.py`
- Create: `apps/api/autenticacao/tests/test_password_change.py`

**Interfaces:**
- Produces: `revoke_credentials_after_password_change(user) -> None`.
- Produces: `POST /auth/password/change/`.

- [ ] **Step 1: Escrever testes**

```python
@pytest.mark.django_db
def test_change_revoga_inclusive_sessao_atual(auth_client, usuario):
    response = auth_client.post(
        "/auth/password/change/",
        {
            "new_password": "NovaSenha123!",
            "new_password_confirmation": "NovaSenha123!",
        },
    )
    assert response.status_code == 204
    assert not AuthToken.objects.filter(
        responsavel=usuario,
        type__in=[TokenType.TOKEN, TokenType.PRE_AUTH, TokenType.RESET_PASSWORD],
        revoked_at__isnull=True,
    ).exists()
```

Cobrir reautenticação obrigatória, confirmação divergente, HIBP, preservação de
fatores/recovery e aviso de segurança.

- [ ] **Step 2: Confirmar RED**

Run: `uv run pytest apps/api/autenticacao/tests/test_password_change.py -q`

Expected: FAIL com 404.

- [ ] **Step 3: Implementar endpoint**

Aplicar `@require_recent_auth()`. Em transação, chamar
`set_validated_password()`, revogar sessões/efêmeros/trusted devices/desafios e
enfileirar aviso por `transaction.on_commit()`. Retornar 204 e não preservar a
sessão atual.

- [ ] **Step 4: Verificar e commit**

Run: `uv run pytest apps/api/autenticacao/tests/test_password_change.py apps/api/autenticacao/tests/test_password_reset.py -q`

Expected: PASS.

```bash
git add apps/api/autenticacao/passwords.py apps/api/autenticacao/tasks.py apps/api/autenticacao/serializers.py apps/api/autenticacao/views.py apps/api/autenticacao/urls.py apps/api/autenticacao/tests
git commit -m "feat(auth): add authenticated password change"
```

### Task 5: Documentar e verificar senha

**Files:**
- Modify: `docs/explanation/autenticacao.md`
- Modify: `docs/reference/api.md`
- Modify: `docs/ROADMAP.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Documentar HIBP e contratos**

Documentar k-anonymity, padding, fail-open, exceção de superuser, respostas
anti-enumeração, TTL e revogações.

Marcar o item combinado MFA/HIBP como concluído e registrar MFA, ciclo de
senha, cleanup e Beat no changelog somente depois dos gates deste plano.

- [ ] **Step 2: Executar gates**

Run: `uv run pytest apps/usuarios/tests apps/api/autenticacao/tests/test_password_reset.py apps/api/autenticacao/tests/test_password_change.py -q`

Expected: PASS.

Run: `uv run ruff check apps/usuarios apps/api/autenticacao api/settings.py`

Expected: PASS.

Run: `uv run python manage.py spectacular --validate --file /tmp/password-api.yml`

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add docs/explanation/autenticacao.md docs/reference/api.md docs/ROADMAP.md CHANGELOG.md
git commit -m "docs(auth): publish mfa and password security"
```
