# Auditoria completa da codebase

**Data:** 15 de agosto de 2026  
**Escopo:** repositório completo  
**Método:** revisão estática paralela, reproduções direcionadas e validações automatizadas  
**Resultado:** nenhum arquivo de produção foi alterado durante a auditoria

## Resumo executivo

A codebase foi dividida em quatro frentes independentes:

1. `apps/api/`: autenticação, base, core, metadata e APIs transversais;
2. apps de domínio: usuários, organizações, times e integrações;
3. `internal_frameworks/` e `utils/`;
4. configuração Django, Docker, CI, observabilidade, documentação e validações globais.

A suíte global terminou com **1.030 testes aprovados** e **89% de cobertura**, mas a revisão confirmou falhas reais não cobertas pelos testes atuais. Os problemas de maior impacto estão em autorização, isolamento entre tenants, ciclo de vida de usuários e organizações, exposição de tokens, permissões administrativas e concorrência em operações sensíveis.

Uma segunda validação independente, executada por um agente `gpt-5.6-sol` com esforço `xhigh`, conferiu diretamente os 49 achados originais. O resultado foi **47 confirmados, 1 parcialmente confirmado, 1 não confirmado e nenhum obsoleto**. O achado P1.10 original foi retirado da fila alta e sua parte válida foi incorporada como dívida P3. A validação também encontrou cinco omissões materiais, uma delas incorporada ao P1.12.

Após a consolidação, este relatório contém **52 grupos ativos**: 12 P1 confirmados, 27 P2 — 26 confirmados e 1 parcial — e 13 P3 confirmados. Não foi confirmado nenhum P0.

| Faixa original validada | Confirmados | Parciais | Não confirmados |
|---|---:|---:|---:|
| P1.1–P1.13 | 12 | 0 | 1 |
| P2.1–P2.28 | 27 | 1 | 0 |
| P3.1–P3.8 | 8 | 0 | 0 |
| **Total** | **47** | **1** | **1** |

### Classificação usada

- **P1 — alta:** falha de segurança, isolamento, autorização, perda de dados ou indisponibilidade importante; deve entrar no próximo ciclo de correção.
- **P2 — média:** comportamento incorreto, risco operacional relevante, corrida ou configuração insegura que exige condições adicionais.
- **P3 — baixa:** dívida técnica, inconsistência documental, observabilidade incorreta ou lacuna de qualidade sem impacto imediato elevado.

## Achados P1 — alta prioridade

Os identificadores originais foram preservados para rastreabilidade. O antigo P1.10 foi rejeitado como vulnerabilidade alcançável e reaparece, reformulado, no P3.12.

### P1.1 — Usuário soft-deleted continua autenticando e retendo permissões

**Locais:**

- `apps/api/base/models.py:47-51,141-146`
- `apps/api/autenticacao/authentications.py:69-87`
- `apps/usuarios/models.py:11-13,41,76`
- `internal_frameworks/permission_cache/resolvers/django.py:15-17`
- `internal_frameworks/permission_cache/resolvers/guardian.py:19-25`
- `internal_frameworks/permission_cache/backends.py:37-38`

`SoftDeleteMixin.delete()` e `SoftDeleteQuerySet.delete()` alteram apenas `is_deleted`; a conta continua com `is_active=True`. O manager padrão de `Usuario` esconde registros deletados, mas o `_base_manager` usado para resolver a FK de tokens não aplica esse filtro. A autenticação verifica somente `is_active`, e os resolvers de permissão também ignoram `is_deleted`.

**Reprodução:** uma sessão autenticada recebeu HTTP 200 em `GET /times/` antes e depois de `usuario.delete()`. O estado final ficou `is_deleted=True`, `is_active=True`, e o usuário continuou visível pelo `_base_manager`.

**Impacto:** uma conta considerada excluída continua acessando tenants com sessões, tokens ou API keys existentes.

**Correção recomendada:**

- rejeitar `is_deleted` em todos os autenticadores e resolvers;
- tornar a exclusão transacional e também definir `is_active=False`;
- revogar sessões, API keys e dispositivos confiáveis;
- invalidar caches de permissão;
- testar exclusão individual e por queryset com autenticação HTTP antes/depois.

### P1.2 — Organização deletada ou inativa continua acessível

**Locais:**

- `internal_frameworks/permission_cache/resolvers/tenant.py:78-84`
- `apps/organizacoes/permissions.py:44-61`

O resolver de sessões exige `organizacao__is_active=True`, mas não exige `is_deleted=False`. Como o soft-delete preserva `is_active=True`, a organização continua válida. No fluxo de API key, a organização do token é usada diretamente, e a validação não recusa organização inativa ou deletada.

**Reproduções:**

