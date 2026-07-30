# Migração genérica entre storages Django

## Objetivo

Adicionar um management command capaz de migrar todos os objetos de um storage
Django para outro. O comando deve funcionar com qualquer backend compatível com
a API pública de `django.core.files.storage.Storage`, sem lógica específica para
Backblaze B2.

O Backblaze continuará sendo um destino possível por meio do
`apps.api.core.b2_storage.BackblazeB2Storage` já existente.

## Interface de linha de comando

O comando será executado como:

```bash
uv run python manage.py migrate_storage \
  --source legacy_media \
  --destination backblaze
```

`--source` e `--destination` aceitarão:

- um alias definido em `settings.STORAGES`; ou
- o caminho Python completo de uma classe de storage, instanciada sem
  argumentos.

Backends que exigem opções de construção devem ser declarados como aliases em
`STORAGES`.

O comando oferecerá as opções:

- `--source-prefix`: limita a migração a uma subárvore da origem;
- `--destination-prefix`: acrescenta um prefixo aos nomes no destino;
- `--overwrite`: substitui objetos já existentes no destino;
- `--remove-on-success`: remove da origem apenas objetos copiados e verificados
  com sucesso nesta execução;
- `--dry-run`: calcula e apresenta as ações sem abrir, gravar ou remover objetos.

Sem prefixos, os nomes serão preservados integralmente. Com
`--source-prefix`, o prefixo da origem será retirado antes da aplicação de
`--destination-prefix`. Por exemplo, `uploads/users/avatar.png`, com prefixos
`uploads` e `media`, será gravado como `media/users/avatar.png`.

## Arquitetura

A implementação terá duas unidades:

1. Um serviço de migração independente da interface de linha de comando. Ele
   resolverá e percorrerá os storages, copiará e verificará objetos e devolverá
   um resultado estruturado.
2. O management command `migrate_storage`, responsável por argumentos,
   validação da combinação de opções, progresso, resumo e código de saída.

O serviço dependerá somente das operações públicas `listdir`, `open`, `save`,
`exists`, `size` e, quando solicitado, `delete`.

Um resolvedor aceitará aliases de `STORAGES` e caminhos de classe. Quando o
valor corresponder a um alias, será usado o registro global `storages`; caso
contrário, a classe será carregada por seu caminho Python e instanciada sem
argumentos.

## Descoberta e mapeamento

O serviço percorrerá `source.listdir()` recursivamente. Diretórios e arquivos
serão tratados como nomes POSIX, independentemente do sistema operacional.

Para cada arquivo, o nome de destino será calculado removendo
`source-prefix`, quando informado, e adicionando `destination-prefix`, quando
informado. Segmentos de travessia, nomes absolutos e resultados vazios serão
rejeitados.

Origem e destino com o mesmo identificador serão recusados para impedir
autocópia acidental. O comando também documentará que aliases diferentes não
devem apontar para a mesma localização física.

## Fluxo por objeto

Para cada objeto descoberto:

1. Calcular o nome esperado no destino.
2. Consultar `destination.exists()`.
3. Se o objeto já existir e `--overwrite` não tiver sido informado, registrar
   como ignorado e manter a origem.
4. Se existir e `--overwrite` tiver sido informado, apagar o objeto no destino.
5. Fora de `--dry-run`, abrir a origem em modo binário e chamar
   `destination.save()`.
6. Confirmar que o nome retornado por `save()` é exatamente o esperado.
7. Confirmar que `source.size()` e `destination.size()` são iguais.
8. Com `--remove-on-success`, remover a origem somente após as duas
   verificações.

O arquivo de origem permanecerá intacto por padrão. Um objeto ignorado por já
existir nunca será removido da origem, mesmo com `--remove-on-success`.

`--overwrite` possui uma limitação inerente à abstração genérica: alguns
storages precisam apagar o objeto anterior antes de salvar o novo, e essa troca
não é atômica. A ajuda do comando e a documentação destacarão esse risco.

## Erros, saída e encerramento

Falhas serão isoladas por objeto. O comando registrará o nome e a causa, seguirá
com os demais objetos e exibirá ao final os totais de:

- objetos descobertos;
- objetos que seriam ou foram copiados;
- objetos ignorados;
- objetos que seriam ou foram removidos;
- objetos com erro.

Qualquer erro fará o comando terminar com status diferente de zero, depois do
resumo. Falhas de descoberta que impeçam enumerar a origem também produzirão
uma mensagem clara e status diferente de zero.

Backends sem suporte funcional a `listdir()` ou `size()` serão considerados
incompatíveis. Esses métodos são necessários, respectivamente, para enumerar
todo o conteúdo e verificar a cópia antes de uma possível remoção.

No modo `--dry-run`, nenhuma origem será aberta e nenhum método mutável
(`save()` ou `delete()`) será chamado.

## Testes

Testes do serviço e do command cobrirão:

- cópia recursiva com nomes preservados;
- remoção e adição de prefixos;
- objeto existente ignorado por padrão;
- sobrescrita explícita;
- simulação sem mutações;
- remoção da origem somente após cópia e verificação;
- manutenção da origem quando o destino já existe;
- falha em um objeto sem interromper os seguintes;
- divergência do nome retornado por `save()`;
- divergência de tamanho;
- resolução por alias e caminho de classe;
- rejeição de origem e destino iguais;
- mensagens para storages sem `listdir()` ou `size()`;
- resumo e código de saída em execuções com falhas.

Os testes usarão storages em memória e doubles pequenos para injetar falhas, sem
acessar serviços externos.

## Documentação

O README ganhará:

- exemplo de configuração de aliases de origem e destino em `STORAGES`;
- exemplo de `--dry-run`;
- exemplo de cópia;
- exemplo de sobrescrita;
- exemplo de movimentação com `--remove-on-success`;
- alertas sobre a não atomicidade de `--overwrite` e sobre aliases diferentes
  que apontem para a mesma localização.
