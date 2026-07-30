# Configuração local do banco de testes

## Objetivo

Permitir que `make test` execute a suíte Django contra o PostgreSQL local
iniciado pelo `docker compose`, sem exigir que o desenvolvedor exporte as
variáveis `DATABASE_*` manualmente.

## Contexto e causa

O container `drf-base-api-db-1` está saudável e publicado em
`127.0.0.1:5432`. O Compose define como padrão o banco `base` e o usuário e
senha `postgres`. A suíte é chamada diretamente por `pytest`; portanto, ela
não passa por `manage.py`, que é o único ponto que carrega `.env`. Sem
`DATABASE_NAME`, o Django não consegue derivar o nome do banco de teste e a
coleta falha antes de executar os testes que usam banco.

## Alternativas consideradas

1. Alterar `api/settings.py` para assumir valores locais: descartada, pois
   valores de desenvolvimento não devem ser defaults globais das settings.
2. Exigir o carregamento manual de `.env` antes de cada teste: descartada,
   pois mantém o comando documentado (`make test`) quebrado no ambiente local
   padrão.
3. Declarar no `Makefile` defaults iguais aos do Compose, preservando valores
   explicitamente fornecidos: escolhida. A mudança fica restrita ao comando de
   desenvolvimento e permite apontar para outro banco por `DATABASE_*`.

## Design aprovado

O `Makefile` declarará `DATABASE_NAME`, `DATABASE_USER`,
`DATABASE_PASSWORD`, `DATABASE_HOST` e `DATABASE_PORT` com atribuição
condicional (`?=`), usando respectivamente `base`, `postgres`, `postgres`,
`127.0.0.1` e `5432`. Essas cinco variáveis serão exportadas apenas para os
subprocessos do Make.

Assim, `make test` recebe uma configuração utilizável quando o Compose local
foi iniciado com seus defaults. Uma variável de ambiente já fornecida pelo
desenvolvedor continua tendo precedência, permitindo containers com credenciais
ou porta diferentes. Nenhuma setting da aplicação, segredo versionado ou
recurso de produção será alterado.

## Validação

1. Executar `make test` sem `DATABASE_*` exportadas, com os containers locais
   `db` e `redis` saudáveis.
2. Confirmar que a suíte não falha na criação do banco de teste por
   `DATABASE_NAME=None`.
3. Usar `make -pn DATABASE_PORT=5544 | rg '^DATABASE_PORT = 5544$'` para
   confirmar que um valor explícito é preservado pelo Makefile.