- sessão: `GET /times/` respondeu 200 antes e depois de `organizacao.delete()`;
- API key: o endpoint continuou respondendo 200 depois de `organizacao.is_active=False`.

**Correção recomendada:** exigir simultaneamente organização ativa e não deletada em todos os caminhos; suspender ou revogar credenciais quando a organização for desativada/excluída; cobrir sessão e API key em testes HTTP.

### P1.3 — Gestor pode promover convite para proprietário via PATCH

**Locais:**

- `apps/organizacoes/serializers.py:108-123`
- `apps/organizacoes/views.py:115-121,132-137`

`ConviteCreateSerializer.validate_papel()` impõe o teto de papel apenas na criação. PUT/PATCH usam `ConviteSerializer`, que não possui a mesma validação. Gestores podem atualizar convites e elevar o papel para proprietário.

**Reprodução:** um ator com papel 30 enviou PATCH com `papel=50`; a API respondeu 200 e persistiu o valor. Ao aceitar, o destinatário torna-se proprietário.

**Correção recomendada:** mover a validação para um componente comum aos serializers de criação e atualização e testar PUT/PATCH acima do papel do ator.

### P1.4 — Relação M2M permite vazamento cross-tenant

**Locais:**

- `apps/organizacoes/models.py:118-120`
- `apps/organizacoes/admin.py:19-23`
- `apps/organizacoes/serializers.py:59-74`
- `apps/organizacoes/views.py:98-109`

`Vinculo.times` não garante que vínculo e time pertençam à mesma organização. O admin oferece um `filter_horizontal` global, e a serialização/prefetch não aplica uma segunda barreira defensiva. O serializer público já restringe o campo gravável `times` ao tenant atual; portanto, a corrupção exige admin, ORM ou outro fluxo interno que contorne esse serializer.

**Reprodução:** um vínculo da organização A foi associado a um time da organização B. `GET /vinculos/` no tenant A retornou `times_detalhe` contendo o nome do time secreto da organização B.

**Correção recomendada:** usar um modelo intermediário que permita validar/garantir tenant, restringir o admin, validar com `m2m_changed` e filtrar defensivamente a leitura. Se a invariável precisar ser garantida pelo banco, `m2m_changed` não basta: é necessário um desenho relacional ou trigger que torne a associação cross-tenant impossível.

### P1.5 — Administrador pode rebaixar proprietário, inclusive o último

**Locais:**

- `apps/organizacoes/serializers.py:76-85`
- `apps/organizacoes/views.py:100-106`

A validação compara o novo papel somente ao papel do ator. Ela não protege alvos com papel superior nem preserva o último proprietário.

**Reprodução:** ator com papel 40 alterou um proprietário de papel 50 para 20; a API respondeu 200.

**Correção recomendada:** impedir alterações sobre atores de hierarquia superior, proteger o último proprietário e serializar a checagem/alteração com lock transacional.

### P1.6 — Convite inativo pode ser aceito

**Locais:**

- `apps/organizacoes/models.py:168-175`
- `apps/organizacoes/serializers.py:129-150`

A propriedade que define convite pendente ignora `is_active`, e o lookup de aceitação não filtra esse campo.

**Reprodução:** um convite com `is_active=False` foi aceito com HTTP 200 e criou um `Vinculo`.

**Correção recomendada:** incluir `is_active` em `pending`, filtrar explicitamente na aceitação e executar lock, releitura, validação e aceite dentro da mesma transação com `select_for_update()`. Usar o lock isoladamente no serializer não elimina a corrida.

### P1.7 — Bearer token persistente em query string vaza por múltiplos canais

**Locais:**

- `apps/api/autenticacao/authentications.py:108-129`
- `docker/nginx/nginx.conf:70-75`
- `gunicorn.conf.py:31-35`
- `api/settings.py:50-67`
- `docker/nginx/snippets/security-headers.conf:11`

`QueryParamTokenAuthentication` aceita `?token=`. O Nginx registra `$request` e o Gunicorn registra `%(r)s`, ambos incluindo query string. O Sentry usa `send_default_pii=True`, e o `before_send` remove apenas o header `Authorization`, não URL/query. A política de referrer atual ainda permite a URL completa em navegação same-origin.

**Impacto:** credencial bearer recuperável em logs, eventos Sentry, histórico do navegador, proxies e referrers.

**Correção recomendada:** substituir o bearer durável por ticket curto, descartável e vinculado à rota; registrar somente o path; sanitizar query strings no Gunicorn/Sentry; usar `Referrer-Policy: no-referrer` nas respostas envolvidas.

### P1.8 — Restrição por IP interno é anulada atrás do Nginx documentado

**Locais:**

