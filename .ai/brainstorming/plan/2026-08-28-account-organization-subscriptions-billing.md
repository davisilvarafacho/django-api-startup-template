# Batch 5 — Plano de Implementação

> **Para agentes:** SKILL SUBOBRIGATÓRIA: use `superpowers:subagent-driven-development` (recomendado) ou `superpowers:executing-plans` para executar este plano tarefa a tarefa. Os passos usam checkboxes (`- [ ]`) para acompanhamento.

**Objetivo:** entregar o ciclo completo de conta, organização, assinatura e faturamento Stripe descrito no Batch 5.

**Arquitetura:** quatro incrementos verticais constroem primeiro identidade e conta, depois onboarding e tenancy, em seguida o núcleo comercial independente de gateway e, por fim, o subapp de faturamento. Operações externas usam preparação transacional, I/O sem lock e confirmação transacional; fatos globais roteiam o tenant antes de qualquer acesso protegido por RLS.

**Tecnologias:** Python 3.12, Django 5.2, Django REST Framework, PostgreSQL, django-rls, Celery, Redis, google-auth, django-checkouts 1.0, Stripe, pytest-django, Ruff e mypy.

**Spec:** `.ai/brainstorming/spec/2026-08-13-account-organization-subscriptions-billing-design.md`

## Restrições globais

- Durante o desenvolvimento integrado, usar `../django-checkouts`; antes da entrega final, fixar `django-checkouts[stripe]==1.0.0` e regenerar `uv.lock`.
- Somente `apps.assinaturas.subapps.faturamento` pode importar `django_checkouts`; nenhum módulo importa diretamente Stripe.
- Views e serializers chamam objetos de aplicação; não criam nem alteram models de domínio diretamente.
- Dinheiro é inteiro na menor unidade; estados persistidos usam `IntegerChoices` em intervalos de 10 e `PositiveSmallIntegerField`.
- Models tenantizados incluem `organizacao` nas identidades locais e são exercitados com PostgreSQL real sujeito a RLS.
- Tokens, body bruto, headers de assinatura, payload `raw`, PAN e CVV não são persistidos nem registrados em log/auditlog.
- Cada comportamento segue RED → GREEN → REFACTOR; o teste precisa falhar pela ausência do comportamento antes da implementação.
- Cada gate executa Ruff, mypy, migrations check, testes focados e `mkdocs build --strict`; a entrega exige pelo menos 80% de cobertura do código novo.

---

## Mapa de arquivos e responsabilidades

| Área | Arquivos principais | Responsabilidade |
|---|---|---|
| Conta | `apps/usuarios/models.py`, `accounts.py`, `emails.py`, `tasks.py`, `serializers.py`, `views.py`, `urls.py`, `errors.py` | Verificação/troca de e-mail, desativação, reativação, exclusão e anonimização. |
| Google | `apps/api/autenticacao/models.py`, `identities.py`, `serializers.py`, `views.py`, `urls.py`, `errors.py` | Validar ID token, criar/vincular/desvincular identidade e emitir sessão local. |
| Organização | `apps/organizacoes/models.py`, `organizations.py`, `memberships.py`, `onboarding.py`, `middleware.py`, `context.py`, `views.py` | Ciclo da organização, propriedade, ocupação de seats e contexto tenant. |
| Comercial | `apps/assinaturas/models.py`, `features.py`, `catalogs.py`, `subscriptions.py`, `proposals.py`, `access_policies.py` | Catálogo imutável, snapshots, alterações, propostas, seats, trials e acesso. |
| Billing | `apps/assinaturas/subapps/faturamento/models.py`, `checkouts.py`, `events.py`, `invoices.py`, `tasks.py`, `views.py` | Stripe, checkout, faturas, webhook, retries e reconciliação. |
| Operação | `api/settings.py`, `.env.example`, `docs/reference/api.md`, `docs/how-to/` | Configuração, rotas, jobs, rollout e operação segura. |

## Interfaces estáveis entre incrementos

```python
class Contas:
    @classmethod
    def validar_para_onboarding(cls, usuario: Usuario) -> None: ...

@dataclass(frozen=True)
class OcupacaoSeats:
    consumidos: int
    reservados: int

class Vinculos:
    @classmethod
    def calcular_ocupacao(cls, organizacao: Organizacao, papeis_isentos: frozenset[Papel]) -> OcupacaoSeats: ...

class CatalogoPlanos:
    @classmethod
    def obter_versao_inicial(cls, *, codigo: str, periodicidade: Periodicidade, moeda: str = "BRL") -> tuple[VersaoPlano, PrecoPlano]: ...

class Assinaturas:
    @classmethod
    def criar(cls, comando: CriacaoAssinatura) -> AssinaturaOrganizacao: ...

class CheckoutsCobranca:
    @classmethod
    def criar(cls, comando: CriacaoCheckout) -> ResultadoCheckout: ...

class EventosCobranca:
    @classmethod
    def receber(cls, *, variante: str, body: bytes, headers: Mapping[str, str]) -> EventoRecebido: ...
```

