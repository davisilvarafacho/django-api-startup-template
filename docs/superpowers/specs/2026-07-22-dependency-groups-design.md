# Separação de grupos de dependências — Design

## Objetivo

Separar dependências de desenvolvimento e de teste do runtime sem remover
pacotes existentes nem criar um grupo de produção.

## Abordagem aprovada

- Manter todas as dependências atualmente usadas pelo runtime em
  `project.dependencies`, incluindo os componentes exclusivos do ambiente de
  produção.
- Criar o grupo `test` para `pytest`, `pytest-django`, `pytest-cov`,
  `factory-boy` e `Faker`.
- Manter o grupo `dev` para ferramentas de qualidade, documentação e os
  componentes Django carregados apenas no ambiente de desenvolvimento. O grupo
  `dev` inclui o grupo `test`, portanto `uv sync` continua preparando um
  ambiente local completo.
- Atualizar `make test` para solicitar `--group test`, tornando a dependência
  de testes explícita e independente do grupo padrão.
- Atualizar `uv.lock` com o resolvedor do uv, preservando as alterações locais
  pré-existentes para Guardian, RLS, PostHog e Rules.

## Limites

- Não remover dependências sem uso aparente.
- Não criar nem mover dependências para um grupo `prod`.
- Não alterar o Dockerfile: `uv sync --no-dev` continuará excluindo dev e, por
  consequência, testes.
- `django-hijack` permanece no runtime nesta mudança, pois é importado
  incondicionalmente por `apps/usuarios/admin.py`.

## Verificação

Validar o TOML e o lockfile com `uv lock --check`; confirmar a seleção dos
grupos com `uv sync --locked --group test`; e rodar a suíte por `make test`.