- `apps/api/core/ip_utils.py:11-25`
- `apps/api/core/metrics.py:22-29,41-45`
- `docker/nginx/sites/production/default.conf:92-95`
- `docker/nginx/snippets/proxy.conf:14-23`

O código usa exclusivamente `REMOTE_ADDR` e ignora a cadeia de proxies confiáveis. Atrás do Nginx, `REMOTE_ADDR` é o IP privado do container proxy, que pertence às redes autorizadas por padrão.

**Reprodução:** `REMOTE_ADDR=172.20.0.5` com cliente público `X-Forwarded-For=203.0.113.10` foi autorizado para métricas.

**Correção recomendada:** restringir métricas na própria rede/proxy ou resolver o IP original exclusivamente a partir de proxies explicitamente confiáveis, sem autorizar automaticamente todo peer privado.

### P1.9 — Ação genérica de clonagem no Django Admin permite escalada

**Locais:**

- `apps/api/base/admin.py:13,25-35,49-63,71-86`
- `apps/usuarios/admin.py:12-13,80-85`

Nos admins que herdam `BaseModelAdmin` e não desabilitam as ações, `clone_records`, `ativar_registros` e `inativar_registros` não declaram `allowed_permissions`. A filtragem padrão do Django inclui incondicionalmente ações sem essa declaração para quem alcança o changelist; o risco não se limita a usuários com `change_usuario`. A clonagem copia campos concretos e M2M. Em `UsuarioAdmin`, apenas e-mail e `is_active` são sobrescritos: hash de senha, `is_staff`, `is_superuser`, grupos e permissões são preservados.

**Impacto:** um operador staff com acesso de visualização ao changelist pode potencialmente clonar uma conta privilegiada e ativar a cópia, mesmo sem permissão `add` ou `change` adequada.

**Correção recomendada:** tornar clonagem opt-in; declarar e verificar permissão `add` para clone e `change` para ativação/inativação; proibir clonagem de usuários/credenciais ou limpar senha, flags, grupos e permissões; testar a matriz view/change/add.

### P1.11 — Exclusão em massa pula invalidação do cache de permissões

**Locais:**

- `apps/api/base/models.py:47-51`
- `internal_frameworks/permission_cache/signals/tenant.py:92-99`
- `internal_frameworks/permission_cache/signals/django.py:143-157`

`SoftDeleteQuerySet.delete()` usa `update(is_deleted=True)` diretamente. Isso não emite os sinais dos quais a invalidação de cache depende. O scanner arquitetural trata `.delete()` como terminal e não detecta esse comportamento.

**Impacto:** acesso removido pode permanecer em cache por até o TTL atual de 1.800 segundos.

**Correção recomendada:** centralizar exclusão/invalidação nos wrappers já existentes em `internal_frameworks/permission_cache/mutations.py` e adicionar um guardrail arquitetural para impedir writes diretos que pulem a invalidação.

### P1.12 — Rotação de campos criptografados perde atualizações concorrentes

**Local:** `internal_frameworks/sensitive_fields/rotation.py:34-49`

A rotina lê o ciphertext, descriptografa e depois usa `bulk_update()` sem lock ou compare-and-swap. Uma atualização normal concorrente pode ser sobrescrita, perdida ou restaurada com valor anterior.

Há um segundo defeito no mesmo fluxo: o SQL bruto lê inclusive registros soft-deleted, mas `model._default_manager.bulk_update()` pode filtrá-los e ignorá-los silenciosamente. Mesmo assim, o contador soma `len(changes)`, reportando atualizações que não ocorreram. A rotina também usa a conexão global/default, sem respeitar o alias do model.

**Correção recomendada:** usar transação com `select_for_update()` ou update condicional pelo ciphertext original; escolher explicitamente manager e alias coerentes; contabilizar somente linhas realmente alteradas; adicionar testes concorrentes, multi-DB e com soft-deleted.

### P1.13 — Fluxo Docker documentado pode produzir imagem que não inicia

**Locais:**

- `.env.example:9,11`
- `Dockerfile:29,41`
- `docker-compose.yml`
- `api/configure_enviroment.py`
- `README.md:138,147`

O Dockerfile instala dependências com `uv sync --frozen --no-dev`. O `.env.example` ativa `DJANGO_ENVIRONMENT=development` e `DJANGO_DEBUG=True`, e o compose injeta esse arquivo no container. No ambiente de desenvolvimento, Django tenta carregar `debug_toolbar`, `django_extensions`, `drf_api_logger`, `silk` e `zeal`, que pertencem ao grupo dev e não estão na imagem.

**Correção recomendada:** separar claramente a imagem/configuração de desenvolvimento da imagem de produção, alinhar o tutorial e falhar cedo quando a combinação for inválida.

