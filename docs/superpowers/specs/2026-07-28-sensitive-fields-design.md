# Campos sensíveis — Design

## Objetivo

Oferecer criptografia autenticada por campo, com rotação segura de chaves, sem
alterar a experiência normal de modelos Django. Valores sensíveis não podem
aparecer em respostas DRF ou no `django-auditlog` por acidente.

## Arquitetura

O módulo profundo é `utils.sensitive_fields`. Sua interface pública será
somente `encrypt(field)`: ele recebe um campo Django e devolve um campo com a
mesma interface de Python, mas cuja persistência é cifrada. A implementação
oculta Fernet, keyring, serialização, token inválido, descoberta de campos e
rotação.

```python
from django.db import models

from utils.sensitive_fields import encrypt


class Pessoa(Base):
    cpf = encrypt(models.CharField(max_length=14))
    anotacoes = encrypt(models.TextField())
    dados_medicos = encrypt(models.JSONField())
```

`pessoa.cpf` continua retornando e aceitando plaintext. O banco recebe tokens
Fernet em colunas de texto; por isso o wrapper mantém as regras de validação do
campo original para o Python/DRF, mas usa armazenamento de texto. A primeira
entrega suporta `CharField`, `TextField` e `JSONField`; outros tipos levantam
erro de configuração em vez de aparentarem ser seguros.

### Configuração e rotação

`SENSITIVE_FIELD_KEYS` é obrigatório fora de testes e contém chaves Fernet em
ordem, separadas por vírgula. A primeira é a chave de escrita; todas podem
decifrar. O módulo cria um `MultiFernet` somente a partir dessa configuração.

Rotação segue quatro etapas:

1. Gerar uma chave Fernet e colocá-la como primeiro valor do keyring.
2. Fazer deploy com a chave nova e as antigas.
3. Executar `rotate_sensitive_fields`, que encontra campos marcados por
   `encrypt()`, lê valores com o keyring e os cifra novamente em lotes.
4. Verificar a contagem, então remover a chave antiga em um deploy posterior.

O comando delega toda a lógica a `utils.sensitive_fields`; o adapter Django em
`apps/api/core/management/commands/rotate_sensitive_fields.py` apenas converte
opções de CLI. Ele usa `bulk_update`, portanto não dispara sinais e **não cria
entradas no auditlog por padrão**. O resultado informa modelos, campos e linhas
recriptografadas. Reexecuções são seguras: valores com a chave ativa não são
regravados.

### Segurança e limitações

- Cada token é autenticado; token corrompido gera `SensitiveFieldDecryptionError`
  sem incluir ciphertext ou chave na mensagem.
- O campo é opaco: não suporta lookup, ordenação, `unique`, índice ou constraint
  sobre seu conteúdo. Busca exata futura requer blind index/HMAC e threat model
  próprio.
- Fernet carrega o timestamp de criação e processa o valor inteiro em memória;
  não é usado para arquivos.
- A chave não entra em migrations, fixtures, logs, serialização ou mensagens de
  erro.
- O campo implementa `deconstruct()` para migrations reproduzirem exatamente
  `encrypt(models.<Campo>(...))`.

## Serialização DRF

`BaseGlobal` passa a declarar `extra_write_only_fields = []` e
`get_write_only_fields()`. Ao executar `contribute_to_class`, o campo cifrado
cria uma cópia de lista específica do modelo e acrescenta seu nome em
`model.extra_write_only_fields`, quando esse atributo existir. Assim subclasses
não compartilham estado mutável.

`BaseModelSerializer` consulta `model.get_write_only_fields()` e marca esses
campos como `write_only=True`, depois de criar os campos DRF. Isso aceita
plaintext em criação/atualização e nunca o devolve em respostas.

## Registro de auditoria

`utils.logs.register` substitui todos os usos diretos de
`auditlog.register`. Sua interface é compatível com a versão instalada:

```python
register(
    model=None,
    include_fields=None,
    exclude_fields=None,
    mapping_fields=None,
    mask_fields=None,
    mask_callable=None,
    m2m_fields=None,
    serialize_data=False,
    serialize_kwargs=None,
    serialize_auditlog_fields_only=False,
)
```

Antes de delegar, ela compõe `exclude_fields` sem mutar os argumentos do
chamador, mantendo ordem e removendo duplicatas. A lista final sempre contém:

1. `internal_fields` e `extra_internal_fields` do modelo;
2. todos os campos marcados por `encrypt()`;
3. os campos que o chamador forneceu em `exclude_fields`.

Os demais argumentos seguem inalterados para `auditlog.register`, inclusive o
uso como decorator quando `model` não é informado. Exclusão vence inclusão,
como no auditlog original. O registro de modelos existentes será migrado para
esse utilitário e a configuração global `BASE_AUDITLOG_EXCLUDE_FIELDS` será
removida se ficar redundante.

## Estrutura de arquivos

- `utils/sensitive_fields.py`: interface `encrypt`, campos cifrados, keyring,
  descoberta e rotação em lotes.
- `utils/logs.py`: wrapper compatível de `auditlog.register`.
- `apps/api/core/management/commands/rotate_sensitive_fields.py`: adapter CLI
  mínimo para o utilitário de rotação.
- `utils/tests/test_sensitive_fields.py`: criptografia, serialização,
  migrations, keyring e rotação.
- `utils/tests/test_logs.py`: composição de exclusões e encaminhamento de todos
  os parâmetros do auditlog.

## Testes de aceitação

1. `encrypt()` preserva o acesso normal, armazena token sem plaintext e suporta
   `CharField`, `TextField` e `JSONField`.
2. Um token adulterado falha com erro seguro.
3. Keyring antigo lê dados existentes; rotação os regrava com a chave ativa e é
   idempotente.
4. Campos cifrados entram automaticamente em `extra_write_only_fields` e não
   são serializados pela API.
5. `utils.logs.register` exclui campos internos e cifrados, preserva todos os
   parâmetros e não modifica coleções recebidas.
6. A rotação usa `bulk_update` e não produz entradas de auditlog.
