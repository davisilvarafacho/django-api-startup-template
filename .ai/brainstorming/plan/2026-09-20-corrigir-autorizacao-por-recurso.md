# Correção da autorização por recurso — Implementation Plan

> **Para agentes:** SKILL SUBOBRIGATÓRIA: use `superpowers:executing-plans` para executar este plano tarefa a tarefa. Os passos usam checkboxes (`- [ ]`) para acompanhamento.

**Goal:** fechar as três falhas reproduzidas e implementar o contrato aprovado de autorização por recurso sem liberar rotas durante a transição.

**Architecture:** primeiro bloquear as concessões indevidas já observadas; depois substituir a autorização dispersa por uma `ResourcePolicy` por recurso, resolvida por `view.action`. A mesma policy alimentará o `ScopeRegistry`, a delegação de API keys, os checks de inicialização e o catálogo de scopes.

**Tech Stack:** Python 3.12, Django 5.2, DRF, PostgreSQL, pytest-django.

**Spec:** `.ai/brainstorming/spec/2026-08-10-model-permission-policy-design.md`.

## Global Constraints

- Sessão humana: autenticação, tenant/vínculo, permission Django, papel mínimo e queryset/RLS devem ser cumulativos.
- API key: tenant, disponibilidade da action e scope; permissions e papel do responsável não participam do uso.
- `permission_classes` da classe e de `@action` são aditivas para `ModelPermissionMixin`.
- Migração sem fallback silencioso: policy incompleta ou inválida falha fechada.
- Testes usam migrations reais e PostgreSQL; executar com `DATABASE_USER=postgres DATABASE_PASSWORD=postgres` no ambiente Compose local.

## Review Focus

1. Uma sessão com papel suficiente e sem codename Django deve receber 403 em cada mutação CRUD.
2. Uma key com `memberships:delete`, `memberships:*` ou `*` deve receber 403 no `destroy` de vínculo.
3. Um scope armazenado como `*:delete` não deve autorizar leitura, mesmo em keys emitidas antes da correção.
4. `@action(permission_classes=[...])` não pode remover tenant, policy ou scope obrigatórios.
5. Actions customizadas devem usar `view.action`, inclusive em rotas com prefixo/versionamento diferente.

## Mapa de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `apps/api/core/scope_registry.py` | sintaxe de scope, disponibilidade por policy e tradução para codenames |
| `apps/api/autenticacao/permissions.py`, `scope_delegation.py` | negação de key e delegação de scopes |
| `apps/api/base/resource_policies.py` (novo) | `ActionPolicy`, `ResourcePolicy` e resolução por action |
| `apps/api/base/views.py` | `ModelPermissionMixin` e composição aditiva |
| `apps/organizacoes/views.py` | policies de organizações, times, vínculos e convites |
| `apps/api/core/route_markers.py`, `routes_registry.py` | contrato público e checks |
| `apps/api/autenticacao/views.py`, `serializers.py` | catálogo `/auth/api_keys/scopes/` e erros por índice |
| `apps/organizacoes/tests/`, `apps/api/autenticacao/tests/`, `apps/api/base/tests/` | regressão HTTP e unitária |
| `.gitattributes` | garantir LF no script de inicialização PostgreSQL usado pelo Docker no Windows |

### Task 0: Tornar o banco de testes reproduzível no Windows

- [ ] Acrescentar `docker/postgres/*.sh text eol=lf` a `.gitattributes`; confirmar `git ls-files --eol docker/postgres/init-billing-roles.sh` com `w/lf` após novo checkout.
- [ ] Em projeto Compose isolado, iniciar `db` e `redis` sem override temporário e confirmar healthchecks verdes e roles de faturamento presentes.
- [ ] Rodar um teste que usa migrations reais contra o banco recém-inicializado; commitar `fix: preservar LF nos scripts de inicializacao postgres`.

### Task 1: Corrigir o wildcard ambíguo

- [ ] Escrever teste unitário em `apps/api/autenticacao/tests/test_token_scopes.py`: `TokenScopePermission` deve negar `scopes=["*:delete"]` quando a view exige `teams:read`.
- [ ] Escrever teste em `apps/api/autenticacao/tests/test_scope_delegation.py`: delegar `*:delete` devolve `auth.invalid_scope`, inclusive para superuser; `*` continua válido quando autorizado.
- [ ] Rodar esses dois testes e registrar o estado RED. A prova desta auditoria foi `Failed: DID NOT RAISE APIError` para `*:delete` contra `teams:read`.
- [ ] Em `parse_scope()`, rejeitar `resource == "*" and action != "*"`; em `matches_scope()`, manter a exceção capturada por `TokenScopePermission` para negar também scopes inválidos já persistidos.
- [ ] Normalizar `ValueError` de parse na validação de escrita de API keys para `auth.invalid_scope` com índice do item, sem HTTP 500.
- [ ] Rodar `pytest apps/api/autenticacao/tests/test_token_scopes.py apps/api/autenticacao/tests/test_scope_delegation.py -q --reuse-db` e commitar `fix: rejeitar wildcards de scope ambiguos`.

### Task 2: Bloquear a exclusão de vínculos por API key