## Achados P2 — prioridade média

Os antigos P2.7, P2.17 e P2.19 foram reclassificados como P3.9, P3.10 e P3.11. Os demais IDs originais foram mantidos para permitir comparação com a validação independente.

### P2.1 — Paginação sem limite

**Local:** `apps/api/core/pagination.py:5-6,55-66`

`max_page_size` permanece `None`, qualquer inteiro positivo é aceito e `page_size=all` vira `7**10`, aproximadamente 282 milhões. Isso permite consumo excessivo de banco, CPU e memória.

Definir um limite finito e mover exportações completas para fluxo assíncrono/privilegiado.

### P2.2 — PATCH concorrente de metadata perde dados ou retorna 500

**Locais:** `apps/api/metadata/handlers.py:51-77`, `apps/api/metadata/models.py:26-35`

O handler faz leitura, merge Python e `save()` sem transação ou lock. Duas criações podem colidir na constraint única; atualizações de chaves diferentes podem sobrescrever umas às outras.

### P2.3 — Reset de senha não garante um único token ativo

**Local:** `apps/api/autenticacao/passwords.py:35-60`

Duas transações podem invalidar zero tokens antigos e criar dois tokens válidos. Bloquear uma linha estável do usuário antes de invalidar/criar.

### P2.4 — Limite de sessões é inconsistente e pode ser contornado

**Locais:** `apps/api/autenticacao/views.py:103-119,161-169`, `apps/api/autenticacao/mfa.py:251-288`

A contagem inclui sessões revogadas com expiração futura, ignora sessões ativas sem expiração, possui corrida `count-then-create` e não é aplicada à emissão final no fluxo MFA.

### P2.5 — Tentativas e envios MFA são renováveis

**Locais:** `apps/api/autenticacao/views.py:395-416,478-512`, `apps/api/autenticacao/mfa.py:267-341`

Cada novo challenge reinicia tentativas e não invalida challenges anteriores. Depois de esgotar ou consumir o challenge mais recente, o fluxo pode voltar a aceitar um anterior. O atacante pode renovar tentativas de OTP e causar volume elevado de SMS/e-mail. O `UserRateThrottle` global de 1.000 requisições/hora reduz o volume máximo, mas não elimina a renovação nem oferece cooldown específico por usuário/IP/finalidade.

### P2.6 — `MFA_SMS_ENABLED=False` não desabilita SMS

**Locais:** `api/settings.py:517-519`, `apps/api/autenticacao/checks.py:8-12`, `apps/api/autenticacao/mfa.py:101-141`

A flag é lida somente pelo system check; enrollment, métodos disponíveis, challenge e envio não a consultam.

### P2.8 — API keys aceitam expiração que não persiste e reportam status incorreto

**Local:** `apps/api/autenticacao/serializers.py:162-183,222-230`

`expiry` é gravável, mas o update ignora o campo. Além disso, uma chave expirada recebe status `active`, apesar de ser rejeitada na autenticação.

### P2.9 — API key em `invitations:create` pode provocar HTTP 500 ao validar papel explícito

**Classificação:** parcialmente confirmado.

**Locais:** `apps/organizacoes/permissions.py:44-61,95-117`, `apps/organizacoes/serializers.py:108-123`.

O fluxo de API key define `request.tenant=None`; a permissão pode liberar a requisição pelos scopes. Sem o campo `papel`, a criação retorna HTTP 201. Quando o cliente envia `papel` explicitamente, `ConviteCreateSerializer.validate_papel()` acessa `request.tenant.role` e provoca `AttributeError`/HTTP 500.

**Correção recomendada:** separar a validação de hierarquia humana da autorização de API keys e definir explicitamente como o scope governa o papel permitido.

### P2.10 — Bloqueio do Axes viola o envelope global de erros

**Local:** `apps/api/autenticacao/handlers.py:72-93`

O handler retorna `{"mensagem": ...}` em vez do contrato `{"errors": [...], "request_id": ...}`.

### P2.11 — Readiness do B2 pode ficar verde durante indisponibilidade

**Locais:** `apps/api/core/b2_storage.py:139-144`, `apps/api/core/health_check.py:112-115`

`exists()` transforma qualquer `B2Error` em `False`; a readiness chama `exists("")` e ignora o booleano. Falhas de autenticação, rede ou serviço podem resultar em readiness saudável.

### P2.12 — Readiness pública expõe detalhes internos

**Local:** `apps/api/core/health_check.py:35-72`

As respostas públicas incluem tipo e texto integral da exceção, podendo revelar hostnames, buckets e topologia.

### P2.13 — Migração de storage apaga o destino cedo demais

