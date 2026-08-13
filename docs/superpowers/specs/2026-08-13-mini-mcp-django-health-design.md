# Design: mini servidor MCP para saúde do Django

## Objetivo

Adicionar ao template um servidor MCP local e pequeno, executado por `stdio`, que inicialize o Django e exponha somente a ferramenta `health`. A ferramenta informa a disponibilidade de todos os bancos de dados e caches configurados no projeto.

## Dependência e transporte

O servidor usará o SDK Python oficial do Model Context Protocol, na linha estável `mcp>=2,<3`, por meio da interface `FastMCP`. Somente o pacote base será instalado; o extra de CLI não é necessário para executar o servidor.

O único transporte será `stdio`. O cliente MCP iniciará o processo com:

```bash
uv run python -m mcp_server
```

O protocolo ocupará `stdin` e `stdout`. O servidor não imprimirá mensagens comuns em `stdout`; eventuais logs serão direcionados a `stderr` pelo mecanismo de logging.

## Organização e inicialização

O código ficará no pacote de topo `mcp_server/`, separado das aplicações de domínio porque é um adaptador operacional do projeto. O ponto de entrada definirá `DJANGO_SETTINGS_MODULE=api.settings` somente quando a variável ainda não estiver definida, executará `django.setup()` e iniciará o FastMCP com transporte `stdio`.

Se a inicialização do Django falhar, o servidor não poderá atender o protocolo e o processo terminará com erro. Configurações de ambiente fornecidas pelo cliente serão respeitadas.

## Ferramenta `health`

`health` não receberá argumentos e sempre tentará executar todas as verificações, mesmo quando uma delas falhar.

Para cada alias presente em `settings.DATABASES`, a ferramenta obterá a conexão correspondente pelo gerenciador do Django e executará `SELECT 1` usando um cursor. Isso inclui, na configuração atual, os aliases `default` e `logging`.

Para cada alias presente em `settings.CACHES`, a ferramenta criará uma chave exclusiva, gravará um valor com expiração curta, lerá o valor para confirmar o ciclo completo e apagará a chave em uma etapa de limpeza executada mesmo após falha. Isso inclui, na configuração atual, `default`, `cachalot` e `permissions`. Uma leitura diferente do valor gravado será representada como `CacheRoundTripError`, ainda que o backend não lance uma exceção. Falhas na limpeza também tornarão o alias `unhealthy`.

O resultado terá formato estável e serializável como JSON:

```json
{
  "status": "healthy",
  "checks": {
    "databases": {
      "default": {"status": "healthy"},
      "logging": {"status": "healthy"}
    },
    "caches": {
      "default": {"status": "healthy"},
      "cachalot": {"status": "healthy"},
      "permissions": {"status": "healthy"}
    }
  }
}
```

O estado geral será `healthy` apenas quando todas as verificações forem saudáveis. Uma falha produzirá `status: "unhealthy"` no item afetado e no resultado geral. O item incluirá `error_type` com o nome da classe da exceção, mas não incluirá a mensagem original, URLs, hosts, credenciais nem traces. Falhas serão representadas no resultado estruturado em vez de interromper as verificações restantes.

## Testes e documentação

Os testes seguirão TDD e cobrirão:

- resposta saudável contendo todos os aliases configurados;
- falha isolada de banco sem impedir as outras verificações;
- falha isolada de cache, incluindo tentativa de limpeza, sem impedir as outras verificações;
- propagação do estado parcial para o estado geral;
- registro e chamada da ferramenta `health` por uma sessão MCP em memória.

O README mostrará como executar o módulo e um exemplo genérico de configuração de cliente MCP com `command`, `args` e diretório de trabalho. A verificação final incluirá os testes focados, a suíte relevante, Ruff e a validação estrita da documentação.

## Fora de escopo

Não haverá transporte HTTP, autenticação, resources, prompts, outras ferramentas, consultas a models de domínio, migrações, endpoint REST de saúde ou verificação de Celery. A ferramenta também não modificará dados persistentes além das chaves efêmeras necessárias para testar os caches.
