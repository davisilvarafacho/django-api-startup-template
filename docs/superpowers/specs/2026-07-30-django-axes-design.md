# Proteção contra força bruta no login com django-axes

## Objetivo

Ativar o `django-axes` para bloquear ataques de força bruta contra o
`POST /auth/login/` e o login do `/admin/`, com uma resposta de bloqueio
adequada a uma API JSON e sem introduzir um vetor de negação de serviço por
bloqueio de conta.

## Contexto e causa

O `django-axes==8.0.0` já está declarado em `pyproject.toml:19` e presente no
`uv.lock`, mas **não está configurado**: uma busca por `axes`/`AXES` no
repositório encontra ocorrências apenas no `pyproject.toml`, no `uv.lock` e em
`docs/frameworks.md:7`. Nada no `api/settings.py`. A dependência está instalada
e inerte, e o login não tem hoje nenhuma proteção contra tentativas repetidas.

Três fatos do código atual condicionam o design:

1. **`apps/api/autenticacao/views.py:37` instancia o `AuthTokenSerializer` sem
   `context`.** O serializer do DRF chama
   `authenticate(request=self.context.get('request'), ...)`, então o `request`
   chega como `None`. O `AxesStandaloneBackend` levanta
   `AxesBackendRequestParameterRequired` nesse caso — que é um `ValueError`, não
   capturado pelo `authenticate()` do Django. Sem corrigir isso, ativar o axes
   transforma **todo login em erro 500**.

2. **O `django-ipware` não está instalado.** Em `axes/helpers.py:190-196`,
   quando `IPWARE_INSTALLED` é falso o axes resolve o IP por
   `request.META.get("REMOTE_ADDR")`. Atrás do nginx isso é o IP do container do
   proxy, **idêntico em toda request**. Qualquer chave de bloqueio que envolva
   `ip_address` degeneraria silenciosamente para uma chave só de `username`.

3. **O `AccessLog` é a única tabela do axes que cresce sem limite.** O
   `AccessAttempt` se autolimpa via `clean_expired_user_attempts` quando há
   cooloff configurado, e o `AXES_ENABLE_ACCESS_FAILURE_LOG` já é `False` por
   padrão. O `AccessLog`, por outro lado, grava um registro por login
   bem-sucedido e cresce com o volume de tráfego.

Também foi verificado que o `django-rls` não interfere: o
`RLSQuerySet._enforce_context()` só exige contexto de tenant para modelos com
`_rls_policies`, e os modelos do axes são modelos Django comuns. As escritas do
axes durante o login funcionam sem organização definida.

## Alternativas consideradas

### Chave de bloqueio

1. `AXES_LOCKOUT_PARAMETERS = ["username"]` (padrão do axes): descartada. Numa
   API de login pública, permite que qualquer pessoa bloqueie a conta de
   qualquer usuário conhecido enviando senhas erradas — negação de serviço
   trivial.
2. `["ip_address"]`: descartada. Bloqueia por IP, o que causa dano colateral
   em clientes atrás de NAT corporativo ou CGNAT.
3. `[["username", "ip_address"]]` (E lógico): **escolhida**. Só bloqueia a
   combinação usuário + origem. Lacunas aceitas conscientemente: credential
   stuffing distribuído (um IP por tentativa) e varredura de vários usuários a
   partir de um único IP.

Foi verificado que uma configuração em camadas — limite baixo para
`username + ip_address` e limite alto para `ip_address` sozinho — **não é
expressável** no axes: o `get_failure_limit(request, credentials)`
(`axes/helpers.py:429`) resolve um limite por request, não por parâmetro de
bloqueio. A escolha é genuinamente binária.

### Armazenamento

1. `AxesDatabaseHandler`: **escolhido**. Persiste `AccessAttempt` e `AccessLog`,
   o que permite investigação posterior e correlação com o `TokenMetaData`.
2. `AxesCacheHandler`: descartado. Estado volátil, perdido em restart do Redis,
   sem trilha para forense.

### Política de bloqueio

