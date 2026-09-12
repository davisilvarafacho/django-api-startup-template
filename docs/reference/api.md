# API

A especificação OpenAPI é exposta em `/api/schema/` e a referência interativa
Scalar em `/api/docs/`.

Para validar o schema localmente:

```bash
uv run python manage.py spectacular --validate --file schema.yml --skip-checks
```

`--skip-checks` limita esse comando ao contrato OpenAPI. A validação operacional
das credenciais, das roles de banco e do rollout continua sendo feita por
`python manage.py check --deploy` no ambiente de destino.

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

Na anonimização definitiva de uma conta, a trilha não é apagada: IDs técnicos,
ação e timestamp permanecem, enquanto representações e payloads relacionados à
conta são sanitizados. Eventos em que a conta era apenas a autora preservam o
conteúdo não pessoal; no bloco `user`, o ID técnico permanece e os campos
pessoais ficam nulos.

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

## Conta

| Rota | Contrato |
| --- | --- |
| `POST /account/deactivate/` | Sessão com reautenticação recente e MFA quando habilitado. Revoga acessos, suspende vínculos e responde `204`. |
| `POST /account/deletion/` | Mesmos requisitos. Desativa imediatamente, agenda a anonimização e responde `202` com `scheduled_for`. |
| `POST /account/reactivation/` | Pública. Recebe `email` e responde sempre `202` com o mesmo corpo, exista ou não uma conta reativável. |
| `POST /account/reactivation/confirm/` | Pública. Recebe `token`; reativa dentro da carência e responde `204`. |

O único proprietário ativo de uma organização ativa recebe
`409 account.owner_transfer_required` ao tentar desativar ou excluir a conta.
Repetir um pedido de exclusão devolve
`409 account.deletion_already_scheduled`, com a data original em
`context.scheduled_for`; o prazo não é reiniciado. Tokens inválidos, expirados,
de propósito diferente ou usados fora da carência devolvem
`400 account.reactivation_invalid`.

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

## Assinatura

Todas as rotas exigem uma sessão humana e o header `X-Organization`; API keys
não recebem acesso financeiro por padrão. Alteração e cancelamento exigem
reautenticação recente e MFA quando o usuário o tiver habilitado.

| Rota | Contrato |
| --- | --- |
| `GET /assinatura/` | Proprietário ou administrador; devolve o snapshot corrente, revisão e situação de acesso com motivos e menor prazo de regularização. |
| `GET /assinatura/recursos/` | Qualquer vínculo ativo; devolve todos os recursos efetivos em tipos JSON. |
| `GET /assinatura/utilizacao-seats/` | Proprietário ou administrador; devolve contratados, consumo, reservas, disponibilidade e excessos. |
| `POST /assinatura/alteracoes/` | Proprietário; solicita plano, periodicidade ou quantidade absoluta de seats com revisão e chave de idempotência. |
| `POST /assinatura/cancelamento/` | Proprietário; encerra imediatamente contratos gratuitos, trials ou pendentes (`200`), ou agenda contratos pagos para o fim do período (`202`), sempre com revisão otimista. |
| `DELETE /assinatura/cancelamento/` | Proprietário; remove o agendamento antes da efetivação, também com revisão. |
| `POST /assinatura/propostas/{id}/aceitar/` | Proprietário; aceita uma proposta do próprio tenant sem enumerar propostas alheias. |

Conflitos de revisão ou idempotência usam
`409 billing.subscription_conflict`. Uma organização ativa ainda não
inicializada usa `503 billing.subscription_required`. Após expirar uma carência,
rotas comuns usam `403 billing.organization_restricted`; as rotas acima são
marcadas para permitir somente a consulta ou regularização por proprietário e
administrador, sem ampliar o papel do usuário.

## Faturamento

As rotas financeiras autenticadas aceitam somente sessões humanas, exigem
`X-Organization` e respeitam o mesmo isolamento RLS da assinatura. Criação de
checkout exige autenticação recente; quando MFA estiver habilitado para a
conta, o step-up também exige o segundo fator. Valores são inteiros em centavos.

| Rota | Contrato |
| --- | --- |
| `POST /assinatura/checkouts/` | Proprietário. Cria checkout recorrente para contratação (`finalidade=10`), alteração (`20`) ou proposta aceita (`30`). Recebe `chave_idempotencia` e a referência exigida pela finalidade. |
| `GET /faturamento/checkouts/` | Proprietário ou administrador. Lista até 50 checkouts por página, do mais recente para o mais antigo. |
| `GET /faturamento/faturas/` | Proprietário ou administrador. Lista até 50 estados normalizados de fatura por página, sem expor payload do gateway. |
| `POST /faturamento/forma-pagamento/checkouts/` | Proprietário ou administrador com autenticação recente. Abre um setup hospedado, sem item ou cobrança, usando `chave_idempotencia`. |
| `POST /faturamento/webhooks/{variante}/` | Pública e sem autenticação de sessão. Autentica os bytes originais pela assinatura do gateway; para Stripe, a variante é `stripe`. |

Uma chave idempotente repetida com o mesmo snapshot devolve a operação vigente;
se for reutilizada para outro conteúdo, a API responde
`409 billing.checkout_conflict`. Enquanto já existir checkout da mesma operação
em criação ou aberto, responde `409 billing.checkout_pending`. Ausência de
capability ou referência de preço obrigatória usa
`422 billing.checkout_unavailable`. Uma falha externa inconclusiva usa
`503 billing.checkout_uncertain`: o cliente não deve criar outra chave nem
assumir falha; a reconciliação decide o estado remoto.

O webhook responde `200` tanto para um evento novo quanto para uma duplicata
idempotente (`received=true`, `duplicate=false|true`). Assinatura, variante ou
protocolo inválido respondem `400`; reutilizar o mesmo identificador remoto com
conteúdo autenticado diferente responde `409 billing.webhook_collision`. O
schema deliberadamente não descreve nem persiste o corpo bruto do provedor.

As duas listagens financeiras aceitam `size` inteiro positivo para reduzir a
página, mas sempre limitam a resposta a 50 registros. `size=all` não é
suportado e nunca desativa a paginação; valores inválidos usam a página padrão
de 50 registros.

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
