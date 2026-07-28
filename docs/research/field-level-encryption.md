# Criptografia por campo no Django

## Resultado

O projeto não possui criptografia por campo implementada. A recomendação é criar
uma abstração interna pequena, inicialmente `EncryptedTextField` e
`EncryptedJSONField`, baseada em `cryptography.fernet.Fernet` e `MultiFernet`.

As bibliotecas prontas avaliadas não são uma base segura para este projeto:

- [`django-encrypted-model-fields`](https://pypi.org/project/django-encrypted-model-fields/)
  teve o último release em 2022 e declara compatibilidade somente até Django 4.0.
- [`django-cryptography`](https://github.com/georgemarshall/django-cryptography)
  declara suporte somente até Django 4.2 e Python 3.11.

O [`cryptography`](https://cryptography.io/en/latest/fernet/) é mantido e
`MultiFernet` oferece a estratégia necessária para introduzir uma chave nova,
continuar lendo dados antigos e recriptografar valores com `rotate()`.

## Contrato inicial

- Valores cifrados são opacos: não oferecer `lookup`, ordenação, `unique`,
  índice ou constraint sobre o valor cifrado.
- Não usar para arquivos; Fernet cifra valores inteiramente em memória e expõe
  o timestamp de criação do token.
- Para busca exata futura, desenhar um blind index por HMAC após threat modeling.
- As chaves vêm apenas do ambiente/secret manager; nunca de migrations.
- A rotação usa chave nova como primária, lista temporária de chaves para leitura,
  recriptografia em lotes e remoção da chave antiga somente após verificação.

## Django e auditoria

O campo próprio deverá implementar e testar `deconstruct()`, pois o Django usa
essa representação nas migrations. Consulte a documentação de
[custom model fields](https://docs.djangoproject.com/en/5.2/howto/custom-model-fields/)
e de [migration operations](https://docs.djangoproject.com/en/5.2/ref/migration-operations/).

Campos protegidos devem ser excluídos ou mascarados no `django-auditlog`, para
que plaintext não seja gravado em mudanças ou dados serializados. A biblioteca
documenta `exclude_fields` e `mask_fields` em sua
[documentação de uso](https://django-auditlog.readthedocs.io/en/latest/usage.html).

## Testes mínimos

- Round-trip, `NULL` e token adulterado.
- Ciphertexts diferentes para o mesmo plaintext.
- Leitura com chave antiga e rotação com `MultiFernet`.
- Ausência de plaintext no banco e no audit log.
