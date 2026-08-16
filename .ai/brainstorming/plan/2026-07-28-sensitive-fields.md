# Sensitive Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide encrypted Django fields with key rotation, safe DRF output, and audit-log registration that excludes internal and encrypted data automatically.

**Architecture:** `utils.sensitive_fields` exposes `encrypt(field)` and hides Fernet serialization, keyring lookup, encrypted-field discovery, and batch rotation. `utils.logs.register` is the single adapter to `auditlog.register`; it derives exclusions from model metadata and then delegates all auditlog options unchanged. The management command only parses CLI arguments and calls the rotation utility.

**Tech Stack:** Django 5.2, Django REST Framework, django-auditlog 3.3, `cryptography` (`Fernet` and `MultiFernet`), pytest-django.

## Global Constraints

- Support only `CharField`, `TextField`, and `JSONField` in this release; reject other field types at model import time.
- `SENSITIVE_FIELD_KEYS` is a comma-separated Fernet keyring. The first key encrypts and all keys decrypt.
- Encrypted values are opaque: no lookup, ordering, index, `unique`, or database constraint may be enabled for them.
- Never include plaintext, ciphertext, or keys in exceptions, audit entries, fixtures, logs, or command output.
- Rotation must use `bulk_update`, be idempotent, and deliberately create no auditlog entries.
- Keep the public field interface to `encrypt(field)` and keep the audit wrapper signature compatible with the installed `auditlog.register`.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml`, `uv.lock` | Declare and lock `cryptography`. |
| `utils/env.py`, `.env.example` | Declare and document `SENSITIVE_FIELD_KEYS`. |
| `utils/sensitive_fields.py` | Field wrapper, keyring, encryption, discovery, and batch rotation. |
| `utils/logs.py` | Compatible auditlog registration wrapper. |
| `apps/api/base/models.py` | Expose model-level write-only metadata. |
| `apps/api/base/serializers.py` | Apply write-only metadata to DRF fields. |
| `apps/api/core/management/commands/rotate_sensitive_fields.py` | Minimal Django command adapter. |
| `apps/{usuarios,organizacoes,api/autenticacao}/models.py` | Register models via `utils.logs.register`. |
| `utils/tests/test_sensitive_fields.py` | Encryption, keyring, serializer, and rotation tests. |
| `utils/tests/test_logs.py` | Audit exclusion and forwarding tests. |

### Task 1: Declare the encryption dependency and keyring configuration

**Files:**
- Modify: `pyproject.toml: dependencies`
- Modify: `uv.lock`
- Modify: `utils/env.py: ENVS and EnviromentVar`
- Modify: `.env.example: Django settings section`
- Test: `utils/tests/test_sensitive_fields.py`

**Interfaces:**
- Produces: `get_sensitive_field_keys() -> list[bytes]` uses `SENSITIVE_FIELD_KEYS` and raises `ImproperlyConfigured` when absent outside tests.

- [ ] **Step 1: Write the failing keyring tests**

```python
from django.core.exceptions import ImproperlyConfigured
from pytest import raises

from utils.sensitive_fields import get_sensitive_field_keys