**Locais:** `apps/api/core/storage_migration.py:149-183`, `apps/api/core/management/commands/migrate_storage.py:53-56`

Com overwrite, o destino é excluído antes de abrir, salvar e verificar a origem. Uma falha posterior perde a versão anterior. Aliases diferentes também podem apontar ao mesmo backend/localização.

### P2.14 — Regeneração de recovery codes não é atômica

**Locais:** `apps/api/autenticacao/mfa.py:184-189`, `apps/api/autenticacao/views.py:468-475`

O endpoint executa delete seguido de `bulk_create` sem lock/transação. Concorrência pode deixar dois lotes, e falha intermediária deixa zero códigos.

### P2.15 — Campo criptografado não é garantidamente write-only

**Local:** `internal_frameworks/sensitive_fields/fields.py:13-16`

A proteção só injeta `extra_write_only_fields` quando o model possui essa propriedade. Um `ModelSerializer` genérico para um model real como `MFAFactor` pode expor o segredo TOTP em texto claro.

### P2.16 — Guardian usa alias incorreto em cenário multi-database

**Local:** `internal_frameworks/permission_cache/resolvers/guardian.py:28-52`

O alias é calculado, mas `get_content_type(obj)` e consultas do checker continuam no banco default. IDs de content type podem divergir entre bancos. O impacto depende do uso real do Guardian com objetos fora do banco default; a instalação atual já possui os aliases `default` e `logging`, embora o segundo não seja um banco de domínio.

### P2.18 — Parser booleano aceita tipos indevidos

**Local:** `utils/env.py:115-122`

`ast.literal_eval` permite inteiros e coleções em uma API supostamente booleana, além de rejeitar valores comuns como `false` minúsculo. Literais malformados também podem lançar `SyntaxError`, que não é tratado pelo helper atual.

### P2.20 — Chaves de campos sensíveis são validadas tarde

**Locais:** `internal_frameworks/sensitive_fields/keyring.py:8-12`, `internal_frameworks/sensitive_fields/cipher.py:9-17`

A aplicação pode iniciar saudável em produção sem `SENSITIVE_FIELD_KEYS` e falhar com HTTP 500 apenas no primeiro uso. Adicionar Django system check/deploy check.

### P2.21 — Hardening de produção incompleto

**Local:** `api/settings.py`

O default de `DEBUG` é verdadeiro, produção não falha cedo com secret key vazia e não há configuração efetiva de redirect HTTPS, HSTS, `SESSION_COOKIE_SECURE` e `CSRF_COOKIE_SECURE`. `manage.py check --deploy` confirmou os avisos.

### P2.22 — PostgreSQL e Redis publicados em todas as interfaces

**Local:** `docker-compose.yml`

No Compose local, `5432:5432` e `6379:6379` são publicados em todas as interfaces; PostgreSQL possui defaults previsíveis e Redis não exige autenticação. Para desenvolvimento local, publicar somente em `127.0.0.1` ou não publicar portas quando apenas containers consumidores precisarem delas.

### P2.23 — Pipeline de segurança não bloqueia vulnerabilidades

**Local:** `.github/workflows/security.yml:16-33`

`pip-audit` e Bandit usam `|| true`, e o Trivy usa `exit-code: "0"`. O escopo do Bandit também não inclui `internal_frameworks/`, onde estão componentes sensíveis.

No ambiente auditado, `pip-audit` encontrou 88 advisories em dez pacotes instalados: Bleach, cryptography, Django, idna, Pillow, Pygments, sqlparse, tablib, urllib3 e Werkzeug. É necessário distinguir dependências runtime/dev, atualizar o lock e tornar o job bloqueante com processo de exceção explícito.

### P2.24 — Observabilidade não recebe corretamente logs da aplicação

**Locais:** `api/logging_config.py`, `observability/config.alloy`, `docker-compose.yml`, `docker-compose.observability.yml`

O formatter emite timestamp no formato `2026-08-15 14:36:26,913`, enquanto o Alloy espera RFC3339. Além disso, o web em produção grava `/app/logs/api.jsonl`, mas esse diretório não é montado no host consumido pelo Alloy.

### P2.25 — Deploy Jenkins não verifica a nova versão

**Local:** `.ci/Jenkinsfile:49-91`.

O fluxo para/remove o container anterior antes de iniciar o novo; o health check está comentado e não existe rollback automático. `docker run -d` pode retornar sucesso mesmo que a aplicação morra logo depois.

### P2.26 — OpenAPI está incompleto

`manage.py spectacular --validate --fail-on-warn` terminou com 59 erros e 79 avisos, correspondendo a 19 erros e 39 avisos únicos. Várias `APIView`s e serializers não podem ser inferidos, e operações são omitidas do schema.

