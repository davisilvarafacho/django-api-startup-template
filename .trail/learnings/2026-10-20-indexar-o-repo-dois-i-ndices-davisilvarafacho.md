---
title: "Indexar o repo: dois índices independentes (codebase-memory e trail)"
author: Davisilvarafacho
date: 2026-10-20
branch: main
cost: { minutes: 1, tokens: 44377 }
touches:
  - ".env.example"
  - api/settings.py
  - internal_frameworks/sensitive_fields/keyring.py
---

## Decided
- "Indexar o repo" tem dois alvos distintos: o grafo do `codebase-memory-mcp` (`index_repository`, mode `full`) e o mapa do `trail` (`trail build`; `trail status` mostra se está em sincronia).
- Foi rodado só o `index_repository` (full): 8.170 nós / 35.346 arestas. O mapa do trail já estava em sincronia, então não foi reconstruído.

## Tried and ruled out
- Reindexar o trail: desnecessário, `trail status` já reportava "in sync with the code".

## Watch out
- Os arquivos de `docker/nginx/` (nginx.conf, sites/*/default.conf, snippets/proxy.conf) não são interpretados pelo codebase-memory (parse inútil); leia o fonte direto.
- `Makefile` e dois snippets nginx entram com parse parcial; trechos podem faltar no grafo.
- `.env` e `.trail/statusline.cjs` ficam fora do índice por causa do `.gitignore`.
