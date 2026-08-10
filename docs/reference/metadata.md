# Metadata genérico

Todo recurso servido por um `BaseModelViewSet` aceita um documento chave/valor
anexado pelo consumidor da API. Serve para uma integração guardar dados próprios
— o id do registro no ERP, um rótulo de campanha, o estado de uma sincronização
— sem exigir migration no projeto derivado.

O documento é **por organização**: duas organizações anotando o mesmo objeto
mantêm documentos independentes.

## Contrato

```http
GET /pedidos/42/metadata/
```

```json
{"dados": {"erp_id": "X-1", "nota": "urgente"}}
```

`PATCH` funde as chaves enviadas com as existentes. Valor `null` remove a chave.

```http
PATCH /pedidos/42/metadata/
{"dados": {"nota": null, "canal": "web"}}
```

```json
{"dados": {"erp_id": "X-1", "canal": "web"}}
```

Não existe `DELETE`: remover é enviar a chave com `null`.

Valores são **texto**. Números, booleanos, listas e objetos aninhados são
recusados com 422 — serialize do lado do cliente o que precisar de estrutura.

## Permissões

| Método | Permissão exigida |
|--------|-------------------|
| `GET` | `<app_label>.view_<model>` |
| `PATCH` | `<app_label>.change_<model>` |

Quem pode editar o registro pode anotá-lo. Não há codename próprio de metadata.

## Limites

| Setting | Variável de ambiente | Default |
|---------|----------------------|---------|
| `METADATA_MAX_KEYS` | `METADATA_MAX_KEYS` | 50 |
| `METADATA_MAX_KEY_LENGTH` | `METADATA_MAX_KEY_LENGTH` | 256 |
| `METADATA_MAX_VALUE_LENGTH` | `METADATA_MAX_VALUE_LENGTH` | 1024 |

Violar qualquer um deles devolve **422** no envelope de erros padrão. Uma
remoção abre espaço na mesma requisição: enviar `{"a": null, "b": "1"}` com o
limite de chaves estourado por `a` funciona.

## Desligando em um recurso

A action é registrada por padrão — é a única do `BaseModelViewSet` que não é
opt-in. Metadata é superfície de escrita livre para o cliente, então recursos
sensíveis devem recusá-la explicitamente:

```python
class VinculoViewSet(BaseModelViewSet):
    metadata_habilitado = False
```

A action deixa de ser coletada pelo router: a rota não existe, e a resposta é
404 — nem `OPTIONS` revela o endpoint.

## Uso a partir do Python

Leitura é uma property de qualquer instância; escrita passa pelo service:

```python
from apps.api.metadata.handlers import aplicar_metadata

pedido.raw_metadata                        # {"erp_id": "X-1"}
aplicar_metadata(pedido, {"erp_id": "X-2"})
```

Em listagem, use `prefetch_related` para não pagar uma query por objeto:

```python
Pedido.objects.prefetch_related("metadata_registros")
```

`aplicar_metadata` é o único ponto do sistema que cria linha de `Metadata`.
Como `Metadata` está sob RLS, tanto a leitura quanto a escrita exigem contexto
de organização — fora do ciclo de request, envolva em `organizacao_atual(id)`.

## Modelagem

A unicidade do documento segue os
[ADR 0007](../adr/0007-indices-e-constraints-por-organizacao.md) e
[ADR 0008](../adr/0008-unicidade-por-organizacao.md): `organizacao` como
primeiro campo da `UniqueConstraint` e índice parcial em `is_deleted=False`.

## Ciclo de vida

Exclusão de objeto é lógica, então o documento permanece e o endpoint passa a
devolver 404 junto com o alvo. Em exclusão física o documento fica órfão: não há
job de expurgo.
