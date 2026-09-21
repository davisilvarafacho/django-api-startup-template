# Workspaces como subtenancy de uma Organização

## Objetivo

Adicionar uma segunda fronteira de dados dentro de cada Organização. Um
Workspace representa uma unidade interna, como uma filial, e permite separar
dados financeiros, bancários, funcionais e outros registros de negócio sem
fragmentar a Organização nem o seu contrato comercial.

A Organização continua sendo o tenant principal, a unidade de faturamento e a
raiz dos vínculos humanos. O Workspace é um subtenant de dados e autorização.
Uma pessoa pode acessar vários Workspaces da mesma Organização, mas somente os
Workspaces autorizados e selecionados para visualização aparecem nas consultas.

## Escopo

Esta versão inclui:

- um app próprio `apps/workspaces/`;
- os models `Workspace` e `VinculoWorkspace`;
- `current_workspace` no `Vinculo` organizacional;
- concessão automática de acesso a Administradores e Proprietários;
- seleção persistida de Workspaces para visualização;
- isolamento de leitura e escrita por RLS;
- suporte a registros compartilhados com `workspace = NULL`;
- regras distintas para models com Workspace opcional e obrigatório;
- criação transacional de um Workspace inicial com dados padrão junto da
  Organização;
- API keys com acesso a todos os Workspaces ativos da Organização.

Ficam fora desta versão:

- escopos de Workspace específicos para API keys;
- Papéis diferentes por Workspace;
- herança de autorização entre Organizações;
- agregações ou relatórios que atravessem Organizações;
- substituição do model `Time`, que continua sendo um agrupamento de pessoas.

## Vocabulário

- **Organização**: tenant principal, contrato comercial e raiz dos vínculos.
- **Workspace**: unidade interna da Organização que separa dados de negócio.
- **Vínculo**: relação de um Usuário com uma Organização, incluindo o Papel.
- **Vínculo de Workspace**: relação de um Vínculo com um Workspace, que concede
  acesso à unidade.
- **Workspace atual**: Workspace salvo em `Vinculo.current_workspace`, usado
  como contexto operacional e destino padrão de certas gravações.
- **Workspace selecionado**: Workspace cujo Vínculo de Workspace está marcado
  para visualização automática nas consultas.
- **Registro compartilhado**: registro de negócio com `workspace = NULL`,
  visível a quem tiver acesso ativo a pelo menos um Workspace ativo da
  Organização.

## Modelo de dados

### App `apps/workspaces/`

O app terá responsabilidade própria sobre Workspaces, seus vínculos, seleção de
visualização e operações de criação, ativação e desativação. Ele será instalado
como app de negócio no registro central de `BUSINESS_APPS`.

`Workspace` e `VinculoWorkspace` são parte do control plane de tenancy. Eles
precisam ser consultados para resolver o contexto antes que uma policy de
Workspace possa ser aplicada, portanto não dependem da própria policy para
serem resolvidos. Seus endpoints filtram sempre pela Organização corrente e
aplicam autorização explícita.

### `Workspace`

Campos conceituais:

- `organizacao`: FK para `Organizacao`;
- `nome` e identificador público estável;
- `is_active` e `is_deleted`, seguindo o ciclo de vida dos models globais.

Um Workspace inativo não pode ser selecionado, usado como Workspace atual nem
receber novos registros. Os registros existentes continuam preservados, mas
ficam fora da visibilidade normal.

### `VinculoWorkspace`

Campos conceituais:

- `vinculo`: FK para o Vínculo organizacional;
- `workspace`: FK para o Workspace;
- `is_active`: concessão de acesso atualmente válida;
- `selected_for_view`: participação na seleção automática de leitura.

Haverá unicidade ativa de `(vinculo, workspace)`. Um vínculo inativo não
concede acesso, mesmo que `selected_for_view` permaneça verdadeiro. A policy
considera simultaneamente o estado do Vínculo, do Vínculo de Workspace e do
Workspace.

### `Vinculo.current_workspace`

O campo `current_workspace` será uma FK opcional de `Vinculo` para `Workspace`.
Ele substitui o conceito de `default_workspace`.

O valor só é válido quando:

- o Workspace pertence à mesma Organização do Vínculo;
- o Workspace está ativo;
- existe Vínculo de Workspace ativo para esse Vínculo.

Essa coerência será validada no serviço transacional de troca e protegida pelos
testes de concorrência. Quando o Workspace atual fica inativo ou o acesso é
removido, `current_workspace` é limpo.

## Papéis e concessão de acesso

O Papel continua pertencendo ao Vínculo organizacional. Ele não varia por
Workspace: uma pessoa é, por exemplo, Gestor em todos os Workspaces aos quais
tem acesso.

Administradores e Proprietários têm acesso obrigatório a todos os Workspaces
ativos da Organização. A regra será materializada em Vínculos de Workspace:

- na criação de um Workspace, todos os Administradores e Proprietários ativos
  recebem um vínculo;
- na promoção de um Vínculo para Administrador ou Proprietário, são criados os
  vínculos para todos os Workspaces ativos;
