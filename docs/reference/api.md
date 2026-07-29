# API

A especificação OpenAPI é exposta em `/api/schema/` e a referência interativa
Scalar em `/api/docs/`.

Para validar o schema localmente:

```bash
uv run python manage.py spectacular --validate --file schema.yml
```

## Erros

Toda resposta de erro segue o mesmo envelope, independente de ter sido produzida
pelo DRF, por um middleware ou por um handler de status HTTP do Django:

```json
{
  "errors": [
    {
      "code": "auth.invalid_credentials",
      "message": "E-mail ou senha inválidos.",
      "field": null,
      "path": null,
      "context": {}
    }
  ],
  "request_id": "01J..."
}
```

- `code` é estável, em inglês, no formato `dominio.erro`, e é a única coisa pela
  qual um cliente deve decidir seu comportamento — nunca pelo texto de `message`.
- `field`/`path` identificam o campo com erro (para validação simples ou
  aninhada); `context` traz dados estruturados e nunca segredos.
- `request_id` corresponde ao mesmo id usado em logs e no Sentry.

Todo código é declarado como `models.TextChoices` em `<app>/errors.py` e
registrado em `apps.api.core.errors.error_codes`; um system check falha o
startup se houver formato inválido ou duplicidade. Ver a implementação em
`apps/api/core/errors.py` e a spec normativa em
`docs/superpowers/specs/2026-07-28-api-errors-design.md`.

## Autenticação

Todas as URLs usam `_` (nunca `-`) em palavras compostas. Detalhes de cada
mecanismo estão em `docs/explanation/autenticacao.md`.

| Rota | Descrição |
| --- | --- |
| `POST /auth/login/` | Emite uma sessão; devolve `token` uma única vez |
| `POST /auth/reauthenticate/` | Step-up: confirma a senha da sessão atual |
| `POST /auth/logout/` | Revoga logicamente a sessão atual |
| `POST /auth/logout_all/` | Revoga logicamente todas as sessões |
| `/auth/sessions/` | CRUD read-mostly das sessões do usuário autenticado |
| `/auth/api_keys/` | CRUD e ciclo de vida (`rotate`/`suspend`/`resume`) de API keys da organização do header `X-Organization` |

Erros comuns desses endpoints: `auth.invalid_credentials`,
`auth.reauthentication_required`, `auth.expired_token`, `auth.revoked_token`,
`auth.api_key_suspended`, `auth.responsible_inactive`,
`auth.scope_not_delegable`, `organizations.tenant_mismatch`,
`organizations.membership_required`.