## Incremento 1 — identidade e ciclo da conta

### Task 1: Persistir verificação, exclusão agendada e identidade externa

**Arquivos:** modificar `apps/usuarios/models.py`, `apps/api/autenticacao/models.py`, respectivos `admin.py`; criar migrations e testes de models.

**Produz:** `Usuario.email_verificado_em`, `exclusao_solicitada_em`, `exclusao_agendada_para`; `ProvedorIdentidade`; `IdentidadeExterna` com unicidades parciais.

- [ ] Escrever testes que criem duas identidades vivas com o mesmo `(provedor, identificador)` e com o mesmo `(usuario, provedor)` e esperem `IntegrityError`; provar que registro soft-deleted libera ambos.
- [ ] Escrever testes de alteração direta de `Usuario.email`: e-mail diferente limpa `email_verificado_em`; salvar outros campos preserva a verificação.
- [ ] Rodar os testes e confirmar falhas por campos/models ausentes.
- [ ] Implementar os fields e constraints exatamente como o spec; registrar `IdentidadeExterna` no auditlog sem serializar `identificador`.
- [ ] Gerar migrations com `uv run python manage.py makemigrations usuarios autenticacao`; rodar novamente os testes.
- [ ] Commit: `feat: adicionar estado de conta e identidades externas`.

### Task 2: Implementar tokens e fluxos de e-mail

**Arquivos:** criar `apps/usuarios/emails.py`, `accounts.py`, `errors.py`, `serializers.py`, `urls.py`, `tests/email/`; modificar `views.py`, `apps/api/autenticacao/tasks.py`, `api/settings.py`, `.env.example`.

**Consome:** autenticação recente/MFA existente e `revoke_all_user_credentials`.

**Produz:** tokens assinados por propósito; `Contas.verificar_email`, `solicitar_troca_email`, `confirmar_troca_email`; quatro endpoints do spec.

- [ ] Testar token de verificação válido, propósito trocado, e-mail alterado, expiração e replay após verificação; a expectativa pública para tokens inválidos é sempre `account.email_verification_invalid`.
- [ ] Testar reenvio com resposta idêntica para e-mail existente/inexistente e rate limit no cache.
- [ ] Testar troca concorrente: uma confirmação vence, a outra recebe `account.email_already_in_use`; confirmar revoga todas as sessões e API keys e agenda notificações aos dois endereços no commit.
- [ ] Confirmar RED contra as rotas inexistentes.
- [ ] Implementar `TimestampSigner` com salts separados, `max_age` configurável e payload `{usuario_id, email, purpose}`; normalizar e-mail pelo manager do usuário.
- [ ] Implementar serializers/views finos, registrar rotas públicas no `PUBLIC_ROUTES` e exigir recent auth/MFA na solicitação autenticada.
- [ ] Rodar `pytest apps/usuarios/tests/email -q` e testes de autenticação afetados.
- [ ] Commit: `feat: adicionar verificação e troca segura de email`.

### Task 3: Implementar autenticação Google

**Arquivos:** criar `apps/api/autenticacao/identities.py`, `tests/google/`; modificar serializers, views, urls, errors, settings, `.env.example`, `pyproject.toml`, `uv.lock`.

**Produz:** `AutenticacaoGoogle.autenticar`, `vincular`, `desvincular` e endpoints `/auth/google/`, `/connect/`, `/disconnect/`.

- [ ] Testar, com verificador de token substituído somente na fronteira `verify_oauth2_token`, cadastro novo, login por `sub`, conflito de e-mail, token inválido uniforme, usuário inativo/excluído e corrida de cadastro.
- [ ] Testar confiança: `@gmail.com` e `hd` verificam localmente; endereço externo sem `hd` não verifica.
- [ ] Testar que vínculo exige e-mail local verificado e autenticação recente; desvínculo exige senha utilizável e preserva pelo menos um meio de login.
- [ ] Confirmar RED antes de adicionar `google-auth`.
- [ ] Adicionar `google-auth`, configurar `GOOGLE_OAUTH_CLIENT_IDS` via `get_list_from_env` e validar audience contra cada client ID aceito.
- [ ] Reutilizar o emissor atual de `AuthToken`/`TokenMetaData`; não persistir claims, access token ou `raw`.
- [ ] Rodar testes Google, login local, MFA e schema.
- [ ] Commit: `feat: adicionar login e vínculo com Google`.