- [ ] Escrever teste HTTP em `apps/organizacoes/tests/test_api.py` para `memberships:delete`, `memberships:*` e `*`; os três devem devolver 403 e preservar o vínculo.
- [ ] Confirmar RED: o teste atual `test_api_key_com_scopes_crud_ignora_papel_pessoal_em_convites_e_vinculos` aceita `204` para `memberships:delete` e deve ser ajustado ao novo contrato.
- [ ] Declarar `destroy` como exclusiva de sessão em `VinculoViewSet` enquanto a policy definitiva é introduzida. `TokenScopePermission` deve checar essa indisponibilidade antes de `matches_scope()`.
- [ ] Rodar os testes focados de vínculos e API keys; commitar `fix: reservar exclusao de vinculos para sessoes`.

### Task 3: Exigir permission Django além do papel nas sessões

- [ ] Escrever testes HTTP em `apps/organizacoes/tests/test_api.py` para `Time`, `Vinculo` e `Convite`: ator com papel suficiente e sem `add_`, `change_` ou `delete_` recebe 403; com ambos recebe 2xx.
- [ ] Confirmar RED: `test_gestor_cria_convite_na_organizacao_do_header` hoje recebe `201` sem `organizacoes.add_convite`; atualizar fixture para conceder a permission quando o teste pretende cobrir sucesso.
- [ ] Compor `CustomDjangoModelPermissions` nas permissões dos três ViewSets durante a migração. Tratar `aceitar` separadamente com `can_accept_convite`; não aplicar codename CRUD à action customizada.
- [ ] Cobrir também `OrganizacaoViewSet` com model/policy explícito, pois seu queryset é dinâmico. Preservar a leitura da própria organização por key e o fluxo de onboarding sem tenant.
- [ ] Rodar `pytest apps/organizacoes/tests/test_api.py apps/organizacoes/tests/test_permission_cache.py -q --reuse-db` e commitar `fix: combinar permissions e papeis em organizacoes`.

### Task 4: Introduzir `ResourcePolicy` e o mixin obrigatório

- [ ] Criar `ActionPolicy` e `ResourcePolicy` imutáveis em `apps/api/base/resource_policies.py`, com operação lógica, codename, papel mínimo, disponibilidade para key e validação de actions expostas.
- [ ] Criar testes para resolução de CRUD/custom actions por `view.action`, model derivado de `queryset.model`, classe concreta sem policy e action sem codename/papel.
- [ ] Criar `ResourceAccessPermission` e `ModelPermissionMixin` em `apps/api/base/`; o mixin combina `IsAuthenticated`, `TenantPermission`, `ResourceAccessPermission` e permissões extras da classe/action, sem duplicatas.
- [ ] Migrar `BaseModelViewSet` e os ViewSets de organizações para policies explícitas. Declarar `memberships:delete` indisponível para key e manter os papéis mínimos atuais.
- [ ] Adicionar system checks que carregam URLs antes de validar policies, codenames, actions e duplicatas; executar `manage.py check`.
- [ ] Rodar a matriz de `apps/api/base/tests/` e `apps/organizacoes/tests/`; commitar `feat: centralizar autorizacao por recurso`.

### Task 5: Fazer scopes e delegação consumirem a mesma policy

- [ ] Remover o CRUD automático de model em `ScopeRegistry`; registrar apenas actions realmente expostas e delegáveis pelas policies. `resource:*` deve expandir só essas actions.
- [ ] Testar erros distintos `auth.invalid_scope`, `auth.scope_not_available`, `auth.scope_not_delegable` e `auth.insufficient_scope`, incluindo o índice da entrada `scopes`.
- [ ] Implementar `grant_api_scopes` para delegação ampla no tenant, mantendo `grant_unrestricted_apikey` para `*`; nenhum dos dois libera action proibida.
- [ ] Criar `GET /auth/api_keys/scopes/` somente para sessões com tenant e permission de administrar keys, informando disponibilidade, delegabilidade e motivo por action.
- [ ] Adicionar regressões para key antiga com scope agora proibido, wildcard por recurso, wildcard global e action customizada com opt-in.
- [ ] Rodar `pytest apps/api/autenticacao/tests apps/organizacoes/tests -q --reuse-db`; commitar `feat: derivar scopes das policies`.

### Task 6: Fechar rotas públicas, documentação e gates

- [ ] Fazer `@public` configurar `AllowAny` nas views próprias e rejeitar action pública dentro de `ModelPermissionMixin`.
- [ ] Adicionar checks para `AllowAny` sem rota pública e rota pública com permissions restritivas.
- [ ] Atualizar guias de ViewSets, autenticação e OpenAPI com a nova matriz sessão/key e o catálogo de scopes.
- [ ] Executar `ruff check .`, `ruff format --check .`, `mypy .`, `manage.py makemigrations --check --dry-run`, `mkdocs build --strict` e a suíte completa com migrations reais; verificar os três testes RED agora GREEN.
- [ ] Revisar diferenças de autorização antes de rollout; commitar `docs: documentar policies e scopes de recursos`.

## Evidência de base (2026-09-20)

- Suíte focada atual: 72 passed.
- Teste de spec: sessão sem `add_convite` recebeu `201` em vez de `403`.
- Teste de spec: API key excluiu vínculo com `204` em vez de `403`.
- Teste de spec: `*:delete` satisfez `teams:read` em vez de ser recusado.
- Teste de spec: `validate_scope_delegation(superuser, ["*:delete"])` aceitou o scope em vez de recusá-lo.
- No Windows, o checkout `w/crlf` de `docker/postgres/init-billing-roles.sh` abortou a criação do Postgres com `/bin/sh^M: bad interpreter`; uma cópia temporária LF permitiu os testes.
