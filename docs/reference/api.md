# API

A especificação OpenAPI é exposta em `/api/schema/` e a referência interativa
Scalar em `/api/docs/`.

Para validar o schema localmente:

```bash
uv run python manage.py spectacular --validate --file schema.yml
```

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
