# Autenticação por tokens tipados

O projeto autentica a API com tokens compatíveis com Knox. A resolução acontece
no `AuthenticationMiddleware`, antes dos demais middlewares que dependem de
`request.user` (auditoria, tenancy/RLS e analytics). O DRF usa apenas
`PassthroughAuthentication` para reaproveitar o usuário e o token já
resolvidos; o token não é validado duas vezes na mesma request.

Rotas públicas são declaradas pelo `PUBLIC_ROUTES` de cada app. Uma rota que não
estiver nessa lista exige um token válido mesmo que a view declare `AllowAny`.
Por isso, os prefixos públicos devem ser específicos: declarar `/auth/`, por
exemplo, também liberaria logout e demais rotas autenticadas.

## Tipos de token

`AuthToken` é o modelo configurado em `KNOX_TOKEN_MODEL` e guarda o tipo e os
escopos da credencial. `TokenMetaData` é o complemento 1:1 com dados de
dispositivo, risco e o instante de reautenticação. O segredo Knox só é retornado
na emissão; o banco persiste o seu digest irreversível e um prefixo não sensível
para busca.

| Tipo | Valor | Uso | Acesso normal à API |
| --- | ---: | --- | --- |
| `TOKEN` | 1 | Sessão autenticada de usuário. | Sim |
| `RESET_PASSWORD` | 2 | Autorização temporária para redefinir senha. | Não |
| `PRE_AUTH` | 3 | Senha primária validada, com MFA ainda pendente. | Não |
| `API_KEY` | 999 | Integração, com escopos opcionais. | Sim |

`TypedTokenAuthentication` aceita somente `TOKEN` e `API_KEY`. Tokens de reset
e pré-autenticação terão autenticadores próprios nos fluxos que os consomem; não
devem ser reutilizados como `Bearer` em endpoints comuns.

`AuthToken.EPHEMERAL_TYPES` é o conjunto imutável formado exclusivamente por
`PRE_AUTH` e `RESET_PASSWORD`. Essas credenciais sempre exigem `expiry`, tanto
pela validação do modelo quanto pela constraint do banco. A propriedade
`AuthToken.is_expired` é calculada: retorna verdadeiro somente quando existe
`expiry` e ela é menor ou igual ao horário atual. Uma expiração nula não é
considerada expirada.

## Login, sessão e reautenticação

`POST /auth/login/` recebe `email` (ou o campo de compatibilidade `username`) e
`password`. Depois de validar as credenciais, o endpoint emite uma sessão
`TOKEN` e os respectivos metadados na mesma transação. A resposta expõe o token
em texto puro apenas nessa ocasião, além de `expiry`, do identificador/tipo da
sessão e de informações sanitizadas do dispositivo. O endpoint tem o throttle
`auth_login` (10/minuto).

Use a sessão em requests protegidas com o cabeçalho Knox configurado:

```text
Authorization: Bearer <token>
```

`POST /auth/reauthenticate/` confirma novamente a senha da identidade da sessão
atual e atualiza `TokenMetaData.reauthenticated_at`. Ele aceita apenas
`TOKEN` — API keys, tokens ausentes e tokens efêmeros não satisfazem o requisito
— e responde `204 No Content` em sucesso. O endpoint tem o throttle
`auth_reauthenticate` (5/minuto).

Views que precisam de step-up declaram `@require_recent_auth(max_age=300)`.
`RecentAuthenticationPermission` então exige uma sessão `TOKEN` cujo
`reauthenticated_at` esteja dentro da idade máxima. `require_mfa` já faz parte do
marcador declarativo, reservado para a integração da segunda etapa MFA. Uma view
que substitui `permission_classes` deve incluir explicitamente essa permission,
pois deixa de herdar as permissões globais.

## Erros públicos

`APIError` define o envelope estável abaixo e mantém o `context` somente para
uso interno; ele nunca é serializado ao cliente. O handler global do DRF também
normaliza exceções DRF para `code` e `message`.

```json
{
  "code": "auth.reauthentication_required",
  "message": "Reautenticação recente obrigatória."
}
```

`AuthErrorCode` reserva códigos públicos estáveis para credenciais e
tokens inválidos/expirados/revogados, reautenticação, desafio e OTP inválidos,
cooldown, limite de tentativas, senha vazada e indisponibilidade de entrega. Os
clientes devem reagir ao `code`, não ao texto traduzível de `message`.

## Limpeza de tokens expirados

