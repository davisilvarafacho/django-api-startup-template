# 0003 — Feature flags: `django-waffle` e PostHog lado a lado

- Status: Aceito
- Data: 2026-07-23

## Contexto

A base adotou PostHog para product analytics, e o PostHog também oferece feature
flags. Ao mesmo tempo, `django-waffle` foi aprovado no roadmap. Manter os dois
sem critério levaria a flags espalhadas em dois lugares, sem ninguém saber onde
procurar durante um incidente.

## Decisão

Manter os dois, com responsabilidades separadas.

**`django-waffle` — flags operacionais.** Estado no banco, editável pelo admin,
avaliado localmente. É o que se usa para kill-switch (desligar uma integração que
está derrubando a API) e rollout interno. Não faz chamada de rede no caminho da
request, então continua funcionando com a internet degradada — requisito de
qualquer coisa que se pretenda usar durante um incidente.

`WAFFLE_FLAG_DEFAULT`, `WAFFLE_SWITCH_DEFAULT` e `WAFFLE_SAMPLE_DEFAULT` ficam em
`False`, e `WAFFLE_CREATE_MISSING_*` também: uma flag inexistente falha fechado
em vez de criar linha no banco por consulta.

**PostHog — flags de produto.** Rollout por segmento de usuário, experimento e
teste A/B, onde o valor está justamente em cruzar a flag com os eventos que o
PostHog já coleta.

O helper `apps/api/core/feature_flags.py` expõe `SwitchAtivo.para("nome")` e
`FlagAtiva.para("nome")` como permission classes do DRF. Ambas respondem **404**,
não 403: funcionalidade desligada por flag não deve revelar que existe.

## Consequências

- Durante incidente, o lugar de olhar é o admin do waffle — decisão registrada,
  não convenção implícita.
- Flags de produto não entram no banco da aplicação nem no admin.
- O custo é ter dois lugares onde uma flag pode existir; o critério acima é o que
  torna isso previsível.
