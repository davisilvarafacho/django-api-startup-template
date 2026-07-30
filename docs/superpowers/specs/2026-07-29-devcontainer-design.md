# Devcontainer para desenvolvimento local completo

## Objetivo

Abrir a base em um ambiente de desenvolvimento reproduzível, com editor e
debugger conectados à aplicação e com PostgreSQL, Redis e um worker Celery já
disponíveis. O ambiente atende o ciclo local da API; não substitui a imagem ou o
Compose de produção.

## Arquitetura

Os arquivos ficam em `.devcontainer/` e usam Docker Compose próprio de
desenvolvimento. A configuração do Dev Containers conecta o editor ao serviço
`app` e declara o repositório como workspace.

| Serviço | Responsabilidade |
| --- | --- |
| `app` | Ambiente interativo de desenvolvimento, terminal, debugger e `runserver`. |
| `db` | PostgreSQL 16 com volume nomeado e health check. |
| `redis` | Redis 7 com volume nomeado e health check. |
| `worker` | Mesmo ambiente Python de desenvolvimento, executando `celery -A api worker -l info`. |

O `app` e o `worker` usam uma imagem específica de desenvolvimento, separada do
Dockerfile de produção. Ela contém Python 3.12, `uv`, Node, compiladores e os
headers PostgreSQL necessários para instalar os grupos de dependências de
desenvolvimento. O `app` permanece vivo para o editor; o servidor Django é
iniciado explicitamente pelo desenvolvedor.

## Configuração e segurança

O Compose do devcontainer injeta somente valores locais, versionados e sem
segredos reais: `DJANGO_ENVIRONMENT=development`, uma chave Django de
desenvolvimento e os hosts `db` e `redis` com credenciais locais. Ele não lê
`.env`, não expõe variáveis do host e não reutiliza a configuração do Compose de
produção.

PostgreSQL e Redis ficam acessíveis apenas pela rede interna do Compose. A porta
8000 do `app` é encaminhada pelo Dev Containers para acesso à API e ao debugger.
Volumes nomeados preservam os dados locais entre reaberturas do container.

O `worker` depende dos health checks de banco e Redis. O Celery Beat não faz
parte desta primeira versão; fluxos agendados podem ser iniciados manualmente
quando necessários.

## Ciclo de uso

Ao criar o container, o `postCreateCommand` executa `uv sync` e `npm ci`. Ele
não executa migrações nem cria dados demo, pois ambas são gravações de banco que
devem permanecer explícitas e visíveis ao desenvolvedor.

Depois de abrir o ambiente, o tutorial orienta esta sequência:

```bash
make migrate
uv run python manage.py seed_demo
make run
```

O worker já acompanha a fila em um serviço separado. O `seed_demo` usa os
defaults de desenvolvimento definidos na sua própria especificação.

## Verificação

A implementação deve verificar que a configuração é reconhecida pelo Dev
Containers, que os quatro serviços sobem, que o worker conecta ao Redis e que a
aplicação consegue executar `make migrate` e `seed_demo` a partir do container.
Também deve confirmar que `.env` do host não é necessário nem carregado.

## Fora de escopo

- Dockerfile e Compose de produção.
- Celery Beat, stack de observabilidade e ferramentas de infraestrutura remota.
- Execução automática de migrações, seeds ou servidor Django.
