# Task 9 — Assinatura, preço e alterações

## Resultado

- `AssinaturaOrganizacao` e `AlteracaoAssinatura` são models tenantizados com
  enums `PositiveSmallIntegerField` em passos de dez, constraints de origem,
  contrato corrente, idempotência, estados/datas, moeda e revisão.
- A migration `0005` cria as duas tabelas, protege o pedido e os snapshots de
  alteração contra ORM e SQL direto, e habilita `ENABLE/FORCE ROW LEVEL
  SECURITY` com policy por `organizacao_id` nas duas tabelas.
- O ruling da Task 10 foi preservado: `versao_plano` continua nullable no
  schema para permitir a migration atômica futura, mas a constraint atual
  exige a versão de catálogo. Não foi criado `PropostaComercial`, FK ou tipo
  runtime provisório.
- Os DTOs congelados `OrigemVersaoPlano`, `TermosAssinatura`,
  `CriacaoAssinatura` e `CriacaoAlteracaoAssinatura` validam tipos e estados na
  fronteira. `Assinaturas.criar` é o único ponto de produção que instancia
  `AssinaturaOrganizacao`; gratuita, trial e paga montam comandos e delegam.
- O preço usa literalmente franquia e quantidade absoluta de seats, sem
  prorrata implícita. O preset de trial tem relógio determinístico no retry e o
  preset pago rejeita preço total zero.
- Alterações persistem pedido e snapshots completos imutáveis, usam revisão
  otimista e transições nominais, e suportam aplicação imediata ou no próximo
  ciclo. Um evento atrasado conclui o histórico com a revisão observada sem
  sobrescrever o contrato mais novo.
- O onboarding real resolve `CatalogoPlanos`, cria gratuita ou trial sob RLS e
  permanece numa única transação com conta, organização e proprietário.
  `CatalogoPlanos` normaliza a periodicidade textual já publicada pelo seam do
  onboarding sem quebrar consumidores tipados.
- `Vinculos` agora preserva a ordem organização → assinatura corrente →
  vínculo/convite → recálculo de ocupação. Criação e aceite usam o contrato e
  os papéis isentos reais; duas transações concorrentes não conquistam o mesmo
  último seat. Organizações históricas sem contrato continuam operáveis até o
  rollout final da Task 11, que possui explicitamente o comando
  `initialize_subscriptions` e o system check.
- O encerramento real classifica contrato gratuito como imediato, contrato
  pago com período como agendado e encerra o snapshot corrente de forma
  idempotente. Não há import de faturamento nem `django-checkouts`.
- Os dois models novos estão registrados no auditlog. O erro público do limite
  usa `billing.seat_limit_reached` com somente totais não sensíveis.

## Evidência TDD

### RED observados

1. O primeiro teste de models parou na coleta com
   `ImportError: cannot import name 'AlteracaoAssinatura'`.
2. O primeiro teste de DTOs/preço/presets parou com
   `ModuleNotFoundError: apps.assinaturas.subscriptions`.
3. O primeiro teste das integrações reais parou com
   `ModuleNotFoundError: apps.assinaturas.errors`.
4. A prova PostgreSQL real, executada por papel comum, viu contratos e
   alterações dos dois tenants. A causa era a ausência de operações RLS na
   migration; depois de `ENABLE/FORCE` e da policy, cada contexto passou a ver
   exclusivamente o próprio tenant e nenhum contexto passou a ver zero linhas.
5. O teste global de auditlog encontrou os dois models novos como itens extras,
   provando que o registro de produção existia e que a lista normativa do teste
   precisava ser atualizada.
6. A auto-revisão gerou uma regressão para falha durante aquisição de locks: o
   teste recebeu `connection.in_atomic_block is True`. A causa era uma exceção
   dentro de `__enter__`; um generator context manager passou a garantir o
   fechamento do `atomic` também antes do `yield`.
7. Três REDs adicionais provaram que retry de trial sem relógio injetado
   divergia por microssegundos, que o preset pago aceitava preço zero e que o
   banco aceitava `ATIVA/PENDENTE`. Cada invariável recebeu correção isolada.

### GREEN focados e regressões

```text
models/constraints/imutabilidade inicial                 18 passed
DTOs/preço/presets/idempotência inicial                  14 passed
alterações imediatas/agendadas/concorrência inicial       7 passed
integrações reais iniciais                               29 passed
compatibilidade organizações/API                         62 passed
assinaturas + organizações + auditlog                   178 passed
regressão ampliada + architecture                       439 passed
suíte global final                                     1550 passed, 1 warning
```

O warning único é o `SECRET_KEY` temporário aleatório já presente no baseline.
O baseline global anterior à implementação foi `1496 passed, 1 warning`.

## Gates finais

```text
uv run pytest -q --no-cov --tb=short
1550 passed, 1 warning in 357.47s

uv run ruff check .
All checks passed!

uv run ruff format --check .
388 files already formatted

uv run python manage.py check
System check identified no issues (2 silenced).

uv run python manage.py makemigrations --check --dry-run
No changes detected

uv run mkdocs build --strict
Documentation built successfully

git diff --check
exit 0
```

O aviso do Material sobre MkDocs 2.0 e a lista de páginas fora do `nav` são os
mesmos avisos informativos já conhecidos; o build strict terminou com sucesso.

O mypy focado foi executado com `--explicit-package-bases` sobre
`apps/assinaturas` e `apps/organizacoes/memberships.py`: permaneceu exatamente
no baseline conhecido de 73 diagnósticos preexistentes nos módulos importados,
sem diagnóstico nos arquivos alterados pela Task 9. A invocação global sem
`--explicit-package-bases` continua parando antes da análise por nomes de
módulo duplicados, problema preexistente do repositório.

## Auto-revisão e limites

- `rg` confirma uma única chamada de produção a
  `AssinaturaOrganizacao.objects.create`, dentro de `Assinaturas.criar`.
- A prova RLS não usa o dono/superusuário para consultar: cria papel PostgreSQL
  comum com somente `SELECT` nas tabelas e valida os dois tenants e a ausência
  de contexto.
- O bloqueio de seats usa ocupação recalculada depois do lock da assinatura; o
  erro retorna os totais atuais, não o payload projetado da tentativa rejeitada.
- Nenhum módulo de proposta, checkout, faturamento ou adapter de gateway foi
  criado ou importado. Campos de evento existentes em `AlteracaoAssinatura`
  são somente metadados normalizados previstos no contrato desta task.
- A revisão por subagente recomendada pelo skill de code review não foi usada
  porque a instrução desta task proíbe subagentes; a revisão foi feita no diff
  local e produziu o RED do bloco atômico descrito acima.
- Não foi implementada funcionalidade das Tasks 10 ou posteriores. O backfill
  e a ativação final da exigência de assinatura continuam, conforme o plano,
  na Task 11.