- enquanto o Papel for Administrador ou Proprietário, seus vínculos não podem
  ser desativados ou removidos;
- ao rebaixar a pessoa, os vínculos existentes são mantidos e passam a ser
  acessos normais, que podem ser removidos posteriormente.

Gestores, Membros e Visualizadores podem ter vínculos em vários Workspaces. A
concessão e remoção desses acessos exige autorização administrativa e ocorre em
transação com revalidação do estado da Organização e do Vínculo.

`selected_for_view` é separado de autorização. A seleção pode limitar o que a
pessoa vê nas consultas, mas nunca cria acesso a um Workspace ao qual ela não
tem Vínculo de Workspace ativo. O Workspace atual sempre precisa permanecer
selecionado.

## Criação inicial e ciclo de vida

A criação de uma Organização será uma operação transacional que:

1. cria a Organização;
2. cria o Workspace inicial com seus dados padrão;
3. cria o Vínculo do Proprietário;
4. cria o Vínculo de Workspace do Proprietário;
5. define o Workspace inicial como `current_workspace`;
6. marca o acesso como selecionado para visualização.

Se qualquer etapa falhar, a Organização não fica parcialmente criada.

Criar, ativar, desativar ou remover Workspaces terá lock da Organização e
revalidará os vínculos afetados. Ao desativar um Workspace, seus acessos deixam
de ser válidos, seleções deixam de produzir dados e `current_workspace` é
limpo para quem o utilizava.

## Contexto da requisição

O cliente não precisa enviar `X-Workspace` em cada chamada. O servidor mantém a
fonte da verdade:

1. a autenticação identifica o Usuário ou a API key;
2. o middleware resolve a Organização;
3. o middleware resolve o Vínculo organizacional;
4. para sessões humanas, carrega `current_workspace` e os vínculos de
   Workspace ativos;
5. o contexto transacional do RLS recebe o identificador do tenant, o
   identificador do Vínculo e o modo do ator;
6. as policies consultam os Vínculos de Workspace persistidos.

Se `current_workspace` estiver nulo em uma rota que exige contexto de negócio,
a API devolve um erro estável informando que a pessoa precisa escolher um
Workspace. A resposta de autenticação e o endpoint de Workspaces expõem o
estado necessário para a UI apresentar essa escolha.

O endpoint de troca de Workspace atual valida Organização, atividade e acesso,
atualiza `current_workspace` sob lock e garante que o acesso permaneça
selecionado. A troca é persistida no Vínculo, portanto vale para os próximos
logins, dispositivos e abas da mesma Organização.

## Visualização automática

A seleção de visualização é alterada por uma operação própria do servidor. A UI
envia o conjunto desejado uma vez; os processos não repetem filtros de
Workspace em cada consulta.

As regras são:

- o Workspace atual sempre pertence ao conjunto selecionado;
- um Workspace só pode ser selecionado quando o acesso está ativo e o
  Workspace está ativo;
- remover um Workspace da seleção não remove o acesso;
- novos acessos podem começar selecionados;
- Administradores e Proprietários recebem acesso a todos os Workspaces, mas a
  seleção continua sendo um estado de visualização independente;
- API keys, nesta versão, consideram todos os Workspaces ativos da Organização
  selecionados para suas consultas.

Um `GET` de um recurso de negócio não precisa de filtro de Workspace. O
queryset normal é filtrado pela policy do banco com base na seleção persistida.

## RLS

Modelos de negócio que herdam de `Base` continuarão sujeitos à policy de
Organização e receberão um FK opcional `workspace`. A policy de Workspace será
restritiva (`permissive=False`) para ser combinada com a policy de Organização
como uma conjunção:

```text
mesma organização E workspace permitido
```

Para uma sessão humana, a regra de leitura é conceitualmente:

```text
organizacao_id = tenant_id
AND (
    workspace_id IS NULL
    AND existe vínculo organizacional ativo
    com algum workspace ativo
    OR
    workspace_id pertence aos workspaces ativos,
    vinculados e selecionados do vínculo atual
)
```

O teste de registro compartilhado exige ao menos um Workspace ativo com acesso
ativo. O teste de registro específico exige que o Workspace do registro esteja
entre os Workspaces selecionados. Um registro de outra Organização não passa
pela primeira policy, mesmo que seu Workspace seja conhecido.

Para API keys, o modo inicial permite registros compartilhados e registros de
todos os Workspaces ativos da Organização. Para tarefas de sistema, haverá um
contexto explícito de atravessamento que continua preso à Organização e não
será utilizado em requests humanas.

As policies serão aplicadas tanto em `USING` quanto em `WITH CHECK`:

- `USING` impede leitura, atualização ou remoção de registros fora do escopo;
- `WITH CHECK` impede criação ou alteração apontando para Workspace sem acesso,
  inativo ou de outra Organização.

O campo `workspace` será acrescentado à `Base` como opcional. Os models que
exigem Workspace declararão essa intenção e terão um `CheckConstraint` na
tabela concreta, além da validação do serializer. Um model que redefinir
explicitamente o campo também poderá usar `null=False`. Models que aceitam
registros compartilhados manterão `NULL` como representação explícita de
escopo da Organização.

