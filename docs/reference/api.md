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
`.ai/brainstorming/spec/2026-07-28-api-errors-design.md`.

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

## Senha

| Rota | Contrato |
| --- | --- |
| `POST /auth/password/reset/request/` | Anônima. Recebe `email` e responde sempre `202` com o mesmo corpo, exista ou não a conta. |
| `POST /auth/password/reset/confirm/` | Anônima. Recebe `token`, `new_password` e `new_password_confirmation`; responde `204`. |
| `POST /auth/password/change/` | `Bearer` reautenticado nos últimos cinco minutos. Recebe `new_password` e `new_password_confirmation`; responde `204`. |

As duas rotas de reset compartilham o throttle `auth_password_reset`
(5/min por padrão): cada tentativa de `request` dispara um e-mail, e o token de
`confirm` vale 30 minutos, então ambas seriam alvo fácil sem limite próprio.

O `confirm` devolve o mesmo `422` para token inexistente, expirado, revogado ou
já usado — distinguir os casos entregaria informação a quem testa tokens.
Violação da política de senha também é `422`, com o erro no campo
`new_password` (inclusive `auth.pwned_password`).

Tanto `confirm` quanto `change` revogam sessões, tokens efêmeros, resets
pendentes e dispositivos confiáveis do usuário. **API keys são preservadas** —
pertencem à integração, não à sessão humana. `change` derruba também a sessão
que fez a chamada, então o cliente precisa refazer o login.

Erros comuns desses endpoints: `auth.invalid_credentials`,
`auth.reauthentication_required`, `auth.expired_token`, `auth.revoked_token`,
`auth.api_key_suspended`, `auth.responsible_inactive`,
`auth.insufficient_scope`, `auth.scope_not_delegable`,
`organizations.tenant_mismatch`,
`organizations.membership_required`.

## MFA

| Rota | Contrato |
| --- | --- |
| `POST /auth/mfa/factors/{type}/setup/` | `Bearer` reautenticado; inicia enrollment. TOTP retorna URI e secret uma única vez. |
| `POST /auth/mfa/factors/{type}/confirm/` | Confirma o OTP; no primeiro fator retorna 10 recovery codes. |
| `DELETE /auth/mfa/factors/{type}/` | Remove o fator e revoga trusted devices. |
| `POST /auth/mfa/recovery-codes/` | Substitui o lote de códigos de recuperação. |
| `POST /auth/mfa/challenge/start/` | Recebe `PreAuth` e o tipo escolhido. |
| `POST /auth/mfa/challenge/verify/` | Recebe `PreAuth`, `type`, `code` e opcional `trust_device`; retorna sessão Bearer. |
| `GET, DELETE /auth/trusted-devices/` | Lista ou revoga dispositivos, com reautenticação recente. |
| `POST /auth/mfa/admin-reset/` | Exige permission, reautenticação e `user_id`/`reason`. |

`POST /auth/login/` retorna `200` para usuários sem MFA ou com trusted device;
para MFA retorna `202` e nunca uma sessão normal antes da verificação.