### P2.27 — Mypy está configurado, mas inutilizável

`mypy api apps internal_frameworks utils` termina imediatamente porque `apps/api/core/request_id.py` é encontrado como `core.request_id` e `apps.api.core.request_id`. Com `--explicit-package-bases`, foram encontrados 151 erros em 27 arquivos. O CI não executa mypy.

### P2.28 — Testes com migrações reais falham

Uma reprodução explícita com migrations coletou 73 testes e terminou com 69 aprovados, 12 erros de setup/teardown e 1 warning. O primeiro erro ocorre ao truncar `django_content_type`, referenciado por `auditlog_logentry`; existe também uma FK histórica para `usuario`. Erros posteriores, incluindo organização duplicada, são cascata do teardown incompleto. A execução oficial com `--nomigrations` passa e esconde o defeito.

**Correção recomendada:** migrar qualquer histórico necessário e remover formalmente a tabela/relações legadas; adicionar ao CI pelo menos um job curto que execute a cadeia real de migrations.

### P2.29 — Banco SQLite de logging não é migrado pelo fluxo normal

**Locais:** `api/settings.py:229-232,535-546`, `Makefile:59-60`.

O `drf-api-logger` de desenvolvimento grava no alias SQLite `logging`, mas `make migrate` executa somente `manage.py migrate` para o banco default. Não há router nem instrução clara que migre o alias. Em validação dinâmica, o logger assíncrono tentou gravar e gerou `OperationalError: no such table: drf_api_logs`.

**Correção recomendada:** automatizar/documentar `migrate --database=logging`, ou instalar um router/fluxo de inicialização que assegure o schema antes de habilitar o logger.

### P2.30 — Banco de teste fixo colide e pode permanecer conectado

**Locais:** `api/settings.py:223-227`, configuração de pytest.

O nome default `test_base_permission_cache` é fixo, favorecendo colisões entre execuções paralelas ou subsequentes. A suíte completa passou, mas o teardown não conseguiu remover o banco porque ainda havia uma conexão ativa. `CONN_MAX_AGE` de três horas aumenta a importância de encerrar conexões explicitamente em testes e processos auxiliares.

**Correção recomendada:** gerar um nome de banco exclusivo por execução/worker, fechar conexões criadas fora do ciclo normal do pytest e tornar o warning de teardown visível no CI.

## Achados P3 — baixa prioridade

### P3.1 — Telemetria de token não está instalada

**Locais:** `apps/api/autenticacao/middleware.py:133-145`, `api/settings.py:168`, `apps/api/autenticacao/models.py:368-372`

`UpdateTokenLastUsedMiddleware` não aparece em `MIDDLEWARE`, e `TokenMetaData.increment_usage()` não possui chamadas. `last_used` e `usage_count` permanecem incorretos.

### P3.2 — Bloco morto de modelos de reset

**Local:** `apps/api/autenticacao/models.py:393-614`

Há cerca de 220 linhas comentadas de uma implementação antiga de password reset. O histórico Git já preserva essa referência.

### P3.3 — Documentação aponta para módulo inexistente

**Local:** `docs/explanation/autenticacao.md:203-215`

O documento referencia `common.permission_cache.mutations`; o código real está em `internal_frameworks.permission_cache`.

### P3.4 — Catálogo de variáveis de ambiente está morto e divergente

**Local:** `utils/env.py:5-108`

`ENVS` não possui consumidor, diverge do `Literal` associado e não cobre várias variáveis usadas por settings.

### P3.5 — Versão mínima de Python inconsistente

`pyproject.toml` declara `requires-python >=3.10`, enquanto `.python-version`, Docker, Ruff e as diretrizes do repositório usam Python 3.12. Usar 3.12 no desenvolvimento e suportar 3.10 não é, isoladamente, uma contradição; o problema é o contrato de suporte ambíguo e o risco de Ruff/CI aceitarem sintaxe 3.12 que quebra nos runtimes declarados como suportados.

### P3.6 — CI não verifica formatação

O pipeline executa `ruff check`, mas não `ruff format --check`. No estado auditado, `apps/api/base/schema.py` seria reformatado sem que o CI falhasse.

### P3.7 — Cobertura não está tecnicamente garantida

O upload do Codecov permite continuação após erro. A documentação exige 80% para código novo, mas o workflow local não assegura sozinho que esse requisito bloqueie a integração. `codecov.yml` marca o project como informacional e define target de 80% apenas para patch.

### P3.8 — Página com nome duplicado e fora da navegação

`docs/ref/How Django can handle 100 millions of requests per day.md.md` possui extensão duplicada e não aparece na navegação do MkDocs. Outro documento de referência também está fora do nav; o MkDocs reporta isso apenas como INFO e continua passando em modo strict.