### Task 4: Completar desativação, reativação e anonimização

**Arquivos:** modificar `apps/usuarios/accounts.py`, `tasks.py`, views/urls; criar `tests/lifecycle/`; modificar `api/settings.py`/Celery beat.

**Produz:** `Contas.desativar`, `solicitar_reativacao`, `confirmar_reativacao`, `agendar_exclusao`, `anonimizar_contas_vencidas`.

- [ ] Testar bloqueio do único proprietário com locks; desativação revoga credenciais/dispositivos e suspende vínculos sem liberar seat.
- [ ] Testar solicitação pública genérica e confirmação de reativação; confirmar cancelamento de exclusão sem ressuscitar sessões ou vínculos.
- [ ] Testar prazo padrão de sete dias, segunda solicitação idempotente e task em lotes; anonimização limpa PII/sub, cancela convites e libera o e-mail original.
- [ ] Confirmar RED e implementar operações transacionais usando a ordem global de locks já adotada em `Usuario.delete()`.
- [ ] Adicionar endpoints, task periódica e documentação; rodar toda a suíte do incremento.
- [ ] Gate 1: Ruff, mypy, migrations check, docs e cobertura dos arquivos novos.
- [ ] Commit: `feat: completar ciclo de vida da conta`.

## Incremento 2 — organização, onboarding e tenancy

### Task 5: Evoluir organização, vínculos e ocupação de seats

**Arquivos:** modificar `apps/organizacoes/models.py`; criar `organizations.py`, `memberships.py`, testes de domínio e migration.

**Produz:** e-mail de faturamento, agendamento de encerramento, estado explícito de vínculo/convite, `OcupacaoSeats` e `Vinculos`.

- [ ] Testar e implementar fields/constraints para `Organizacao.email_faturamento`, encerramento e suspensão; preservar registros históricos.
- [ ] Testar agregação literal: ativo e suspenso consomem, convite pendente reserva, expirado/aceito não reserva e papel isento não consome.
- [ ] Mover criação/aceite de vínculos para `Vinculos`; bloquear assinatura corrente antes de recalcular o último seat (a integração fica ativa após Task 9).
- [ ] Rodar testes existentes de organizações para provar compatibilidade.
- [ ] Commit: `feat: adicionar ciclo e ocupacao de organizacoes`.

### Task 6: Criar onboarding e encerramento

**Arquivos:** criar `apps/organizacoes/onboarding.py`; modificar serializers/views/urls/tasks/errors/settings; criar testes de onboarding e encerramento.

**Produz:** `Organizacoes`, `OrganizationOnboarding` e endpoints de criação/encerramento.

- [ ] Testar pré-condições de onboarding, cópia do e-mail verificado e rollback integral quando qualquer colaborador falha.
- [ ] Implementar o orquestrador fino com `organizacao_atual_privilegiada` somente ao criar o contrato tenantizado.
- [ ] Testar encerramento imediato gratuito, agendado pago, cancelamento e efetivação idempotente com revogação de API keys.
- [ ] Implementar tasks/rotas e erros sem revelar organizações alheias.
- [ ] Commit: `feat: adicionar onboarding e encerramento de organizacoes`.

### Task 7: Construir contexto tenant completo

**Arquivos:** modificar `apps/organizacoes/context.py`, `middleware.py`, `permissions.py`, errors; criar testes de middleware/contexto e arquitetura.

**Produz:** `ContextoOrganizacao`; middleware como único resolvedor de tenant/assinatura/acesso; marcador `regularizacao_assinatura`.

- [ ] Testar matriz sessão/API key, header divergente, sem vínculo, vínculo inativo e organização inativa sem enumeração.
- [ ] Testar que uma rota comum é bloqueada após carência e uma rota marcada permite apenas proprietário/administrador regularizar.
- [ ] Implementar contexto imutável e fazer `TenantPermission` apenas consumi-lo.
- [ ] Adicionar teste arquitetural que proíba allowlist textual para regularização.
- [ ] Gate 2 completo e commit: `feat: integrar contexto comercial ao tenant`.

## Incremento 3 — catálogo, contratos, seats e acesso

### Task 8: Criar app, recursos tipados e catálogo imutável

