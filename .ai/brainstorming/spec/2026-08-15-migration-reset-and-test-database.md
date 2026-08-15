# Reset de migrations e banco isolado de testes

## Contexto

O projeto congelou temporariamente as migrations enquanto os models evoluíam.
Por isso, os alvos de teste e a CI usam `--nomigrations`, o gate
`makemigrations --check --dry-run` está suspenso e o histórico atual contém
migrations incrementais de reconciliação que não são necessárias para uma
instalação nova.

O banco local `base` foi confirmado como descartável. Na inspeção anterior ao
reset ele não continha a tabela `django_migrations`, portanto não existe estado
de aplicação que precise ser preservado. Migrations de dependências externas
continuam sendo responsabilidade dos respectivos pacotes e não serão
reescritas.

O Django já possui uma configuração `TEST.NAME`, mas ela usa o nome legado
`test_base_permission_cache`. O novo contrato será explícito e genérico:
`base_test`, criado e destruído pelo runner de testes.

Não existe um MCP PostgreSQL configurado no Codex local. O MCP será adicionado
somente para inspeção, com acesso de leitura ao banco de desenvolvimento. As
operações destrutivas continuarão passando pelo comando versionado e suas
salvaguardas.

## Objetivos

- Consolidar o estado atual dos models em novas migrations iniciais dos apps
  próprios.
- Expor um comando repetível, observável e bloqueado por padrão para futuros
  resets deliberados.
- Manter migrations manuais cujo efeito não pode ser derivado dos models.
- Voltar a executar migrations reais em toda a suíte e na CI.
- Restaurar o gate que detecta alterações de model sem migration.
- Isolar os testes no banco efêmero `base_test`.
- Configurar um MCP PostgreSQL local, somente leitura e sem credenciais
  versionadas.
- Aplicar a restrição de leitura no próprio PostgreSQL, não apenas no servidor
  MCP.

## Fora de escopo

- Preservar ou migrar dados existentes de `base`.
- Produzir um caminho de upgrade a partir do histórico antigo.
- Alterar migrations de apps de terceiros.
- Dar permissão de escrita ao MCP.
- Manter o banco de testes entre execuções com `--reuse-db`.
- Corrigir regressões preexistentes e não relacionadas ao reset.

## Alternativas consideradas

### 1. Management command com alvo Make fino

Escolhida. Um `manage.py reset_migrations` concentra descoberta dos apps,
salvaguardas, planejamento, restauração de arquivos em caso de falha e
orquestração dos subprocessos Django. Um alvo `make reset-migrations` oferece
a entrada curta sem duplicar lógica.

Essa opção é testável e usa a conexão definida nas settings, sem depender do
nome ou do ID de um container.

### 2. Script shell acoplado ao Docker Compose

Descartada. Seria menor, mas dependeria de `docker compose exec`, do nome do
serviço e da disponibilidade do Docker. Também tornaria mais difícil testar as
proteções sem executar comandos destrutivos reais.

### 3. Squash das migrations existentes

Descartada. Squash serve a projetos que precisam manter compatibilidade com
instalações existentes. Aqui o banco e o caminho de upgrade foram
explicitamente descartados; manter operações de reconciliação apenas
transportaria complexidade legada para a nova baseline.

## Interface do comando

O comando morará no app `core`, que é o lugar definido para infraestrutura da
API e management commands globais.

Sem `--apply`, a execução é um dry-run e não altera arquivos nem banco:

```bash
uv run python manage.py reset_migrations
```

A saída lista:

- ambiente e banco selecionados;
- apps próprios descobertos;
- migrations que seriam removidas;
- migrations manuais preservadas;
- etapas Django que seriam executadas.

A execução destrutiva exige duas manifestações independentes de intenção:

```bash
uv run python manage.py reset_migrations --apply --confirm-database base
```

`--apply` habilita mutações. `--confirm-database` deve ser idêntico ao nome
resolvido em `DATABASES["default"]["NAME"]`; não haverá prompt interativo nem
confirmação parcial.

O alvo Make apenas encaminha os argumentos ao comando. Ele não repete SQL,
descoberta de arquivos ou regras de segurança.

## Salvaguardas

O comando falha antes da primeira mutação quando:

- `DJANGO_ENVIRONMENT=production`;
- o backend de `default` não é PostgreSQL;
- o nome confirmado diverge do banco configurado;
- um app ou arquivo descoberto fica fora dos diretórios esperados;
- uma migration marcada para preservação não existe;
- existem conflitos nos nomes das migrations a gerar.

O comando nunca remove diretórios recursivamente. Ele trabalha com uma lista
resolvida de arquivos `.py`, deixa cada `migrations/__init__.py` intacto e
ignora caches e artefatos que não sejam migrations versionadas.

Não haverá backup do banco, porque a decisão aprovada é descartá-lo. Antes de
alterar o schema, porém, os arquivos antigos serão copiados para um diretório
temporário. Se a geração da nova baseline falhar antes do reset do banco, o
comando restaura os arquivos originais automaticamente.

Depois que o schema for apagado, a operação deixa de ser reversível. Uma falha
posterior mantém as migrations novas no working tree, informa exatamente a
etapa interrompida e permite corrigir a causa e repetir `migrate`.

## Descoberta e tratamento das migrations

