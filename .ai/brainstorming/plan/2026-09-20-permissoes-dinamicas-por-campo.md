# Permissões dinâmicas por campo — Implementation Plan

> **Para agentes:** SKILL SUBOBRIGATÓRIA: use `superpowers:executing-plans` para executar este plano tarefa a tarefa. Os passos usam checkboxes (`- [ ]`) para acompanhamento.

**Goal:** implementar políticas de escrita por campo isoladas por organização e aplicá-las a todos os serializers graváveis de model.

**Architecture:** um app da infraestrutura API guarda políticas, concessões e gestores; um catálogo identifica campos pela dupla recurso/campo; um serviço decide em lote antes da validação normal do serializer. Locks transacionais ordenam escritas e alterações administrativas, enquanto epochs invalidam o cache somente após commit.

**Tech Stack:** Python 3.12, Django 5.2, DRF, PostgreSQL, Redis, pytest-django.

**Spec:** `.ai/brainstorming/spec/2026-08-13-field-write-permissions-design.md`.

## Global Constraints

- Campo protegido: papel mínimo OU concessão individual de vínculo ativo; API key é sempre negada.
- Campo sem política conserva o comportamento atual do endpoint.
- Somente chaves graváveis e explicitamente enviadas em `POST`, `PUT` ou `PATCH` são verificadas.
- Negação de qualquer campo impede toda a persistência e agrega as negações no envelope `errors`.
- Organização, policy, concessão e gestor não podem cruzar tenants; política órfã bloqueia o recurso.
- Escrita ORM interna está fora do contrato e exige autorização no respectivo ponto de entrada.

## Review Focus

1. Um alias simples (`source="nome"`) deve aplicar a policy de `nome` e apontar o alias no erro.
2. Um campo omitido em `PATCH` ou `PUT` não deve exigir concessão.
3. Uma key com scope de update não deve escrever em campo protegido, mesmo se seu responsável for proprietário.
4. Uma policy criada entre validação e `save()` não pode deixar a escrita passar sem ordenação transacional.
5. Uma migration que renomeia campo sem migrar a policy deve falhar fechada.

## Pré-requisito de contrato

A spec de campo usa `model.api_scope_resource`, enquanto a spec de autorização por recurso exige que `ResourcePolicy.resource` seja a declaração única após a migração. Antes de implementar este plano, atualizar a spec de campo para consumir o catálogo de `ResourcePolicy`; não manter dois nomes públicos independentes para o mesmo recurso. Executar primeiro `.ai/brainstorming/plan/2026-09-20-corrigir-autorizacao-por-recurso.md`.

## Mapa de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/api/field_access/models.py`, `migrations/` (novos) | policies, grants, gestores e estado de versão por organização |
| `apps/api/field_access/catalog.py` (novo) | model fields elegíveis pela policy do recurso |
| `apps/api/field_access/authorization.py` (novo) | decisão em lote e erros por chave pública |
| `apps/api/field_access/management.py` (novo) | mutações administrativas, locks e invalidação |
| `apps/api/field_access/views.py`, `serializers.py`, `urls.py` (novos) | API administrativa |
| `apps/api/base/serializers.py` | `FieldWriteAuthorizationMixin` antes da validação DRF |
| `apps/organizacoes/serializers.py` | migração dos serializers graváveis para a base protegida |
| `tests/architecture/` | guardrail contra novos serializers graváveis fora da base |
| `api/settings.py`, `docs/reference/`, `docs/how-to/` | registro, contrato e operação |

### Task 1: Fixar identidade do recurso e o catálogo

- [ ] Atualizar a seção “Catálogo de campos” da spec: o recurso vem de `ResourcePolicy.resource`, já registrado no `ScopeRegistry`.
- [ ] Escrever testes para incluir `Time.nome` e excluir chave primária, campo interno, `editable=False`, propriedade, relação reversa e source aninhado.
- [ ] Criar `FieldCatalog` em `apps/api/field_access/catalog.py`; usar a mesma policy de recurso para resolver model e nome público.
- [ ] Rodar os testes do catálogo e commitar `feat: catalogar campos autorizaveis por recurso`.

### Task 2: Persistir policies, concessões e gestores

