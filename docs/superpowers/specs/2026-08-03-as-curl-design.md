# Design: utilitário `as_curl`

## Objetivo

Adicionar uma função reutilizável que converta uma `requests.Response` em um comando `curl` reproduzível.

## Interface

`as_curl(response, *, save_to_file=False) -> str`

O objeto recebido é uma resposta do pacote `requests`. A função usa a requisição preparada associada a ela (`response.request`) para produzir o comando e sempre retorna essa string.

## Comando gerado

O comando inclui:

- método HTTP;
- URL preparada;
- todos os cabeçalhos, inclusive credenciais;
- corpo da requisição, quando presente.

Cada argumento é escapado para shell POSIX, permitindo colar o resultado com segurança em um terminal. Cabeçalhos não serão mascarados.

## Persistência opcional

Quando `save_to_file=True`, além de retornar o comando, a função o grava na raiz do repositório em um arquivo chamado `<uuid4>.curl` e registra, no nível `INFO`, o nome ou caminho desse arquivo. Falhas de escrita não são ocultadas: a exceção de I/O é propagada.

Com o valor padrão (`False`), nenhum arquivo é criado nem há log de persistência.

## Organização e testes

O código ficará em `utils/curl.py`, com testes em `utils/tests/test_curl.py`. Os testes criam requisições reais preparadas pelo `requests` e verificam GET com cabeçalhos, POST com corpo, escaping, gravação opcional e registro de log. A raiz de saída será isolada nos testes para não criar arquivos no checkout.

## Fora de escopo

Não haverá suporte a outros clientes HTTP, mascaramento seletivo, opções de formatação ou execução do comando gerado.
