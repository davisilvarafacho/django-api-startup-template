# Proteção contra força bruta

## O que é protegido

O `django-axes` protege o `POST /auth/login/` e o login do `/admin/`. Recuperação
de senha e MFA ainda não fazem parte desta proteção.

## Política

Cinco falhas na combinação usuário + IP disparam um bloqueio de 30 minutos. Um
login correto durante a janela zera o contador. Tentativas feitas enquanto o
bloqueio está ativo não estendem o prazo.

A chave combina usuário e IP de propósito. Bloquear apenas pelo usuário
permitiria que qualquer pessoa trancasse a conta alheia; bloquear apenas pelo IP
puniria clientes que compartilham NAT. Essa escolha ainda deixa aceitas as
lacunas de credential stuffing distribuído e de varredura de vários usuários
por um único IP, descritas na [spec de design](../superpowers/specs/2026-07-30-django-axes-design.md).

## Resposta de bloqueio

O servidor responde `429` com:

```json
{"mensagem": "Muitas tentativas de login."}
```

O header `Retry-After` informa o número de segundos restantes. Clientes devem
respeitar esse header em vez de fazer retry imediato.

## Configuração

Estas variáveis controlam a política:

| Variável | Default | Função |
|---|---:|---|
| `AXES_ENABLED` | `True` (exceto em testes) | Liga ou desliga a proteção. |
| `AXES_FAILURE_LIMIT` | `5` | Falhas antes do bloqueio. |
| `AXES_COOLOFF_MINUTES` | `30` | Duração do bloqueio em minutos. |

## Desbloquear um usuário

No admin do Django, apague o `AccessAttempt` correspondente. Pela linha de
comando, use:

```bash
uv run python manage.py axes_reset_username <email>
```

## Dependência do proxy

`AXES_CLIENT_IP_CALLABLE` confia em `X-Forwarded-For` apenas quando
`DJANGO_BEHIND_PROXY` está ligado. O valor é garantido pelo nginx de borda;
consulte [Proxy reverso com nginx](proxy-nginx.md). Expor a API sem esse nginx
e manter a variável ligada torna o bloqueio por IP contornável.

## Retenção

`AccessLog` é expurgado aos 90 dias por tarefa periódica. `AccessAttempt` é
limpo automaticamente pelo próprio axes quando há cooloff configurado.
