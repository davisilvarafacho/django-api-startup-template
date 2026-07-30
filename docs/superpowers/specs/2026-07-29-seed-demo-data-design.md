# Seed genérico de dados de demonstração

## Objetivo

Permitir que quem começa a usar a base tenha um ambiente local funcional logo
após aplicar as migrações. O comando cria somente os dados genéricos da própria
plataforma multi-tenant; cada projeto consumidor continua responsável por seus
dados de domínio.

## Interface

O comando será executado como:

```bash
python manage.py seed_demo
```

Ele aceita somente a opção `--allow-production`. Sem essa opção, a execução
com `DJANGO_ENVIRONMENT=production` falha antes de fazer qualquer gravação. A
opção existe exclusivamente para um operador que deliberadamente precise criar
esses dados em produção; ela não deve constar de scripts de deploy.

## Dados criados

O comando cria os seguintes registros caso não existam:

| Entidade | Identificador estável | Dados padrão |
| --- | --- | --- |
| Organização | slug `demo` | nome `Organização Demo` |
| Time | organização `demo` + nome | nome `Time Demo` |
| Usuário | e-mail `demo@example.com` | nome `Usuário Demo`, senha `demo123456` |
| Vínculo | organização `demo` + usuário demo | papel `PROPRIETARIO`, associado ao Time Demo |

O usuário não recebe `is_staff` nem `is_superuser`. Seu acesso demonstra o
papel de proprietário de uma organização, sem criar privilégio administrativo
global do Django.

## Fluxo e invariantes

O adapter Django fica em `apps.api.core.management.commands.seed_demo`; a
lógica de persistência é deliberadamente pequena e usa os modelos reais.
Toda execução é envolvida por uma única `transaction.atomic()`.

As buscas usam os identificadores estáveis da tabela acima. Uma primeira
execução cria o grafo inteiro. Nas execuções seguintes, registros existentes
permanecem inalterados: o comando não troca senha, nome, flags de usuário,
papel ou associações manualmente editadas. Ele só cria elos ausentes necessários
para completar o grafo demo — por exemplo, o vínculo ou a associação do vínculo
ao time caso ainda não existam.

Ao concluir, o comando informa para cada entidade se ela foi criada ou já
existia e imprime as credenciais de desenvolvimento, o slug a enviar em
`X-Organization` e o próximo passo de autenticação. A senha conhecida é aceita
somente porque o comando é bloqueado em produção por padrão.

## Erros

- Em produção sem `--allow-production`, o comando levanta `CommandError` antes
  da transação de escrita.
- Falhas de banco revertem a transação completa; não pode sobrar parte do grafo
  demo.
- O comando não cria tokens Knox: o fluxo documentado usa `POST /auth/login/`
  para exercitar a autenticação real e obter um token descartável.

## Testes

Testes de integração do comando devem provar que:

1. uma execução cria organização, time, usuário e vínculo de proprietário;
2. o vínculo pertence ao Time Demo;
3. uma segunda execução não duplica registros e preserva alterações manuais,
   incluindo a senha;
4. a execução em produção sem a opção falha sem criar registros; e
5. `--allow-production` permite a criação deliberada.

## Documentação de uso

`docs/how-to/desenvolvimento-local.md` passa a orientar, depois de `migrate`:

```bash
uv run python manage.py seed_demo
```

Ela documenta `demo@example.com` / `demo123456`, o login em `/auth/login/` e o
header `X-Organization: demo` nas requisições às rotas isoladas por tenant.

## Fora de escopo

- Dados de domínio de projetos consumidores.
- Usuários adicionais, convites, tokens pré-gerados ou dados aleatórios.
- Um registry extensível de providers de seed.
- Devcontainer, runbooks e política de depreciação: cada um será um subprojeto
  próprio do Batch 11.