1. Defaults do axes (`FAILURE_LIMIT = 3`, `COOLOFF_TIME = None`): descartados.
   `COOLOFF_TIME = None` significa **bloqueio permanente** até intervenção no
   admin.
2. Cooloff progressivo por reincidência: descartado por ora. Exige callable com
   consulta de reincidência e estado próprio; complexidade não justificada antes
   de haver evidência de que o cooloff fixo é insuficiente.
3. Perfil equilibrado (limite 5, cooloff de 30 minutos, reset no sucesso, sem
   reinício do cooloff durante o bloqueio): **escolhido**.

O ponto decisivo é o `AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT`, cujo
padrão é `True`. Com o padrão, um cliente que faz retry automático — um app
móvel com credencial salva desatualizada, por exemplo — reinicia o cooloff a
cada tentativa e **nunca sai do bloqueio**, sem nenhuma intenção maliciosa. Com
`False`, o handler retorna cedo durante o bloqueio
(`axes/handlers/database.py:130-175`) e não atualiza o `attempt_time`, então o
prazo expira normalmente.

### Resposta de bloqueio

1. `429` com mensagem genérica e header `Retry-After`: **escolhida**.
2. `429` sem qualquer indicação de tempo: descartada. O argumento de sigilo não
   se sustenta com a chave E: quem vê o `429` só aprendeu sobre as tentativas
   que ele mesmo fez do próprio IP.
3. Manter `400` idêntico a credencial inválida: descartada. Deixa o cliente sem
   sinal para recuar e elimina o único caso em que o `429` é semanticamente
   correto.

Sem `AXES_LOCKOUT_CALLABLE` o axes só emite JSON quando a request carrega
`X-Requested-With: XMLHttpRequest` (`axes/helpers.py:447-500`); no resto dos
casos devolve texto puro. O callable é obrigatório para uma API JSON.

### Resolução de IP

1. Endurecer o `get_client_ip` existente e apontar o axes para ele:
   **escolhida**.
2. Resolvedor novo, exclusivo do axes: descartada. Dois resolvedores podem
   divergir, e aí os dois registros do mesmo login (`AccessAttempt.ip_address` e
   `TokenMetaData.ip_address`) discordam — justamente o que inviabiliza a
   correlação forense que motivou o handler de banco.
3. Instalar `django-ipware`: descartada. Adiciona dependência para reimplementar
   o que a opção 1 resolve em poucas linhas, e cria um terceiro resolvedor de IP
   no projeto.

### Retenção do `AccessLog`

1. `AXES_DISABLE_ACCESS_LOG = True`: descartada. O `TokenMetaData` já registra
   cada login com muito mais detalhe, mas ele é criado **dentro da `LoginView`
   do Knox** (`views.py:73`) — login no `/admin/` não passa por ali. Desligar o
   `AccessLog` deixaria o admin protegido contra força bruta mas **sem nenhuma
   trilha de quem conseguiu entrar**, e o `auditlog` não cobre autenticação.
2. Manter o `AccessLog` com expurgo periódico: **escolhida**. O custo é uma
   entrada no `django-celery-beat`, que o projeto já executa.

### O axes no ambiente de teste

1. `AXES_ENABLED = False` em teste, com os testes do axes ligando o recurso
   explicitamente: **escolhida**, com a condição descrita adiante.
2. Ligado sempre, com fixture `autouse` de reset: descartada. Obrigaria toda a
   suíte a pagar limpeza para proteger um punhado de testes de autenticação, e
   ainda deixaria a janela de um teste que falhe no meio sem limpar.

Sob o test client não existe `X-Forwarded-For` e o `REMOTE_ADDR` é sempre
`127.0.0.1`: **toda a suíte compartilha um único IP**, então a chave E degenera
para `username` puro. Como o estado vive no banco, dois testes que errem a senha
do mesmo usuário de fixture passam a se bloquear mutuamente, com resultado
dependente da ordem de execução.