Os apps candidatos vêm da lista `BUSINESS_APPS`, fonte autoritativa dos apps
instalados do projeto. O comando resolve cada `AppConfig`, confirma que seu
caminho está sob `apps/` e considera somente o respectivo diretório
`migrations/`. Assim, `apps/api/` continua sendo tratado apenas como agrupador.

Todas as migrations históricas desses apps serão removidas, com uma exceção
declarativa:

```text
core/0001_schedule_access_log_cleanup.py
```

Essa migration agenda uma tarefa no `django-celery-beat` por `RunPython`. Seu
efeito não aparece nos models e, portanto, `makemigrations` não consegue
recriá-lo. Ela permanece como `core.0001`, dependente da migration do
`django-celery-beat`.

As migrations antigas de reconciliação de autenticação, cópia de timestamps,
renome de campos e remoção de tabelas Knox não serão preservadas. Seus estados
finais já estão expressos nos models, e não existem dados legados a transformar.

## Sequência de aplicação

1. Validar ambiente, backend, confirmação, apps e caminhos.
2. Criar uma cópia temporária das migrations que serão substituídas.
3. Remover apenas os arquivos aprovados.
4. Executar `makemigrations` em um processo Python novo para evitar um grafo de
   migrations em cache no processo que alterou os arquivos.
5. Executar `makemigrations --check --dry-run` antes de tocar no banco.
6. Fechar conexões Django e recriar somente o schema `public` do banco
   confirmado, mantendo o banco, seu owner e a configuração de conexão.
7. Executar `migrate` em outro processo Python novo. Isso aplica tanto a nova
   baseline quanto as migrations intactas das dependências externas.
8. Executar novamente `makemigrations --check --dry-run` e `showmigrations
   --plan`.
9. Remover a cópia temporária e emitir um resumo final.

## Banco de testes

O nome padrão será `base_test`, configurável por `TEST_DATABASE_NAME`. O
Makefile exportará esse default junto das demais variáveis locais, e a CI o
declarará explicitamente.

`make test` deixará de usar `--nomigrations`. O pytest-django criará
`base_test`, aplicará todo o grafo real, executará a suíte e destruirá o banco
ao final. Não será usado `--reuse-db`, pois a finalidade principal nesta fase é
detectar uma baseline incompleta ou não reproduzível.

O usuário configurado para testes precisa de permissão `CREATEDB`. O usuário
local e o serviço PostgreSQL da CI usam `postgres`, que satisfaz esse requisito.

## CI e documentação

A mudança remove `--nomigrations` de todos os alvos de teste do Makefile e do
workflow principal. O job de lint volta a executar:

```bash
uv run python manage.py makemigrations --check --dry-run
```

Também serão atualizados o template de PR, o fluxo de desenvolvimento, o
ROADMAP e relatórios que ainda descrevem o congelamento como vigente. A
documentação operacional explicará o dry-run, a confirmação destrutiva e o
fato de que o comando não é permitido em produção.

## MCP PostgreSQL

O MCP será configurado no `~/.codex/config.toml` local, não no repositório.
Assim, a string de conexão não entra no Git e a configuração continua
específica da máquina que executa o Compose.

O MCP não usará o superusuário `postgres`. Depois da nova baseline ser
aplicada, a configuração operacional criará um papel local dedicado, com senha
aleatória mantida apenas na configuração do Codex. Esse papel receberá somente:

- `CONNECT` em `base`;
- `USAGE` no schema `public`;
- `SELECT` nas tabelas existentes;
- `SELECT` por default privilege nas tabelas futuras criadas pelo owner local.

Ele não receberá `CREATE`, escrita em tabelas, uso de sequences, ownership ou
privilégios administrativos. Como recriar o schema remove os grants associados
a ele, a etapa de configuração do MCP será repetida depois de cada reset.

O servidor escolhido é o DBHub, com versão exata verificada e fixada no
momento da implementação. Ele será iniciado por STDIO, apontará para `base` em
`127.0.0.1:5432` com o papel dedicado e também operará em modo somente leitura
no servidor. Não será usado `@latest`.

O sucesso da configuração será comprovado por `codex mcp list` e pela
inicialização do servidor. Como o inventário de ferramentas da sessão atual é
imutável, o MCP só ficará chamável depois de reiniciar o cliente ou iniciar
uma nova sessão.

## Testes e validação

Os testes do comando cobrirão, sem apagar um banco real:

- dry-run sem efeitos colaterais;
- bloqueio em produção;
- bloqueio para backend não PostgreSQL;
- confirmação ausente ou incorreta;
- descoberta apenas dos apps próprios;
- preservação de `__init__.py` e da migration manual do `core`;
- restauração dos arquivos quando a geração falha;
- interrupção imediata quando um subprocesso falha;
- ordem das etapas destrutivas.

A validação integrada usará o banco local `base`, cuja destruição foi
autorizada, e comprovará:

1. migrations novas geradas e versionáveis;
2. `migrate` completo em schema vazio;
3. `makemigrations --check --dry-run` limpo;
4. `base_test` criado, migrado e removido por uma execução de pytest;
5. testes focados do comando e smoke tests dos models aprovados;
6. lint e documentação sem regressão.

A validação do MCP tentará um `SELECT` permitido e uma escrita deliberadamente
recusada, confirmando que a restrição existe no PostgreSQL mesmo fora do
guardrail do DBHub.

Uma falha preexistente da suíte completa, se permanecer fora desta mudança,
será reportada separadamente e não mascarada com `--nomigrations`.