### P3.9 — MFA devolve digest interno como ID da sessão

**Locais:** `apps/api/autenticacao/views.py:509`, `apps/api/autenticacao/serializers.py:117-118,147-148`.

O fluxo MFA responde `session.id = result.instance.digest`, enquanto o login normal expõe o UUID público. O digest não revela o bearer secret; o impacto confirmado é quebra do contrato e exposição desnecessária de um identificador interno, por isso o item foi rebaixado de P2 para P3.

### P3.10 — Guardrails não acompanham async generators

**Local:** `internal_frameworks/guardrails/decorator.py:24-29`.

O decorator reconhece coroutine e generator síncrono, mas não async generator. A finalização ocorre antes da iteração real. Não foi encontrado consumidor de produção, portanto o defeito permanece latente e foi rebaixado de P2 para P3.

### P3.11 — `as_curl(save_to_file=True)` persiste credenciais inseguramente

**Local:** `utils/curl.py:10-23`.

O helper copia Authorization, cookies e body e grava `.curl` na raiz. O arquivo não está protegido pelo `.gitignore`, e o umask atual pode resultar em modo `0664`. A persistência é opt-in e não foi encontrado consumidor de produção, por isso o item foi rebaixado de P2 para P3. O nome correto do argumento é `save_to_file`, não `save`.

### P3.12 — Parser de actions dormente e documentação divergente do `BaseModelViewSet`

**Locais:**

- `apps/api/autenticacao/permissions.py:119-156`
- `apps/api/base/views.py:258-369`
- `apps/api/base/tests/test_view_mixins.py:58-121`
- `docs/reference/convencoes.md:366-380`
- `README.md:156-169`

O P1.10 original não foi confirmado: `resolve('/times/1/ativar/')` e `resolve('/v1/times/1/ativar/')` retornam `Resolver404`. `TimeViewSet` herda diretamente de `ModelViewSet`, e as actions citadas não estão alcançáveis. O parser baseado em prefixo `/v1` continua incorreto, mas está dormente no roteamento atual.

A causa subjacente é uma deriva maior: a MRO real de `BaseModelViewSet` inclui por padrão somente utilities, metadata e logs, enquanto a documentação afirma que grid, form, values, bulk create/update, clone, cache e ativar/inativar são herdados gratuitamente. `values` não existe, e alguns métodos HTTP documentados também estão incorretos. O README afirma que a action de cache é exposta por padrão, mas o mixin não integra a MRO.

**Correção recomendada:** decidir o contrato público desejado, alinhar MRO, testes e documentação e substituir parsing de path por `view.action` antes de expor qualquer dessas actions.

### P3.13 — `DJANGO_ENVIRONMENT=test` não garante configuração de teste

**Local:** `api/settings.py:23-42`.

`TESTING` depende de `pytest` já estar em `sys.modules` ou de `"test"` aparecer em `sys.argv`. Fora desses contextos, definir apenas `DJANGO_ENVIRONMENT=test` seleciona `CONFIG_ENVIRONMENT=development`, apesar do contrato sugerido pelo nome da variável.

**Correção recomendada:** incluir explicitamente o valor `test` na seleção de ambiente ou documentar que a variável, sozinha, não ativa settings de teste.

## Verificações executadas

| Verificação | Resultado |
|---|---|
| Suíte global com banco exclusivo e `--nomigrations` | **1.030 testes aprovados**, cobertura total **89%**; warning no teardown porque uma conexão ativa impediu remover o banco automaticamente |
| Execução focada com migrations reais | 73 coletados; 69 aprovados; 12 erros de setup/teardown; 1 warning |
| `ruff check .` | aprovado |
| `ruff format --check .` | somente `apps/api/base/schema.py` seria reformatado; 282 arquivos já formatados |
| `mkdocs build --strict` | aprovado |
| `makemigrations --check --dry-run` | nenhuma alteração detectada |
| `manage.py check --deploy` em produção | 63 problemas/avisos; 2 checks silenciados |
| OpenAPI strict | 59 erros e 79 avisos; 19/39 únicos |
| Mypy normal | falhou pela colisão `core.request_id`/`apps.api.core.request_id` |
| Mypy com `--explicit-package-bases` | 151 erros em 27 arquivos; 181 arquivos verificados |
| `pip-audit` | 88 vulnerabilidades conhecidas em dez pacotes instalados |
| `npm audit --omit=dev` | zero vulnerabilidades |
| Bandit | três alertas médios, todos revisados como falsos positivos descritos abaixo |
| Docker Compose config | aprovado |
| Docker build check | aprovado |
| Nginx production/development | configuração válida |
| `uv lock --check` | aprovado |
| Version check | aprovado |
| `git diff --check` | aprovado |
| Busca estática por segredos versionados | nenhum segredo confirmado |