A condição: o `AxesStandaloneBackend.authenticate` é decorado com `@toggleable`
(`axes/backends.py:26`), então `AXES_ENABLED = False` anula o backend inteiro —
**inclusive a checagem `if request is None`**. Desligar o axes por padrão sem
compensação esconderia a regressão de 500 descrita no fato 1. Por isso a suíte
**deve** incluir ao menos um teste ponta a ponta do `POST /auth/login/` com
`AXES_ENABLED=True` batendo na view real.

## Design aprovado

### Escopo

Protege o `POST /auth/login/` e o login do `/admin/`. Não inclui recuperação de
senha nem MFA — esses fluxos ficam para a branch `feat/mfa-2fa-hibp`, e este
design deixa os pontos de extensão prontos (a política e a resposta de bloqueio
são reutilizáveis por qualquer view que passe pelo `authenticate()`).

Explicitamente fora de escopo: o throttle `auth: 10/min` declarado em
`REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` e nunca referenciado (não há
`throttle_scope` nem `ScopedRateThrottle` em `apps/`); e ligar o axes ao
`risk_score` do `TokenMetaData`.

### Configuração — `api/settings.py`

O app e o middleware entram na base, não no `api/configure_enviroment.py`: a
regra do projeto proíbe app/middleware **condicional** no settings, e o axes é
incondicional (roda igual em desenvolvimento e produção).

- `LIBS_APPS` recebe `"axes"`.
- `AUTHENTICATION_BACKENDS` recebe `"axes.backends.AxesStandaloneBackend"` como
  **primeiro** item. O backend apenas verifica bloqueio e retorna `None`,
  preservando a cadeia `rules` → `ModelBackend` → `guardian`.
- `MIDDLEWARE` recebe `"axes.middleware.AxesMiddleware"` no fim, com comentário
  explicando que ele substitui a resposta na volta da request.
- `from datetime import timedelta` precisa ser adicionado ao topo do módulo.

```python
AXES_ENABLED = get_bool_from_env("AXES_ENABLED", CONFIG_ENVIRONMENT != "test")
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_FAILURE_LIMIT = int(get_env_var("AXES_FAILURE_LIMIT", 5))
AXES_COOLOFF_TIME = timedelta(minutes=int(get_env_var("AXES_COOLOFF_MINUTES", 30)))
AXES_RESET_ON_SUCCESS = True
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False
AXES_HANDLER = "axes.handlers.database.AxesDatabaseHandler"
AXES_CLIENT_IP_CALLABLE = "apps.api.autenticacao.utils.get_client_ip"
AXES_LOCKOUT_CALLABLE = "apps.api.autenticacao.handlers.resposta_de_bloqueio"
AXES_HTTP_RESPONSE_CODE = 429
AXES_ENABLE_ADMIN = True
AXES_DISABLE_ACCESS_LOG = False
AXES_ENABLE_ACCESS_FAILURE_LOG = False
```

O `AXES_ENABLE_ADMIN = True` é mantido deliberadamente: o admin do axes é a
única via de desbloqueio manual antes do fim do cooloff.

As chaves `AXES_ENABLED`, `AXES_FAILURE_LIMIT` e `AXES_COOLOFF_MINUTES` entram
em `utils/env.py`, tanto na tupla `ENVS` quanto no `Literal` `EnviromentVar` —
são duas listas duplicadas que precisam ser atualizadas juntas.

### Resolução de IP — `apps/api/autenticacao/utils.py`

O `get_client_ip` passa a honrar o `BEHIND_PROXY` (`api/settings.py:80`): atrás
do proxy usa o `X-Forwarded-For`; fora dele usa o `REMOTE_ADDR`.

Isso é seguro porque o `docker/nginx/snippets/proxy.conf:23` faz
`proxy_set_header X-Forwarded-For $remote_addr` — **sobrescreve** em vez de
acumular, então o header chega com um único valor não forjável. A
implementação atual pega o primeiro elemento da lista incondicionalmente, o que
é correto atrás desse nginx e **spoofável fora dele**: num deploy exposto
diretamente, um atacante rotaciona o header, nunca acumula tentativa e passa
pelo axes sem ser bloqueado.

Efeito colateral desejado: o `AccessAttempt.ip_address` fica idêntico ao
`TokenMetaData.ip_address`, e a geolocalização e o `check_suspicious_activity`
deixam de confiar em header forjável.

