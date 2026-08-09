# 0006 — Importações entre módulos

- Status: Aceito
- Data: 2026-08-09

## Contexto

Apps Django compartilham models, serializers, validators, choices, services e
outros objetos. Uma hierarquia universal de camadas evitaria alguns ciclos, mas
restringiria dependências legítimas. Arquivos `shared.py` que apenas reexportam
objetos também não resolvem o problema: eles mudam o caminho, mas preservam o
mesmo grafo de imports.

O Python carrega cada módulo sequencialmente. Dois módulos não podem depender,
no topo, de símbolos um do outro antes que ambos terminem de inicializar. Alguma
aresta de um ciclo concreto precisa ser adiada ou removida.

## Decisão

Imports diretos entre quaisquer apps e tipos de módulo são permitidos. Cada
objeto deve ser importado do módulo que o declara; `shared.py` não será usado
apenas como facade de reexportação.

Não haverá uma ordem global obrigatória entre models, services, serializers,
validators, views ou outros módulos. Quando um ciclo for reproduzido, somente
uma aresta será tornada lazy, no menor escopo possível:

1. Campos relacionais Django devem referenciar models por string, como
   `models.ForeignKey("organizacoes.Organizacao", ...)`.
2. Imports usados somente para type hints devem ficar sob `TYPE_CHECKING`, com
   annotations adiadas.
3. Dependências executadas em runtime devem usar import local dentro do método,
   propriedade ou função que as utiliza.
4. Contratos só devem ser extraídos para um módulo independente quando forem
   abstrações compartilhadas reais, não para esconder um ciclo.

`apps.get_model()`, facades lazy, registries de imports e import hooks globais
não fazem parte do padrão inicial.

## Alternativas rejeitadas

**Hierarquia global obrigatória.** Evitaria parte dos ciclos, mas impediria
colaborações legítimas entre tipos de módulo e imporia refatorações sem benefício
quando nenhum ciclo existe.

**`shared.py` como facade universal.** Reexportar uma classe importa o módulo que
a declara e, portanto, mantém a dependência circular.

**Registry ou import hook lazy global.** Permitiria mais imports bidirecionais no
topo, mas adicionaria magia, indireção e uma superfície de manutenção incompatível
com o problema atual.

## Consequências

- Módulos continuam livres para importar dependências legítimas diretamente.
- Somente ciclos concretos pagam o custo de um import lazy.
- Imports locais são uma exceção arquitetural deliberada e devem ficar no menor
  escopo que usa a dependência.
- Relações Django ficam desacopladas da ordem de carregamento dos models.
- A existência de um `shared.py` precisa ser justificada por abstrações próprias,
  nunca somente por conveniência de reexportação.
