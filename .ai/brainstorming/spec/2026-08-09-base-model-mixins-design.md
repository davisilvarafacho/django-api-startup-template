# Composição dos modelos base por mixins

- Status: aprovado em conversa
- Data: 2026-08-09

## Objetivo

Separar as responsabilidades hoje concentradas em `apps/api/base/models.py` em
mixins pequenos e coesos, sem espalhar a implementação por vários arquivos.
Todos os tipos continuam no mesmo módulo e os consumidores mantêm duas bases
claras: uma tenantless e uma tenant-scoped.

## Bases públicas

```python
class BaseTenantless(...):
    """Base para modelos sem RLS de tenant."""


class Base(TenantMixin, BaseTenantless):
    """Base padrão para dados isolados por organização."""
```

`BaseTenantless` substitui `BaseGlobal`. É usada pelos modelos que precisam
existir ou ser consultados antes do tenant ser determinado, como organização,
time, vínculo, convite e usuário. `Base` continua sendo o padrão para dados de
negócio e preserva o isolamento por organização com RLS.

## Mixins

Os seguintes mixins ficarão em `apps/api/base/models.py` e terão fronteiras
independentes:

| Mixin | Responsabilidade |
| --- | --- |
| `CreationTimestampMixin` | Campos `created_at` e `last_modified_at`. |
| `CreatedByMixin` | Campo e preenchimento automático de `created_by`. |
| `AuditHistoryMixin` | Campo `AuditlogHistoryField`. |
| `ActivityMixin` | Campo `is_active`. |
| `SoftDeleteMixin` | Campo `is_deleted` e exclusão lógica de instância. |
| `TenantMixin` | Campo `organizacao`, policy RLS e preenchimento pelo contexto ativo na criação. |
| `ChangeTrackingMixin` | Snapshot de valores e `UPDATE` automático apenas dos campos alterados. |
| `FieldIntrospectionMixin` | Descoberta de campos, relações e conversão inteira da PK. |
| `FieldPolicyMixin` | Políticas de campos internos, read-only, write-only e deferred. |
| `CloneMixin` | Clonagem, reset de campos de controle e hook de extensão. |
| `CapabilityMixin` | Capacidades `is_clonable`, `is_deletable`, `is_editable` e `is_viewable`. |
| `ApiScopeMixin` | Declaração de `api_scope_resource`. |
| `MetadataMixin` | Propriedades `metadata` e `raw_metadata`, com import local canônico. |

Cada método cooperativo de ciclo de vida chama `super()`. A ordem de composição
assegura que, ao criar um modelo `Base`, a organização vem do contexto RLS e a
autoria vem do usuário corrente; ao atualizar, o rastreamento calcula os campos
finais alterados antes de delegar ao Django.

## Querysets e managers

Querysets e managers não são modelos e não serão forçados a virar model mixins.
Permanecem no mesmo arquivo, com mixins de manager para políticas ortogonais:

- campos deferred;
- ocultação de soft-deletados;
- filtro de registros ativos.

Os managers públicos preservados são `objects`, `all_objects` e `ativos`.
`all_objects` inclui soft-deletados, mas continua sujeito a RLS quando o modelo
é tenant-scoped. O queryset de soft delete mantém `QuerySet.delete()` como
exclusão lógica.

## Rastreamento de alterações

Em instâncias persistidas e sem `update_fields` informado, o
`ChangeTrackingMixin` compara os valores finais carregados contra um snapshot
profundo. Somente as colunas alteradas e os campos `auto_now` necessários entram
no `UPDATE`. Um `save()` sem diferença não escreve nem atualiza timestamp.

Campos deferred não são acessados nem comparados. O snapshot é renovado depois
de cada save e refresh bem-sucedido. Chamadas com `update_fields` preservam o
contrato explícito do consumidor. `bulk_create`, `bulk_update` e
`QuerySet.update()` permanecem fora deste mecanismo.

## Tenancy

`TenantMixin` preserva a FK obrigatória `organizacao` e a policy
`TenantPolicy`. Ao inserir um objeto `Base` sem `organizacao_id`, obtém o ID do
contexto RLS ativo. Sem contexto, não escolhe organização: as restrições do
modelo e do banco rejeitam a escrita. Um valor informado pelo consumidor não é
substituído; RLS continua validando-o.

## Metadata

Este trabalho não altera `apps/api/metadata/**`. Ele assume o contrato de
`2026-08-09-importacoes-entre-modulos-design.md`: `Metadata` é declarado no
módulo canônico `apps.api.metadata.models`, herda de `Base`, e a dependência de
volta é resolvida por import local dentro de `MetadataMixin.metadata`.

## Compatibilidade e migração

- `Base` preserva nome, API e semântica tenant-scoped.
- `BaseGlobal` é removida e seus consumidores migram para `BaseTenantless`.
- Métodos e atributos públicos atuais continuam disponíveis pelas novas bases.
- Não há mudança de schema planejada além da renomeação de classes abstratas;
  as colunas herdadas permanecem as mesmas.

## Testes

- Contratos dos campos e managers para as duas bases.
- Criação automática de `organizacao` sob contexto RLS e falha sem contexto.
- Atualização seletiva, update vazio, reversão de valor, campos deferred,
  valores mutáveis e `update_fields` explícito.
- Soft delete de instância e de queryset.
- Clonagem, autoria e metadata com o import local canônico.
- Regressão de imports do Django depois que a implementação de metadata estiver
  integrada.