Se um load balancer confiável for colocado na frente do nginx, o procedimento é
o já documentado no próprio `proxy.conf`: declarar `set_real_ip_from` e
`real_ip_header` no server block, o que faz o `$remote_addr` passar a ser o IP
real do cliente sem alterar nada no Django.

### Resposta de bloqueio — `apps/api/autenticacao/handlers.py` (novo)

Módulo novo, seguindo o `handlers.py` já existente em `apps/api/base/`.

`resposta_de_bloqueio(request, credentials)` devolve `429` com corpo
`{"mensagem": "Muitas tentativas de login."}` — a forma usada por
`apps/api/core/status_handlers.py` — e o header `Retry-After` com os segundos
restantes.

A mensagem **não** informa o tempo em texto; a informação de prazo vai apenas no
header, para consumo do cliente.

Cálculo dos segundos restantes: monta o filtro com
`get_client_parameters(...)` em vez de codificar a chave E diretamente, para não
quebrar caso o `AXES_LOCKOUT_PARAMETERS` mude; busca o `AccessAttempt` mais
recente e calcula `attempt_time + cooloff − agora`, com piso de 1 segundo. Se
nenhuma linha for encontrada, devolve o cooloff completo, que é o valor
conservador.

O cálculo é exato porque o `attempt_time` é sobrescrito a cada falha
(`axes/handlers/database.py:214`, apesar do `auto_now_add`), mas **não** durante
o bloqueio, por causa do `AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = False`.

### Correção do bloqueador — `apps/api/autenticacao/views.py:37`

```python
serializer = AuthTokenSerializer(data=request.data, context={"request": request})
```

Pré-requisito da ativação, pelas razões do fato 1 do contexto.

### Retenção — `apps/api/core/tasks.py` e migration de dados

Task `limpar_logs_de_acesso_antigos()`, decorada com `@shared_task`, invocando
`axes_reset_logs --age 90`.

O agendamento vai numa **migration de dados idempotente**
(`update_or_create` de `CrontabSchedule` e `PeriodicTask`, com dependência de
`django_celery_beat`), porque o projeto usa o `DatabaseScheduler`: sem a
migration, a tarefa não existiria em nenhum deploy. A alternativa seria
documentar um cadastro manual, descartada por não ser reproduzível.

O `AccessAttempt` não entra no expurgo: se autolimpa pelo cooloff.

### Migrações

O axes traz as próprias migrações; nenhuma migração de modelo precisa ser
criada, e o `makemigrations --check --dry-run` da CI continua limpo. A migration
de dados do agendamento é a única adicionada por este design.

### Testes

Todos com `override_settings(AXES_ENABLED=True)`, já que o padrão em teste é
desligado:

- login válido com axes ligado, na view real, retorna 200 — este é o teste que
  captura a regressão de 500 do bloqueador;
- N falhas retornam 400 e a falha N+1 retorna 429 com `mensagem` e
  `Retry-After`;
- chave E: o mesmo usuário a partir de outro IP **não** é bloqueado, e o mesmo
  IP com outro usuário **não** é bloqueado;
- `AXES_RESET_ON_SUCCESS`: um acerto no meio da janela zera o contador;
- retry durante o bloqueio **não** estende o cooloff;
- `get_client_ip` com e sem `BEHIND_PROXY`.

### Documentação

`docs/how-to/protecao-forca-bruta.md`, ao lado de `proxy-nginx.md`: política
adotada, variáveis de ambiente, como desbloquear um usuário pelo admin, e o
aviso de que o `AXES_CLIENT_IP_CALLABLE` depende do nginx de borda para ser
confiável.

A página precisa ser registrada na seção `how-to` do `nav` do `mkdocs.yml`
(as demais how-to estão nas linhas 22-27). Os arquivos de
`docs/superpowers/specs/` ficam fora do `nav` — como os oito já existentes — e
isso não quebra o `mkdocs build --strict`, que trata página ausente do `nav`
como `INFO`, não como aviso.