def test_keyring_remove_espacos_e_preserva_ordem(settings, monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", "new-key, old-key")
    assert get_sensitive_field_keys() == [b"new-key", b"old-key"]


def test_keyring_ausente_falha_fora_de_testes(settings, monkeypatch):
    settings.TESTING = False
    monkeypatch.delenv("SENSITIVE_FIELD_KEYS", raising=False)
    with raises(ImproperlyConfigured, match="SENSITIVE_FIELD_KEYS"):
        get_sensitive_field_keys()
```

- [ ] **Step 2: Run the tests to verify the module is absent**

Run: `uv run pytest utils/tests/test_sensitive_fields.py -q`

Expected: collection fails because `utils.sensitive_fields` does not exist.

- [ ] **Step 3: Add the dependency and configuration**

```toml
# pyproject.toml
"cryptography>=46.0.0,<47.0.0",
```

```python
# utils/env.py
"SENSITIVE_FIELD_KEYS",
```

```dotenv
# .env.example
# Chaves Fernet separadas por vírgula; a primeira cifra e todas decifram.
SENSITIVE_FIELD_KEYS=
```

Implement `get_sensitive_field_keys()` so it strips empty entries, encodes values as UTF-8 bytes, and raises `ImproperlyConfigured` when the resulting list is empty outside tests.

- [ ] **Step 4: Lock and run the focused tests**

Run: `uv lock && uv run pytest utils/tests/test_sensitive_fields.py -q`

Expected: lock resolves and both keyring tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock utils/env.py .env.example utils/tests/test_sensitive_fields.py utils/sensitive_fields.py
git commit -m "feat: configure sensitive field keyring"
```

### Task 2: Implement the encrypted field wrapper and DRF write-only metadata

**Files:**
- Create: `utils/sensitive_fields.py`
- Modify: `apps/api/base/models.py: BaseGlobal field metadata methods`
- Modify: `apps/api/base/serializers.py: BaseModelSerializer.__init__`
- Modify: `utils/tests/test_sensitive_fields.py`

**Interfaces:**
- Consumes: `get_sensitive_field_keys() -> list[bytes]`.
- Produces: `encrypt(field: models.Field) -> models.Field`, `SensitiveFieldDecryptionError`, and `is_encrypted_field(field: models.Field) -> bool`.

- [ ] **Step 1: Add failing model and serializer tests**

```python
class Paciente(BaseGlobal):
    cpf = encrypt(models.CharField(max_length=14))
    anotacoes = encrypt(models.TextField(null=True))
    dados = encrypt(models.JSONField(default=dict))

    class Meta:
        app_label = "utils"


def test_encrypt_mantem_plaintext_no_model_e_token_no_banco(db, settings):
    paciente = Paciente.objects.create(cpf="123.456.789-00", dados={"grupo": "A"})
    raw = Paciente.objects.filter(pk=paciente.pk).values_list("cpf", flat=True).get()
    assert raw != "123.456.789-00"
    assert paciente.cpf == "123.456.789-00"
    assert paciente.dados == {"grupo": "A"}


def test_encrypt_adiciona_campo_a_write_only(settings):
    assert Paciente.extra_write_only_fields == ["cpf", "anotacoes", "dados"]
    serializer = BaseModelSerializer(instance=Paciente(cpf="123.456.789-00"))
    assert serializer.fields["cpf"].write_only is True
```

Use an isolated test model/table fixture or Django schema editor so this test does not modify production migrations.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest utils/tests/test_sensitive_fields.py -q`

Expected: `encrypt` and `get_write_only_fields` are undefined.

- [ ] **Step 3: Implement the field module and serializer integration**

Implement `encrypt(field)` as a dynamic subclass of the wrapped field class with an encrypted mixin. The mixin must:

```python
class EncryptedFieldMixin:
    is_sensitive_field = True

    def get_prep_value(self, value): ...
    def from_db_value(self, value, expression, connection): ...
    def to_python(self, value): ...
    def db_type(self, connection): return "text"
    def deconstruct(self): ...
```

`CharField` and `TextField` serialize plaintext as strings; `JSONField` uses compact JSON and restores Python objects. `get_prep_value` encrypts only plaintext, while `from_db_value` recognizes Fernet tokens and decrypts them. Invalid tokens raise `SensitiveFieldDecryptionError("Não foi possível decifrar campo sensível.")`.

In `contribute_to_class`, set `field.model`, then rebind `cls.extra_write_only_fields` to a new deduplicated list when that attribute exists and append the field name. Add this to `BaseGlobal`:

```python
extra_write_only_fields = []

@classmethod
def get_write_only_fields(cls):
    return list(cls.extra_write_only_fields)
```

Extend `BaseModelSerializer.__init__` with an `additional_write_only` argument and set `self.fields[field_name].write_only = True` for names returned by `model.get_write_only_fields()`.

- [ ] **Step 4: Add security and migration regression tests**

```python
def test_token_adulterado_nao_expoe_conteudo(db):
    with raises(SensitiveFieldDecryptionError, match="Não foi possível"):
        Paciente._meta.get_field("cpf").from_db_value("invalid-token", None, None)


def test_deconstruct_reconstroi_encrypt():
    _, path, args, kwargs = Paciente._meta.get_field("cpf").deconstruct()
    assert path == "utils.sensitive_fields.encrypt"
    assert args[0].__class__ is models.CharField
    assert kwargs == {}
```

- [ ] **Step 5: Run focused tests and commit**

Run: `uv run pytest utils/tests/test_sensitive_fields.py apps/api/base/tests/test_auditlog.py -q`

Expected: PASS once PostgreSQL test settings are available; otherwise record the missing database configuration and run the DB-less test selection separately.

```bash
git add utils/sensitive_fields.py utils/tests/test_sensitive_fields.py apps/api/base/models.py apps/api/base/serializers.py
git commit -m "feat: add encrypted model fields"
```

### Task 3: Add compatible audit-log registration

**Files:**
- Create: `utils/logs.py`
- Modify: `apps/usuarios/models.py: audit registration`
- Modify: `apps/organizacoes/models.py: audit registrations`
- Modify: `apps/api/autenticacao/models.py: audit registration`
- Modify: `apps/api/base/tests/test_auditlog.py`
- Create: `utils/tests/test_logs.py`

**Interfaces:**
- Consumes: `is_encrypted_field(field) -> bool`.
- Produces: `register(model=None, include_fields=None, exclude_fields=None, mapping_fields=None, mask_fields=None, mask_callable=None, m2m_fields=None, serialize_data=False, serialize_kwargs=None, serialize_auditlog_fields_only=False)`.

- [ ] **Step 1: Write failing delegation tests**

```python
@patch("utils.logs.auditlog.register")
def test_register_exclui_campos_internos_e_cifrados_sem_mutar_argumento(register):
    supplied = ["manual"]
    register_model(Paciente, exclude_fields=supplied, serialize_data=True)

    assert supplied == ["manual"]
    assert register.call_args.kwargs["exclude_fields"] == ["interno", "cpf", "manual"]
    assert register.call_args.kwargs["serialize_data"] is True
```

```python
@patch("utils.logs.auditlog.register")
def test_register_encaminha_todos_os_parametros(register):
    callback = "utils.tests.test_logs.mask"
    register_model(
        Paciente, include_fields=["nome"], mapping_fields={"nome": "Nome"},
        mask_fields=["outro"], mask_callable=callback, m2m_fields={"tags"},
        serialize_data=True, serialize_kwargs={"fields": ["nome"]},
        serialize_auditlog_fields_only=True,
    )
    assert register.call_args.kwargs["mask_callable"] == callback
    assert register.call_args.kwargs["serialize_auditlog_fields_only"] is True
```

- [ ] **Step 2: Run the log tests to verify failure**

Run: `uv run pytest utils/tests/test_logs.py apps/api/base/tests/test_auditlog.py -q`

Expected: collection fails because `utils.logs` is absent.

- [ ] **Step 3: Implement the wrapper and migrate registrations**

```python
def register(model=None, include_fields=None, exclude_fields=None, mapping_fields=None,
             mask_fields=None, mask_callable=None, m2m_fields=None,
             serialize_data=False, serialize_kwargs=None,
             serialize_auditlog_fields_only=False):
    def register_model(cls):
        automatic = [*getattr(cls, "internal_fields", []), *getattr(cls, "extra_internal_fields", [])]
        automatic.extend(field.name for field in cls._meta.fields if is_encrypted_field(field))
        return auditlog.register(
            cls, include_fields=include_fields, exclude_fields=_unique([*automatic, *(exclude_fields or [])]),
            mapping_fields=mapping_fields, mask_fields=mask_fields, mask_callable=mask_callable,
            m2m_fields=m2m_fields, serialize_data=serialize_data,
            serialize_kwargs=serialize_kwargs,
            serialize_auditlog_fields_only=serialize_auditlog_fields_only,
        )
    return register_model if model is None else register_model(model)
```

Replace direct imports and direct calls in the three model modules. Update the auditlog test to assert internal fields through `utils.logs.register`, not `BASE_AUDITLOG_EXCLUDE_FIELDS`.

- [ ] **Step 4: Run focused tests and commit**

Run: `uv run pytest utils/tests/test_logs.py apps/api/base/tests/test_auditlog.py -q`

Expected: PASS with database configuration; DB-less forwarding tests must pass in all environments.

```bash
git add utils/logs.py utils/tests/test_logs.py apps/usuarios/models.py apps/organizacoes/models.py apps/api/autenticacao/models.py apps/api/base/tests/test_auditlog.py api/settings.py
git commit -m "feat: centralize audit log registration"
```

### Task 4: Implement and verify batch key rotation

**Files:**
- Modify: `utils/sensitive_fields.py`
- Create: `apps/api/core/management/__init__.py`
- Create: `apps/api/core/management/commands/__init__.py`
- Create: `apps/api/core/management/commands/rotate_sensitive_fields.py`
- Modify: `utils/tests/test_sensitive_fields.py`

**Interfaces:**
- Consumes: `iter_encrypted_fields() -> Iterator[tuple[type[models.Model], models.Field]]`.
- Produces: `rotate_sensitive_fields(batch_size: int) -> RotationResult` and command `manage.py rotate_sensitive_fields --batch-size 500`.

- [ ] **Step 1: Write failing rotation tests**

```python
def test_rotation_le_chave_antiga_e_regrava_com_ativa(db, settings, monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", f"{NEW_KEY},{OLD_KEY}")
    paciente = paciente_cifrado_com(OLD_KEY, cpf="123.456.789-00")

    result = rotate_sensitive_fields(batch_size=1)

    paciente.refresh_from_db()
    assert result.updated == 1
    assert paciente.cpf == "123.456.789-00"
    assert token_usa_chave(paciente_token_bruto(paciente), NEW_KEY)


def test_rotation_e_idempotente_e_usa_bulk_update(db, mocker):
    bulk_update = mocker.patch.object(Paciente.objects, "bulk_update", wraps=Paciente.objects.bulk_update)
    rotate_sensitive_fields(batch_size=10)
    rotate_sensitive_fields(batch_size=10)
    assert bulk_update.call_count == 1
```

Add an auditlog assertion before and after rotation that the number of `LogEntry` rows is unchanged.

- [ ] **Step 2: Run the rotation tests to verify failure**

Run: `uv run pytest utils/tests/test_sensitive_fields.py -q`

Expected: import fails because `rotate_sensitive_fields` is absent.

- [ ] **Step 3: Implement rotation and the command adapter**

Implement rotation with `apps.get_models()`, only fields for which `is_encrypted_field(field)` is true, primary-key pagination, `QuerySet.iterator(chunk_size=batch_size)`, and `manager.bulk_update(batch, [field.name], batch_size=batch_size)`. Detect active-key tokens before adding an object to a batch. The command must validate `batch_size > 0` and print only model/field counts.

- [ ] **Step 4: Run targeted command and tests**

Run: `uv run pytest utils/tests/test_sensitive_fields.py -q && uv run python manage.py help rotate_sensitive_fields`

Expected: all field tests pass and Django prints the command help containing `--batch-size`.

- [ ] **Step 5: Commit**

```bash
git add utils/sensitive_fields.py utils/tests/test_sensitive_fields.py apps/api/core/management
git commit -m "feat: rotate sensitive field keys"
```

### Task 5: Final verification and documentation handoff

**Files:**
- Modify: `docs/research/field-level-encryption.md` only if implementation differs from the recorded decision.
- Modify: `docs/ROADMAP.md` only to mark the sensitive-field line complete after all verification passes.

- [ ] **Step 1: Run static checks**

Run: `uv run ruff check utils apps/api/base apps/api/core apps/usuarios apps/organizacoes && uv run python manage.py makemigrations --check`

Expected: both commands exit 0.

- [ ] **Step 2: Run the full suite with PostgreSQL test configuration**

Run: `DATABASE_NAME=<configured database> uv run pytest`

Expected: all tests pass; if the database is unavailable, report the exact configuration gap and retain focused DB-less evidence.

- [ ] **Step 3: Inspect the final diff for secrets and plaintext**

Run: `git diff main...HEAD --check && rg -n 'SENSITIVE_FIELD_KEYS=.*[A-Za-z0-9_-]{20,}' . --glob '!.env' --glob '!uv.lock'`

Expected: no whitespace errors and no literal Fernet key in tracked files.

- [ ] **Step 4: Commit documentation state**

```bash
git add docs/ROADMAP.md docs/research/field-level-encryption.md
git commit -m "docs: record sensitive field implementation"
```

## Plan Self-Review

- Spec coverage: Tasks 1–2 cover the wrapper, keyring, security contract, migrations, and DRF; Task 3 covers automatic audit exclusions and compatibility; Task 4 covers silent, idempotent bulk rotation; Task 5 covers verification and roadmap state.
- Placeholder scan: no deferred design items or unspecified error-handling steps remain.
- Type consistency: `encrypt`, `is_encrypted_field`, `rotate_sensitive_fields`, and `utils.logs.register` use the same names in every task.