- [ ] Criar `apps.api.field_access` com `start_api_app`; registrar em `BUSINESS_APPS` e manter testes/migrations dentro do app.
- [ ] Escrever testes de unicidade de policy viva por `(organization, resource, field)`, concessão por `(policy, membership)` e gestão por `(organization, membership)`.
- [ ] Escrever testes que recusem vínculo de outro tenant e ignorem vínculo desativado ou excluído.
- [ ] Implementar `FieldWritePolicy`, `FieldGrant`, `FieldAccessManager` e estado versionado; gerar migration inicial e rodar `makemigrations --check --dry-run`.
- [ ] Rodar `pytest apps/api/field_access/tests/test_models.py -q --reuse-db`; commitar `feat: persistir politicas e concessoes de campo`.

### Task 3: Autorizar em lote e manter consistência

- [ ] Escrever testes para papel mínimo, concessão individual, key negada, campo aberto, múltiplas negações e isolamento entre organizações.
- [ ] Implementar `FieldAuthorizationService` com uma busca por lote `organização + recurso`, chave pública preservada e fallback Redis→banco que nunca autoriza em caso de falha dupla.
- [ ] Escrever teste concorrente PostgreSQL: transação de escrita com lock compartilhado e alteração de policy com lock exclusivo; confirmar a ordem de commit exigida pela spec.
- [ ] Implementar invalidação por epoch em `transaction.on_commit()`, sem usar Cachalot nas queries de recomposição.
- [ ] Rodar testes de autorização, cache e concorrência do app; commitar `feat: decidir escrita de campos com isolamento transacional`.

### Task 4: Proteger cada entrada DRF gravável

- [ ] Escrever testes HTTP de `POST`, `PUT` e `PATCH` para campo enviado/omitido, alias simples, `ForeignKey`, `ManyToMany`, negação agregada e nenhuma gravação parcial.
- [ ] Integrar `FieldWriteAuthorizationMixin` em `BaseModelSerializer.to_internal_value()` antes de `super()`, observando `field.read_only`, `field.source` e as chaves presentes no payload.
- [ ] Migrar `TimeSerializer`, `VinculoSerializer`, `ConviteSerializer` e `OrganizacaoEmailFaturamentoSerializer` para `BaseModelSerializer`; classificar serializers só de leitura e preservar os campos/erros públicos atuais.
- [ ] Adicionar teste arquitetural que enumera `serializers.ModelSerializer` nas apps e falha para subclass gravável direta sem isenção explícita.
- [ ] Rodar `pytest apps/organizacoes/tests apps/api/base/tests apps/api/field_access/tests -q --reuse-db`; commitar `feat: aplicar policies de campo nos serializers`.

### Task 5: Expor gestão administrativa segura

- [ ] Escrever testes de proprietário implícito, gestor delegado, gestor que revoga a própria delegação e key impedida de administrar.
- [ ] Implementar `FieldAccessManagementService` com locks de tenant/vínculo, unicidade, exclusão lógica em cascata de concessões e invalidação após commit.
- [ ] Implementar as rotas `/acessos_campos/campos_disponiveis/`, `/politicas/`, `/concessoes/` e `/gestores/` com os payloads portugueses da spec.
- [ ] Verificar 403/409/503, tenant cruzado e auditoria sem valores de campo; commitar `feat: administrar acesso a campos por organizacao`.

### Task 6: Fechar migrations, checks e entrega

- [ ] Criar operação reversível `RenameFieldWritePolicies(resource, old_field, new_field)` que usa models históricos e o alias do `schema_editor`; testar rename e reversão.
- [ ] Criar operação explícita de remoção que exclui logicamente policy e grants; adicionar system check que relata policy órfã e bloqueia toda escrita do recurso/organização.
- [ ] Adicionar métricas de decisão, fallback, latência e órfãs sem IDs de usuário/organização nos labels.
- [ ] Documentar o contrato, a operação de rename, a aplicação das policies e o limite para escritas ORM internas.
- [ ] Executar Ruff, mypy, migrations check, `mkdocs build --strict` e suíte completa com PostgreSQL/Redis; verificar cobertura mínima de 80% do código novo.
- [ ] Committar `docs: documentar autorizacao dinamica por campo`.

## Evidência de base (2026-09-20)

- `django.apps` não registra app de field access nem qualquer model `FieldWritePolicy`, `FieldGrant` ou `FieldAccessManager`.
- `TimeSerializer`, `VinculoSerializer` e `ConviteSerializer` não herdam de `BaseModelSerializer`.
- A suíte focada de autorização atual teve 72 passed; ela não cobre a funcionalidade ausente.
