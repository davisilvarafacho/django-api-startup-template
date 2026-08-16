# Política de importações entre módulos

- Status: aprovado em conversa
- Data: 2026-08-09

## Contexto

Apps Django precisam compartilhar models, serializers, validators, choices,
services e outros objetos. Impor uma hierarquia global rígida reduziria ciclos,
mas restringiria demais a evolução dos apps. Por outro lado, arquivos
`shared.py` que apenas reexportam objetos não alteram o grafo de dependências e
não impedem importações circulares.

O caso que motivou esta decisão é a integração entre `Base`, em
`apps.api.base.models`, e `Metadata`, agora em `apps.api.metadata.models`:
`Metadata` herda de `Base`, enquanto `Base` oferece uma propriedade que consulta
`Metadata`.

## Objetivos

- Permitir imports diretos entre quaisquer apps e módulos por padrão.
- Fazer somente os ciclos concretos pagarem o custo de uma resolução lazy.
- Manter imports legíveis, localizados e apoiados por recursos nativos de Python
  e Django.
- Evitar facades, registries e import hooks globais sem necessidade real.

## Não objetivos

- Garantir imports eager bidirecionais no topo de dois módulos. O carregamento
  sequencial de módulos do Python torna essa garantia impossível sem indireção.
- Impor uma ordem universal entre models, services, serializers, validators,
  views ou outros tipos de módulo.
- Refatorar preventivamente imports que não formam ciclos.
- Corrigir neste trabalho os demais aspectos funcionais do framework de metadata.

## Decisão

Cada objeto deve ser importado diretamente do módulo que o declara. Um arquivo
`shared.py` não deve existir apenas para reexportar objetos de outros módulos.

Imports entre apps e entre tipos de módulo são livres. Quando um ciclo for
efetivamente reproduzido, uma única aresta do ciclo será tornada lazy, no menor
escopo possível, conforme esta ordem de preferência:

1. Campos relacionais do Django devem referenciar models por string, por exemplo
   `models.ForeignKey("organizacoes.Organizacao", ...)`. Isso adia a resolução
   para o app registry sem criar código auxiliar.
2. Imports usados somente para type hints devem ficar sob `TYPE_CHECKING`, com
   annotations adiadas.
3. Dependências executadas em runtime devem usar import local dentro do método,
   propriedade ou função que as utiliza.
4. Um contrato deve ser extraído para um módulo independente somente quando
   representar uma abstração compartilhada real, e não apenas para esconder um
   ciclo.

`apps.get_model()`, facades lazy, registries de imports e import hooks não fazem
parte do padrão inicial. Podem ser avaliados futuramente se imports locais se
tornarem numerosos ou difíceis de rastrear.

## Aplicação ao metadata

- `apps.api.metadata.models` importará `Base` diretamente de
  `apps.api.base.models`.
- `apps.api.base.models` não importará `Metadata` no topo. A propriedade
  `BaseGlobal.metadata` fará o import direto de `Metadata` localmente.
- `apps/api/base/shared.py` e `apps/api/metadata/shared.py`, criados apenas como
  facades de reexportação, serão removidos.
- O `AppConfig` e `BUSINESS_APPS` usarão o caminho canônico
  `apps.api.metadata`, refletindo a nova localização do app.

## Documentação normativa

A decisão será registrada em um novo ADR. `AGENTS.md` e `CLAUDE.md` receberão
uma regra curta com:

- liberdade de imports diretos;
- proibição de `shared.py` usado apenas como reexport;
- import local como solução padrão para dependência circular concreta;
- referências textuais obrigatórias para models em campos relacionais Django.

## Testes e validação

- Um teste de regressão deve demonstrar que o setup do Django carrega os models
  sem importação circular.
- O teste de contrato da constraint de metadata deve continuar passando após a
  mudança de caminho.
- `manage.py check`, Ruff e o build estrito do MkDocs devem ser executados.
- Testes que exigem banco serão executados somente com PostgreSQL e Redis
  disponíveis, conforme as regras do repositório.

## Consequências

- Módulos continuam livres para colaborar sem uma hierarquia artificial.
- Ciclos ficam explícitos no ponto exato em que são quebrados.
- Alguns métodos poderão conter imports locais, uma exceção deliberada à
  preferência usual por imports no topo.
- Desenvolvedores precisam distinguir uma abstração compartilhada real de uma
  facade que apenas muda o caminho do mesmo ciclo.