**Arquivos:** criar app `apps/assinaturas` pelo `start_api_app`; criar `features.py`, `catalogs.py`, management command `sync_plans`, testes e migrations; modificar settings/docs.

**Produz:** `RecursoPlano[T]`, `ValoresRecursos`, `CatalogoRecursos`, `Plano`, `VersaoPlano`, `PrecoPlano`, definições tipadas e `CatalogoPlanos`.

- [ ] Gerar o app pelo comando do projeto e conferir dotted path/ordem em `BUSINESS_APPS`.
- [ ] Testar chaves duplicadas, tipos inválidos, default de snapshot antigo e JSON Schema literal.
- [ ] Testar constraints e imutabilidade de versão/preço publicados.
- [ ] Testar `sync_plans` dry-run, apply, repetição e recusa de mutação da mesma versão.
- [ ] Implementar o catálogo gratuito v1 e um plano pago Stripe de exemplo sem referências remotas embutidas.
- [ ] Commit: `feat: adicionar catalogo versionado de planos`.

### Task 9: Implementar assinatura, preço e alterações

**Arquivos:** modificar `models.py`; criar `subscriptions.py`; criar testes de contratos/alterações e migration.

**Produz:** DTOs do spec, `AssinaturaOrganizacao`, `AlteracaoAssinatura`, `Assinaturas.criar` e presets.

- [ ] Testar XOR de origem, uma assinatura corrente, snapshots completos e estados/data coerentes.
- [ ] Testar preço com literais para franquia, excedente, mensal/anual e zero.
- [ ] Testar criação gratuita/trial/paga e idempotência; somente o método base instancia o model.
- [ ] Testar revisão otimista e alterações imediatas/agendadas, incluindo evento atrasado sem regressão.
- [ ] Implementar locks, transições nominais e auditoria; integrar onboarding e último seat das Tasks 5–7.
- [ ] Commit: `feat: adicionar contratos e alteracoes de assinatura`.

### Task 10: Implementar propostas enterprise

**Arquivos:** modificar models/admin; criar `proposals.py`, testes e migration; modificar serializers/views/urls.

**Produz:** `PropostaComercial`, `Propostas` e endpoint de aceite.

- [ ] Testar ciclo completo, validade, revisão, autorização e repetição do aceite.
- [ ] Testar que modo pagamento apenas aceita e prepara checkout; modo contratual exige operador, MFA e justificativa no Admin.
- [ ] Testar ativação atômica encerrando contrato corrente e criando novo ciclo originado exclusivamente da proposta.
- [ ] Implementar API/Admin e commit: `feat: adicionar propostas comerciais enterprise`.

### Task 11: Implementar trials, carências e política de acesso

**Arquivos:** criar `access_policies.py`; modificar `subscriptions.py`, tasks, middleware e testes.

**Produz:** `UtilizacaoSeats`, `SituacaoAcesso`, `PoliticaAcessoAssinatura` e encerramento de trial.

- [ ] Testar todas as fórmulas com expectativas literais e coexistência das duas carências.
- [ ] Testar que renovação falha não reinicia prazo; pagamento/ocupação regularizados limpam apenas a carência correspondente.
- [ ] Testar fallback de trial no mesmo contrato, incremento de revisão, ausência de carência financeira e eventual carência de seats.
- [ ] Implementar tasks idempotentes, endpoints de consulta/alteração/cancelamento e integração final do middleware.
- [ ] Criar `initialize_subscriptions` dry-run/apply e system check de rollout.
- [ ] Gate 3 completo e commit: `feat: completar politicas de assinatura e acesso`.

## Incremento 4 — faturamento Stripe

### Task 12: Integrar django-checkouts e criar modelos financeiros

**Arquivos:** adicionar dependência local; criar subapp `apps.assinaturas.subapps.faturamento`, models/admin/checks/errors, tests e migrations; modificar settings/env.

**Produz:** `AssinaturaGateway`, `ReferenciaPrecoGateway`, `CheckoutCobranca`, `FaturaAssinatura`, `EventoCobranca` e contexts RLS de ingresso.

- [ ] Gerar subapp com `start_api_app faturamento --parent assinaturas` e registrar ambos separadamente.
- [ ] Testar todas as constraints descritas nas seções 15.2 e 20, incluindo IDs globais e coerência de organização.
- [ ] Testar RLS real: ingresso insere/atualiza linha nula uma vez, não enxerga roteadas; tenant enxerga somente sua linha.
- [ ] Implementar `billing_ingress` em `REGISTERED_CONTEXT_KEYS`, checks e admin seguro.
- [ ] Configurar `CHECKOUT_VARIANTS["stripe"]` apenas por variáveis de ambiente.
- [ ] Commit: `feat: adicionar persistencia de faturamento`.

