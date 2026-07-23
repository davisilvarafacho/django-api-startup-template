# Analytics de produto com PostHog

O PostHog cobre o eixo de **produto** da observabilidade: quem usou o quê, com
que frequência e em que fluxo desistiu. Erro e latência são do Sentry e do
Grafana (ver [Subir a observabilidade localmente](observabilidade-local.md)).

## Configuração

No `.env`:

```bash
POSTHOG_PROJECT_TOKEN=phc_...
POSTHOG_HOST=https://us.i.posthog.com
POSTHOG_DISABLED=False
```

Sem `POSTHOG_PROJECT_TOKEN` o SDK é desligado explicitamente no
`CoreConfig.ready()` — em teste ele também fica desligado, sempre. Isso evita que
cada request tente enfileirar evento para uma API inalcançável.

O `PosthogContextMiddleware` está no `MIDDLEWARE`, então toda request já carrega
contexto de sessão e usuário sem instrumentação manual.

## Eventos instrumentados

Todos em `apps/api/autenticacao/views.py`:

| Evento | Quando |
|---|---|
| `user_logged_in` | Autenticação concluída e token Knox emitido |
| `suspicious_login_detected` | Login marcado como suspeito por mudança de país |
| `token_revoked` | Usuário revogou um token específico |
| `all_tokens_revoked` | Usuário revogou todos os tokens exceto o atual |

## Enviar um evento novo

```python
import posthog

posthog.capture(
    distinct_id=str(usuario.pk),
    event="pedido_criado",
    properties={"valor": float(pedido.valor), "organizacao": organizacao.slug},
)
```

Use `snake_case` no nome do evento e prefira propriedades a eventos diferentes
(`pedido_criado` com `properties={"origem": "app"}`, não `pedido_criado_app`) —
segmentar por propriedade é barato, criar evento novo não.

## Feature flags

O PostHog também faz feature flags, mas nesta base ele cobre apenas rollout de
**produto**. Kill-switch e flag operacional são do `django-waffle`. O critério
está no [ADR 0003](../adr/0003-feature-flags.md).

## Dashboards gerados pelo wizard

O setup inicial criou os seguintes recursos no projeto PostHog:

- [Analytics basics — Dashboard](https://us.posthog.com/project/503222/dashboard/1889190)
- [Daily logins](https://us.posthog.com/project/503222/insights/gne2Opwy)
- [Logins by device type](https://us.posthog.com/project/503222/insights/8OBIJary)
- [Suspicious logins detected](https://us.posthog.com/project/503222/insights/lEtEjuBw)
- [Token revocations](https://us.posthog.com/project/503222/insights/4R61Oivh)
- [Unique active users](https://us.posthog.com/project/503222/insights/Z1mLRMyW)

## Pendência conhecida

O caminho de *returning visitor* ainda não chama `identify`: uma sessão que
retorna sem passar por login novo permanece com `distinct_id` anônimo. Resolver
ao mexer no fluxo de sessão.

## Conectar as fontes de dados

O projeto tem PostgreSQL, Resend e Sentry. Para ligá-los ao data warehouse do
PostHog:

```bash
npx @posthog/wizard warehouse
```
