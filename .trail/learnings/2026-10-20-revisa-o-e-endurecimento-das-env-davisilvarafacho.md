---
title: Revisão e endurecimento das env vars
author: Davisilvarafacho
date: 2026-10-20
branch: main
cost: { minutes: 12, tokens: 123364 }
touches:
  - ".env.example"
  - api/settings.py
  - internal_frameworks/sensitive_fields/keyring.py
  - utils/env.py
  - utils/tests/test_env.py
  - manage.py
---

## Decided
- Leitura de env só via utils/env.py. `get_bool_from_env` aceita apenas true/false (sem distinguir caixa); vazio vale como ausente, igual a get_int/get_list.
- Em settings.py: DJANGO_ENVIRONMENT ausente ou fora de development/production/test derruba o boot, exceto sob pytest/`manage.py test` (TESTING). DJANGO_DEBUG tem default `not IN_PRODUCTION`, e `true` em produção levanta ImproperlyConfigured.
- `EnviromentVar` (Literal) deve coincidir exatamente com os nomes lidos pelos helpers no código e estar documentado em .env.example. utils/tests/test_env.py varre o repo por AST e falha se divergir.
- Variáveis lidas só por docker-compose, Makefile ou gunicorn ficam no .env.example, não no Literal.
- IPAPI_ACCESS_KEY foi removida do .env.example: nenhum código a lê (autenticacao/utils.py chama o ipapi.co sem chave).

## Tried and ruled out
- Manter `ast.literal_eval` no parser de bool: `VAR=` vazio dava SyntaxError não tratado, `1`/`0` voltavam como int e `true` minúsculo falhava.
- Comparar listas de variáveis com `comm` sem ordenar: a saída fica enganosa. Ordene antes.

## Watch out
- O Dockerfile fixa DJANGO_ENVIRONMENT=production sem DJANGO_DEBUG. No compose, o `env_file: .env` sobrescreve o ENV da imagem.
- `.env` local e .env.example podem ser editados fora da sessão. Releia antes de editar.
- Defaults de localhost em ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS e INTERNAL_IPS ainda valem em produção, sem aviso.
- A suíte completa (`make test`) demora mais de 10 min. Rode em background.