### Task 13: Implementar checkout em três fases

**Arquivos:** criar `checkouts.py`, serializers/views/urls, testes de checkout; modificar interfaces de assinaturas/propostas.

**Produz:** `CriacaoCheckout`, `ResultadoCheckout`, referência assinada versionada e `CheckoutsCobranca.criar`.

- [ ] Testar capability e referência de preço ausentes antes de qualquer comando capturado pelo fake.
- [ ] Testar itens base/seat, total, moeda, referência assinada, quatro finalidades e idempotência por organização.
- [ ] Testar sucesso, falha conhecida e falha incerta: nenhuma transação/lock permanece durante `client.checkouts.create`.
- [ ] Implementar preparação, I/O e confirmação separadas; persistir somente resposta normalizada, nunca `raw`.
- [ ] Adicionar endpoints e métricas sem labels de alta cardinalidade.
- [ ] Commit: `feat: adicionar checkouts recorrentes Stripe`.

### Task 14: Ingerir e rotear webhooks com segurança

**Arquivos:** criar `events.py`, webhook view/route, testes de ingestão e RLS.

**Produz:** `EventosCobranca.receber`, hash canônico da allowlist normalizada, deduplicação e roteamento.

- [ ] Testar que assinatura inválida não persiste; duplicata de mesmo hash retorna sucesso sem reagendar; mesmo ID/hash diferente retorna erro de segurança sem sobrescrever.
- [ ] Testar roteamento por referência assinada e assinatura externa; referência adulterada ou tenant incoerente fica não roteado.
- [ ] Testar tipo desconhecido como `IGNORADO` e tipo conhecido/status inválido como falha de protocolo.
- [ ] Implementar verificação via `client.webhooks`, sanitização antes do `JSONField` e enqueue somente em `transaction.on_commit`.
- [ ] Commit: `feat: adicionar ingestao idempotente de webhooks`.

### Task 15: Processar eventos, faturas e reconciliação

**Arquivos:** completar `events.py`, `invoices.py`, `tasks.py`, views/admin, testes de workers/reconciliação.

**Produz:** claim/process/finalize sem lock durante rede, até oito tentativas, recuperação e reconciliação pelo mesmo processador.

- [ ] Testar checkout, assinatura e invoice recuperados como fonte final; fatura paga após vencida e evento atrasado não regredindo revisão.
- [ ] Testar backoff exponencial, limite de oito, retry operacional e recuperação de evento abandonado.
- [ ] Testar reconciliação por janela/cursor usando `client.events.list` e o mesmo registro/processador.
- [ ] Implementar tasks com contexto tenant privilegiado somente após roteamento e sem I/O dentro de `atomic`.
- [ ] Adicionar listagens de faturas/checkouts, checkout de forma de pagamento, métricas e Admin seguro.
- [ ] Commit: `feat: processar cobrancas e reconciliar Stripe`.

### Task 16: Fechar rollout, documentação e dependência publicada

**Arquivos:** `pyproject.toml`, `uv.lock`, `.env.example`, docs de API/operação/arquitetura, changelog e schema.

- [ ] Rodar `sync_plans --apply`, `initialize_subscriptions` primeiro em dry-run e depois `--apply` em banco de teste representativo.
- [ ] Publicada a biblioteca, executar `uv remove django-checkouts` se necessário e `uv add 'django-checkouts[stripe]==1.0.0'`; confirmar que `uv.lock` não contém path/VCS.
- [ ] Rodar `uv run python manage.py makemigrations --check --dry-run` e `uv run python manage.py check --deploy` com configuração de teste apropriada.
- [ ] Rodar `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, suíte pytest completa com cobertura e `uv run mkdocs build --strict`.
- [ ] Confirmar cobertura mínima de 80% para todos os módulos novos e executar um teste de fumaça Stripe sandbox documentado sem registrar segredos.
- [ ] Revisar logs/auditlog por PII/payloads, `git diff --check` e estado do worktree.
- [ ] Commit: `docs: documentar operacao de assinaturas e faturamento`.

## Critério de conclusão

O Batch 5 só termina quando todos os gates passam, organizações existentes têm contrato inicializável de forma idempotente, o middleware não permite organização ativa sem assinatura, a suíte RLS roda com papel sem bypass, Stripe funciona pela interface publicada do `django-checkouts` e o lockfile fixa exatamente a versão 1.0.0 publicada.