O contexto de RLS será definido somente depois da autenticação e da resolução
do Vínculo. Nenhum identificador de Workspace enviado pelo cliente será aceito
como prova de autorização.

## Regras de gravação

Para um model com Workspace opcional:

- Workspace informado: grava no Workspace informado após validar acesso,
  atividade e Organização;
- Workspace omitido: grava com `workspace = NULL`, como registro compartilhado;
- Workspace explicitamente nulo: também representa registro compartilhado.

Para um model com Workspace obrigatório:

- Workspace informado: grava no Workspace informado após as mesmas validações;
- Workspace omitido: usa `current_workspace`;
- Workspace explicitamente nulo: falha na validação;
- `current_workspace` nulo: falha exigindo a seleção de um Workspace.

O serviço ou serializer precisa distinguir campo omitido de campo enviado como
`null`. A policy `WITH CHECK` permanece como última barreira para qualquer
escrita que não passe pelo fluxo DRF.

## Contratos de API

Os nomes exatos de rotas ficam para o plano de implementação, mas a superfície
deve oferecer:

- listagem dos Workspaces ativos aos quais o Vínculo tem acesso;
- leitura do Workspace atual;
- troca do Workspace atual;
- leitura e alteração da seleção de visualização;
- criação, atualização, ativação, desativação e remoção de Workspaces;
- concessão, alteração e remoção de Vínculos de Workspace.

As respostas devem distinguir:

- nenhum Workspace atual, que exige seleção;
- Workspace atual inativo ou sem acesso, que exige nova seleção;
- Workspace solicitado inexistente ou não autorizado, sem enumerar dados de
  outro tenant;
- seleção contendo Workspace inativo ou sem acesso.

## Índices e consistência

As consultas de RLS precisam de índices que comecem pelas colunas de escopo da
Organização nas tabelas de negócio. A relação de acesso deve ter índices para:

- `(vinculo, workspace)`;
- `(vinculo, is_active, selected_for_view)`;
- `(workspace, is_active)`.

Constraints e operações transacionais devem impedir dois vínculos ativos para o
mesmo par e manter `current_workspace` compatível com a Organização. Criações e
promoções usam locks para que duas operações concorrentes não deixem um
Administrador sem os vínculos obrigatórios.

## Testes

A implementação deverá cobrir, no mínimo:

- isolamento de dois Workspaces da mesma Organização por uma role PostgreSQL
  sujeita ao RLS;
- impossibilidade de acessar registro de outra Organização;
- visibilidade de registros compartilhados com um acesso ativo;
- ausência de registro específico de Workspace não selecionado;
- seleção de vários Workspaces em uma mesma consulta;
- rejeição de Workspace inativo, alheio ou sem Vínculo ativo;
- criação e promoção vinculando todos os Administradores e Proprietários;
- bloqueio de remoção de acesso obrigatório;
- manutenção de vínculos após rebaixamento;
- limpeza de `current_workspace` após inativação ou remoção de acesso;
- criação compartilhada por omissão em models opcionais;
- uso do Workspace atual por omissão em models obrigatórios;
- rejeição de `null` explícito em models obrigatórios;
- proteção de `bulk_create`, `update` e escritas fora do serializer pela policy;
- criação transacional da Organização com seu Workspace inicial;
- API key acessando todos os Workspaces ativos.

## Migração e compatibilidade

A adoção do campo na `Base` exige migration para as tabelas concretas de
negócio. Dados existentes começarão com `workspace = NULL`, preservando o
comportamento de registros compartilhados até que cada domínio seja classificado
como opcional ou obrigatório.

O rollout deve ocorrer em fases:

1. criar o app, os models e o contexto de acesso;
2. adicionar o campo opcional e as policies sem mudar a leitura dos dados
   existentes;
3. criar ou associar Workspaces iniciais para Organizações existentes;
4. ativar a seleção e o Workspace atual;
5. classificar models obrigatórios e adicionar suas constraints;
6. habilitar os endpoints de administração e os testes de RLS.

Durante a transição, cada Organização existente deve receber um Workspace
inicial e seus Administradores e Proprietários devem receber Vínculos de
Workspace antes de rotas de negócio exigirem `current_workspace`.

## Riscos e decisões conscientes

- O RLS depende de um contexto transacional validado pelo middleware; acesso
  direto ao banco pela aplicação continua pertencendo ao modelo de confiança
  existente do tenant.
- A policy de leitura envolve a tabela de Vínculos de Workspace. Ela precisa de
  índices e de testes de plano para evitar custo proporcional ao número de
  acessos por linha.
- Workspaces de controle não podem depender da policy que resolve o próprio
  contexto. Seus endpoints devem manter filtros e autorização explícitos.
- Manter vínculos após rebaixamento preserva acesso deliberadamente; a remoção
  precisa ser uma operação administrativa separada.
- A seleção de visualização não é uma concessão de acesso. Ela somente reduz o
  conjunto visível para consultas humanas, enquanto a autorização continua
  baseada em Vínculos de Workspace ativos.