`cleanup_expired_tokens()` centraliza uma política idempotente e é a única
implementação usada tanto pela task Celery quanto pelo management command:

| Tipo | Critério de remoção |
| --- | --- |
| `PRE_AUTH` e `RESET_PASSWORD` | Imediatamente após `expiry`. |
| `TOKEN` | Depois de expirar e transcorrer a retenção de sessões. |
| `API_KEY` | Nunca é selecionada automaticamente. |
| Sem `expiry` | Nunca é selecionado automaticamente. |

As exclusões ocorrem em lotes curtos, com lock e transação por lote; metadados
dependentes são removidos por cascade. Os valores atuais de operação são
`AUTH_TOKEN_SESSION_RETENTION_DAYS = 90` e
`AUTH_TOKEN_CLEANUP_BATCH_SIZE = 500`. Eles estão em `api/settings.py`; não há
variável de ambiente correspondente, portanto `.env.example` não precisa nem
deve declarar configuração inativa. Em uma configuração de deployment, altere
os settings efetivamente carregados em vez de criar uma variável sem consumo.

Para inspecionar ou executar manualmente:

```bash
uv run python manage.py cleanup_expired_auth_tokens --dry-run
uv run python manage.py cleanup_expired_auth_tokens --batch-size 100
uv run python manage.py cleanup_expired_auth_tokens --session-retention-days 30
```

`--dry-run` mostra contagens por tipo e não remove dados. `--batch-size` precisa
ser maior que zero; `--session-retention-days` não pode ser negativo. Execute o
command primeiro em modo dry-run em operações de manutenção, acompanhe as
contagens e evite registrar ou copiar tokens puros em shell history, logs ou
chamados de suporte.

A task `autenticacao.cleanup_expired_tokens` chama o mesmo serviço, usa retry
com backoff somente para `OperationalError` e limita-se a três tentativas. Ela
registra apenas duração e contagens por tipo, nunca segredos de token. O
`CELERY_BEAT_SCHEDULE` a agenda diariamente às `00:00`; como
`CELERY_TIMEZONE = TIME_ZONE` e `TIME_ZONE = "America/Sao_Paulo"`, esse horário
é meia-noite em São Paulo. O scheduler configurado é o
`django_celery_beat.schedulers:DatabaseScheduler`; não é necessário cadastrar a
rotina manualmente no Django Admin.

## Escopos de API key

Escopos são uma lista de strings em `AuthToken.scopes` e só restringem
credenciais `API_KEY`. Uma view pode declarar `required_token_scopes` como lista
ou como mapa por método/action; `TokenScopePermission` verifica esse requisito
antes das permissões de modelo. Sessões `TOKEN` continuam dependentes das
permissions normais da aplicação.

```python
required_token_scopes = {
    "GET": ["org:read"],
    "POST": ["org:write"],
}
```

O escopo `"*"` atende qualquer requisito. Não coloque token, OTP, senha ou
informações sensíveis de dispositivo em logs, eventos analíticos, serializações
de leitura ou mensagens de erro.

## MFA opt-in

O usuário pode cadastrar fatores `totp`, `email` e `sms`; um fator de cada tipo
fica ativo somente depois de confirmado. Setup, confirmação, remoção, geração
de recovery codes e gestão de dispositivos confiáveis exigem uma sessão
`Bearer` reautenticada nos últimos cinco minutos. Secrets TOTP e telefones são
cifrados, OTPs são persistidos apenas como HMAC e recovery/trusted-device tokens
nunca são persistidos em texto puro.

Um login com fatores ativos responde `202` com `pre_auth_token` e `methods`.
Envie esse token como `Authorization: PreAuth <token>` exclusivamente para
`/auth/mfa/challenge/start/` e `/auth/mfa/challenge/verify/`; ele expira em
cinco minutos, não autoriza endpoints normais e é removido ao concluir o MFA.
Dispositivos confiáveis duram 30 dias, rodam o segredo a cada uso e podem ser
revogados em `/auth/trusted-devices/`.

Para SMS, configure `MFA_SMS_ENABLED=true` e um `MFA_SMS_BACKEND` de produção.
O deploy falha se Console/InMemory estiverem habilitados fora de DEBUG. Em perda
total de fatores, um operador com `usuarios.can_reset_mfa_usuario` usa
`POST /auth/mfa/admin-reset/`, reautenticado e com justificativa; a operação
revoga fatores, recovery codes, sessões e dispositivos confiáveis, preservando
a senha.