Os subtotais intermediários da primeira rodada foram removidos porque não estavam acompanhados, neste documento, dos comandos e outputs originais necessários para auditoria independente.

## Itens investigados e descartados

- Os três alertas médios do Bandit eram falsos positivos: paths `/tmp` em testes e SQL/f-strings com identificadores fixos ou protegidos por `quote_name`.
- Não foi confirmado vazamento cross-tenant nos caminhos de API keys e metadata inspecionados, além da relação M2M descrita.
- Não foi confirmado N+1 relevante nas rotas ativas efetivamente inspecionadas; isso não constitui prova sobre rotas fora do inventário revisado.
- O fallback eventual do cache de permissões corresponde ao ADR existente; o problema confirmado é a ausência de invalidação no soft-delete em massa.
- Limitações de lookup em campos criptografados já estão documentadas e não foram tratadas como defeito adicional.
- O `uv.lock`, os arquivos Compose e as configurações Nginx são sintaticamente válidos.

## Ordem de correção sugerida

1. Fechar os bypasses de autorização: usuário/organização deletados, convites, hierarquia de papéis, M2M cross-tenant, IP interno e permissões das ações administrativas.
2. Remover bearer token da query string e revogar credenciais potencialmente expostas.
3. Corrigir invalidação de cache e todos os defeitos da rotação criptográfica, incluindo concorrência, soft-deleted, alias e contagem.
4. Adicionar testes de regressão HTTP e concorrentes para todos os P1.
5. Corrigir o perfil Docker e o hardening de produção.
6. Recuperar os gates: migrations reais, banco de logging, isolamento do banco de teste, OpenAPI, mypy, formatação e segurança bloqueante.
7. Tratar corridas de MFA, sessões, metadata, reset, recovery codes e storage.

## Estado do workspace e serviços

- Nenhum arquivo de produção foi alterado pela auditoria.
- O PostgreSQL iniciado especificamente para os testes foi parado ao final.
- O Redis que já estava rodando foi utilizado e não foi desligado.
- Este relatório é o único arquivo modificado por esta etapa de consolidação. `TODO.md` e `docs/research/2026-08-15-spec-implementation-audit.md` já estavam não rastreados antes de sua criação.

## Acompanhamento — 16 de agosto de 2026

### Reset de migrations

- P2.28: resolvido — baseline consolidada, CI e suíte local executam migrations reais.
- P2.30: resolvido — `base_test` permanece o default, overrides por
  `TEST_DATABASE_NAME` são cobertos e a suíte completa passa com nome exclusivo.
- Hardening adicional: preflight de conflitos, etapas irreversíveis identificadas,
  documentação normativa alinhada e MCP PostgreSQL read-only novamente operacional.

Comandos finais e resultados:

```bash
uv run ruff check .
uv run ruff format --check \
  apps/api/core/migration_resets.py \
  apps/api/core/management/commands/reset_migrations.py \
  apps/api/core/tests/management_commands/test_reset_migrations.py \
  api/tests/test_database_settings.py
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
uv run python manage.py makemigrations --check --dry-run
uv run mkdocs build --strict
git diff --check
```

- Todos os cinco checks terminaram com exit 0: zero violações Ruff, quatro arquivos já formatados, nenhuma migration pendente, build strict concluído e zero erros de whitespace.
- O MkDocs manteve o aviso informativo do Material sobre MkDocs 2.0 e informou oito páginas fora de `nav`; nenhum warning bloqueou o build.

```bash
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=development DJANGO_SECRET_KEY=test-secret \
uv run python manage.py reset_migrations
```

- Dry-run com exit 0: oito apps, nove arquivos marcados para remoção, uma migration manual preservada, seis etapas numeradas e duas ocorrências de `makemigrations --check --dry-run`.
- O comando foi executado sem `--apply`; status/diff permaneceram vazios e os hashes das migrations e do schema PostgreSQL permaneceram idênticos antes e depois.

```bash
TEST_DATABASE_NAME=test_reset_hardening_20260816 \
DATABASE_NAME=base DATABASE_USER=postgres DATABASE_PASSWORD=postgres \
DATABASE_HOST=127.0.0.1 DATABASE_PORT=5432 \
DJANGO_ENVIRONMENT=test DJANGO_SECRET_KEY=test-secret \
REDIS_HOST=127.0.0.1 REDIS_PORT=6379 \
uv run --group test pytest --no-cov -q
```

- Gate definitivo após a revisão: **1.089 testes aprovados em 303,96 segundos** com migrations reais, exit 0 e sem warning de conexão ativa; o banco exclusivo não existia após o teardown.
