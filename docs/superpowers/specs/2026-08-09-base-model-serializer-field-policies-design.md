# Políticas de campos do `BaseModelSerializer`

- Status: aprovado em conversa
- Data: 2026-08-09

## Objetivo

Decompor as responsabilidades hoje concentradas no `__init__` do
`BaseModelSerializer` em mixins pequenos e proteger automaticamente os campos
críticos declarados pelo model como proibidos para escrita. A proteção deve ser
silenciosa e absoluta na fronteira do serializer: esses valores nunca entram em
`validated_data`, nem mesmo quando fornecidos programaticamente a `save()`.

## Política do model

`FieldPolicyMixin` continua sendo a fonte única das políticas. A lista padrão
passa a proteger os quatro campos controlados pela aplicação:

```python
forbidden_internal_write_fields = [
    "created_at",
    "created_by",
    "last_modified_at",
    "organizacao",
]
extra_forbidden_internal_write_fields = []
```

`get_forbidden_internal_write_fields()` continua combinando a lista padrão com
`extra_forbidden_internal_write_fields`. Assim, modelos concretos podem ampliar
a proteção, mas serializers não podem reduzi-la ou ignorá-la.

## Composição do serializer

Os comportamentos ficarão no mesmo módulo `apps/api/base/serializers.py`:

| Mixin | Ponto de extensão | Responsabilidade |
| --- | --- | --- |
| `InternalFieldsSerializerMixin` | `__init__` | Remove da representação os campos internos do model. |
| `ReadOnlyFieldsSerializerMixin` | `__init__` | Marca como read-only os campos definidos pelo model. |
| `WriteOnlyFieldsSerializerMixin` | `__init__` | Marca como write-only os campos definidos pelo model. |
| `ForbiddenInternalWriteFieldsSerializerMixin` | `to_internal_value` e `save` | Elimina escritas proibidas vindas do payload ou de kwargs de `save()`. |

Cada mixin de configuração possui somente um método. O mixin de forbidden usa
dois porque protege duas entradas independentes do DRF. Funções privadas de
módulo concentram a descoberta dos nomes e a cópia filtrada dos dados, evitando
duplicação entre os dois pontos de entrada.

```python
class BaseModelSerializer(
    ForbiddenInternalWriteFieldsSerializerMixin,
    InternalFieldsSerializerMixin,
    ReadOnlyFieldsSerializerMixin,
    WriteOnlyFieldsSerializerMixin,
    serializers.ModelSerializer,
):
    pass
```

Os argumentos existentes permanecem compatíveis:

- `ignore_internal` e `additional_internal`;
- `ignore_read_only` e `additional_read_only`;
- `additional_write_only`.

Não existirá `ignore_forbidden_internal_write`, `additional_forbidden` ou outro
bypass no serializer. Campos extras proibidos pertencem exclusivamente à
política do model.

## Fluxo da trava

Antes da validação, o serializer obtém a união retornada por
`get_forbidden_internal_write_fields()`. Ele considera proibidos:

- o nome real do campo do model presente no payload;
- o nome de qualquer campo do serializer cujo `source` aponte para um campo
  proibido.

Quando encontra uma chave protegida, cria uma cópia dos dados, remove a chave e
delega a validação ao DRF. O objeto original recebido pelo serializer não é
mutado. Em serializers com `many=True`, cada child serializer executa a mesma
proteção; serializers aninhados que herdam da base protegem a política de seus
próprios models.

Na chamada a `save(**kwargs)`, os kwargs também são copiados e filtrados antes
de chegar ao DRF. Isso impede que código consumidor use o serializer como bypass.
O preenchimento legítimo de tenant, timestamps e autoria permanece nos defaults,
contextos e hooks do model.

## Compatibilidade

- Payloads que hoje enviam campos proibidos continuam sem erro, mas os valores
  passam a ser descartados de forma garantida.
- A representação de saída não muda: forbidden controla escrita, enquanto
  internal/read-only/write-only continuam controlando exposição e edição.
- `BaseModelSerpySerializer` fica fora deste refactor.
- Nenhum serializer concreto precisa declarar a trava manualmente; herdar de
  `BaseModelSerializer` é suficiente.

## Testes

Os testes devem cobrir:

- a composição e responsabilidade de cada mixin;
- preservação de internal/read-only/write-only e dos argumentos adicionais;
- descarte silencioso em create, update e partial update;
- descarte de aliases declarados com `source`;
- descarte de kwargs fornecidos a `save()`;
- inclusão automática de `extra_forbidden_internal_write_fields`;
- ausência de mutação do payload original;
- inclusão de `created_at` na política padrão do model.
