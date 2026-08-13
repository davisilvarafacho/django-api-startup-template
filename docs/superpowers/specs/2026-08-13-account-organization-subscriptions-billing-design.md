# Batch 5: ciclo de vida de contas, organizações, assinaturas e faturamento

**Data:** 13 de agosto de 2026

**Status:** design aprovado para revisão escrita

**Repositório:** `the-best-django-api-template`

## 1. Objetivo e ordem de execução

Este documento define o ciclo completo do Batch 5 no template:

1. cadastro e login local ou pelo Google;
2. verificação e troca de e-mail;
3. desativação, reativação e exclusão de conta;
4. criação, acesso e encerramento de organização;
5. catálogo versionado de planos;
6. plano gratuito por padrão, trial opcional e propostas enterprise;
7. assinatura por organização, seats, recursos, alterações e carências;
8. checkout, faturas e eventos assíncronos de gateway;
9. RLS, autorização, mensagens de erro e trilha de auditoria.

O trabalho tem uma dependência obrigatória: a biblioteca `django-checkouts`
deve ser implementada, validada e publicada antes da fase de faturamento deste
template. O contrato normativo da biblioteca está no documento
[`django-checkouts recurring lifecycle design`](https://github.com/davisilvarafacho/django-checkouts/blob/main/docs/superpowers/specs/2026-08-06-django-checkouts-recurring-lifecycle-design.md),
e seu plano está no documento
[`recurring checkout lifecycle`](https://github.com/davisilvarafacho/django-checkouts/blob/main/docs/superpowers/plans/2026-08-07-recurring-checkout-lifecycle.md).

O template deve fixar uma versão publicada da biblioteca que implemente aquele
contrato. Dependência por path ou revisão VCS pode ser usada durante o
desenvolvimento integrado, mas não substitui a versão fixada no artefato final.

## 2. Decisões fundamentais

- `django-checkouts` é stateless, autônomo, desacoplado e open source. Ele
  conhece gateways, cobranças recorrentes, checkouts, faturas, eventos e
  capabilities; não conhece organização, plano comercial, proposta ou seat.
- O template é dono de identidade, tenancy, catálogo, contratos, autorização,
  histórico e persistência financeira normalizada.
- Valores monetários são inteiros na menor unidade da moeda. Para BRL, `1099`
  representa R$ 10,99. Não se usa `float` para dinheiro.
- Modelos, classes e campos do domínio usam português, exceto termos
  consagrados como HTTP, checkout, gateway, webhook, seat, trial e onboarding.
- Nomes de arquivos são em inglês e, quando não impostos pelo framework, no
  plural. Cada app tem somente um `models.py`.
- Todos os estados persistidos usam `models.IntegerChoices`, com valores em
  intervalos de 10 e `PositiveSmallIntegerField`. A numeração acompanha a
  sequência conceitual do ciclo, mas estados nunca são comparados com `>=` ou
  `<=`; transições explícitas podem voltar no ciclo.
- Outros inteiros não negativos cujo domínio não supera 32.000 também usam
  `PositiveSmallIntegerField`. Essa é somente uma regra interna de escolha do
  field: não haverá constante, helper ou validator genérico para ela.
- Preços usam `PositiveBigIntegerField`; contadores que podem crescer sem esse
  limite usam o tipo inteiro apropriado.
- O sistema persiste fatos contratuais e financeiros. A permissão efetiva de
  acesso, os recursos e a utilização de seats são calculados em tempo de
  execução.
- Views e serializers não criam modelos de domínio diretamente. Entradas
  externas chamam os objetos de aplicação descritos neste documento.

## 3. Limites e estrutura dos apps

Haverá dois apps Django, com dependência unidirecional do faturamento para o
núcleo comercial:

```text
apps.organizacoes ---------------------> apps.assinaturas
                                               ^
                                               |
                         apps.assinaturas.subapps.faturamento
                                               |
                                               v
                                        django-checkouts
```

`apps.assinaturas` não importa o subapp de faturamento. Assim, plano gratuito,
trial local e ativação contratual enterprise continuam funcionando quando o
subapp ou um gateway não está habilitado. O subapp pode chamar as interfaces
públicas do núcleo, nunca editar seus modelos por fora delas.

Operações que podem exigir gateway usam interfaces em duas fases no núcleo:
`preparar_*` cria e devolve uma intenção tipada; `confirmar_*` ou `falhar_*`
aplica o resultado normalizado com idempotência e controle de revisão. Quando
há gateway, a view/orquestração fica no subapp e conduz essas fases. Quando não
há, o núcleo conclui somente os fluxos locais permitidos. Não se usa import
invertido, signal ou detecção implícita do subapp.

A dependência de `apps.organizacoes` para `apps.assinaturas` é de aplicação:
onboarding, convites e vínculos pedem ao núcleo comercial que valide ou aplique
regras de seat. O núcleo recebe uma ocupação tipada e não consulta diretamente
os modelos de vínculo e convite; isso evita um ciclo entre os módulos.

Os apps entram separadamente em `INSTALLED_APPS`:

```python
"apps.assinaturas"
"apps.assinaturas.subapps.faturamento"
```

Estrutura prevista:

```text
apps/assinaturas/
├── __init__.py
├── admin.py
├── apps.py
├── models.py
├── access_policies.py
├── catalogs.py
├── checks.py
├── errors.py
├── features.py
├── proposals.py
├── serializers.py
├── subscriptions.py
├── tasks.py
├── urls.py
├── views.py
├── migrations/
├── tests/
└── subapps/
    ├── __init__.py
    └── faturamento/
        ├── __init__.py
        ├── admin.py
        ├── apps.py
        ├── models.py
        ├── checks.py
        ├── checkouts.py
        ├── errors.py
        ├── events.py
        ├── invoices.py
        ├── serializers.py
        ├── tasks.py
        ├── urls.py
        ├── views.py
        ├── migrations/
        └── tests/
```

`admin.py`, `apps.py`, `models.py`, `urls.py`, `__init__.py` e `migrations/`
mantêm os nomes canônicos reconhecidos pelo Django e pelo Python. Módulos de
domínio adicionais são sempre nomeados em inglês e no plural.

## 4. Objetos de aplicação

Os objetos que cumprem o papel de facade não recebem o sufixo `Facade`:

- `Contas`: verifica se o usuário está apto ao onboarding, gerencia e-mail,
  desativação, reativação e exclusão;
- `Organizacoes`: cria e encerra organizações;
- `Vinculos`: cria proprietário, administra vínculos e convites e produz
  `OcupacaoSeats`;
- `CatalogoPlanos`: resolve plano, versão e preço vigentes;
- `Assinaturas`: cria e altera contratos, calcula seats e encerra trial ou
  assinatura;
- `Propostas`: emite, aceita, recusa, expira e ativa propostas comerciais;
- `CheckoutsCobranca`, `EventosCobranca` e `FaturasAssinatura`: integram o
  subapp de faturamento com `django-checkouts`.

`OrganizationOnboarding` conserva o nome já aprovado e permanece em
`apps.organizacoes`. Ele é o orquestrador fino do caso de uso, não um lugar para
repetir regras dos objetos acima. Sua entrada é um usuário existente, ativo,
sem exclusão agendada e com e-mail verificado. Em uma transação ele:

1. chama `Contas.validar_para_onboarding(usuario)`;
2. chama `Organizacoes.criar(...)`;
3. chama `Vinculos.criar_proprietario(...)`;
4. chama `CatalogoPlanos.obter_versao_inicial(...)`;
5. chama `Assinaturas.criar_gratuita(...)` ou `criar_trial(...)`, conforme a
   configuração inicial.

Na criação, `Organizacoes` copia o e-mail verificado do proprietário para o
novo campo `Organizacao.email_faturamento`. Depois disso, esse e-mail pertence
à organização e pode ser atualizado por proprietário ou administrador sem
acompanhar futuras trocas do e-mail pessoal.

Depois de criar a organização global, o onboarding abre
`organizacao_atual_privilegiada(organizacao.id)` dentro da transação para criar
a assinatura protegida por RLS. Ele não desabilita o guard nem depende do papel
dono das tabelas.

`Assinaturas.criar(...)` é o único método base que constrói uma assinatura. Ele
aceita todos os termos tipados e aplica constraints, snapshots e auditoria.
`criar_gratuita`, `criar_trial`, `criar_paga` e `criar_enterprise` apenas montam
os termos que mudam e delegam a `criar`. Nenhum preset instancia
`AssinaturaOrganizacao` por conta própria.

O contrato público evita dicionários sem tipo e documenta os parâmetros no
próprio código:

```python
@dataclass(frozen=True)
class OrigemVersaoPlano:
    versao_plano: VersaoPlano


@dataclass(frozen=True)
class OrigemPropostaComercial:
    proposta_comercial: PropostaComercial


OrigemAssinatura: TypeAlias = OrigemVersaoPlano | OrigemPropostaComercial


@dataclass(frozen=True)
class TermosAssinatura:
    periodicidade: Periodicidade
    moeda: str
    valor_base_centavos: int
    valor_seat_centavos: int
    seats_inclusos: int
    seats_contratados: int
    expansao_automatica_seats: bool
    recursos: ValoresRecursos
    carencia_pagamento_dias: int
    carencia_excesso_seats_dias: int


@dataclass(frozen=True)
class CriacaoAssinatura:
    organizacao: Organizacao
    origem: OrigemAssinatura
    termos: TermosAssinatura
    status: StatusAssinatura
    status_financeiro: StatusFinanceiro
    politica_trial: PoliticaTrial | None
    trial_termina_em: datetime | None
    chave_idempotencia: str
```

`Assinaturas.criar(comando: CriacaoAssinatura) -> AssinaturaOrganizacao` é a
assinatura do método base. Os presets têm entradas menores e explícitas, obtêm
os termos do catálogo/proposta e produzem esse comando. `ValoresRecursos` é a
coleção validada e tipada definida na seção 10, não um `dict[str, Any]` exposto
aos consumidores.

Esses módulos não são wrappers de CRUD. Sua interface contém casos de uso
tipados, invariantes, erros deliberados e resultados; transações, ORM, locks,
auditoria e transições ficam na implementação. Não existirão métodos genéricos
`create`, `update` ou `save` que obriguem cada chamador a reconstruir as regras.
Os testes de comportamento atravessam a mesma interface usada pelas views e
tasks. O seam externo de pagamentos é `django-checkouts`: produção usa o
adapter configurado pela biblioteca e testes do template usam seu fake, sem
expor seams internos apenas para facilitar mocks.

## 5. Identidade, cadastro e login pelo Google

### 5.1. Modelo extensível, implementação simples

O app existente `apps.api.autenticacao` recebe somente:

```python
class ProvedorIdentidade(models.IntegerChoices):
    GOOGLE = 10, _("Google")
```

`IdentidadeExterna(BaseGlobal)` possui `usuario` como `ForeignKey`, `provedor`
como `PositiveSmallIntegerField` com essas choices e `identificador` como
`CharField` para o `sub` imutável do Google.

Constraints condicionadas a `is_deleted=False` garantem unicidade de:

- `(provedor, identificador)`;
- `(usuario, provedor)`.

O modelo é genérico para permitir outros provedores no futuro. Nesta versão,
porém, existe apenas a classe concreta `AutenticacaoGoogle`, com os métodos
`autenticar`, `vincular` e `desvincular`. Não haverá classe base, registry,
adapter ou diretório de provedores antecipado.

A integração é direta com Google Identity Services e `google-auth`. Não adiciona
`django-allauth`, `social-auth-app-django` nem fluxo OAuth para recursos do
Google, pois o único objetivo é autenticar a identidade por ID token.

A identidade guarda somente provedor e `sub`. Não persiste access token,
refresh token, e-mail, nome, foto nem payload do Google. O vínculo sobrevive a
trocas do e-mail local porque sua chave é o `sub`.

### 5.2. Validação do token

O cliente envia um ID token do Google. O backend usa `google-auth` e
`google.oauth2.id_token.verify_oauth2_token` para validar assinatura, expiração,
issuer e audience contra a lista de client IDs configurada. A aplicação exige
`sub`, `email` e `email_verified=true` para o primeiro cadastro. Um ID token
inválido sempre resulta no mesmo erro público, sem revelar qual claim falhou.

Regras de fluxo:

1. Se `(GOOGLE, sub)` existe, o usuário ativo entra e recebe os mesmos
   `AuthToken` e `TokenMetaData` do login local.
2. Se o `sub` não existe e o e-mail local também não existe, o usuário é criado
   automaticamente, recebe senha inutilizável e a identidade é vinculada na
   mesma transação. Não precisa cadastrar uma conta antes.
3. Se já existe usuário com o mesmo e-mail, o sistema não vincula por
   coincidência de e-mail. A pessoa entra pelo mecanismo existente e chama o
   endpoint explícito de conexão após autenticação recente.
4. Usuário inativo, excluído ou com exclusão agendada não recebe sessão pelo
   fluxo comum de login.

O cadastro copia `given_name` e `family_name`, quando presentes, respeitando os
limites locais. A ausência desses claims não impede o cadastro e não cria nova
persistência de perfil.

### 5.3. Confiança no e-mail

O e-mail local nasce verificado somente quando o Google é fonte autoritativa:

- conta `@gmail.com` com `email_verified=true`; ou
- conta Google Workspace com `email_verified=true` e claim `hd` presente.

Não se infere Workspace pelo texto do domínio. Uma conta Google com endereço
externo e sem `hd` pode criar a conta e entrar, mas `email_verificado_em`
permanece nulo e o usuário precisa concluir a verificação local antes de criar
organização ou executar ações sensíveis.

Desvincular o Google exige autenticação recente e uma senha local utilizável;
assim o usuário nunca remove o único meio de entrada. Uma conta criada pelo
Google pode definir uma senha depois pelo fluxo seguro existente.

## 6. Verificação e troca de e-mail

`Usuario` recebe apenas `email_verificado_em`, nullable. Não haverá booleano de
verificação, `email_pendente` nem tabela de token.

Tokens assinados e com expiração carregam `usuario_id`, e-mail normalizado,
propósito e timestamp. Cada propósito possui salt distinto. Reenvios têm rate
limit no cache e a resposta pública não revela se o e-mail existe.

Na troca de e-mail:

1. a solicitação exige sessão, autenticação recente e MFA quando habilitado;
2. o novo e-mail fica somente no token assinado; o e-mail atual continua ativo;
3. a confirmação revalida token e unicidade dentro de `transaction.atomic()`;
4. e-mail e `email_verificado_em=agora` mudam atomicamente;
5. todas as sessões e API keys pessoais, inclusive a atual, são revogadas;
6. os endereços antigo e novo recebem notificação;
7. identidades externas e e-mail de faturamento das organizações não mudam.

Qualquer outro caminho suportado que altere `Usuario.email` deve deixar
`email_verificado_em` nulo. A confirmação assinada é a exceção explícita porque
comprova o novo endereço na mesma transação.

E-mail verificado é pré-condição para criar organização, aceitar convite,
conectar identidade externa e executar ações contratuais ou financeiras. Login,
solicitação/reenvio de verificação e consulta da própria conta continuam
disponíveis para que o usuário consiga regularizar o estado.

## 7. Desativação, reativação e exclusão da conta

### 7.1. Desativação reversível

Desativar define `Usuario.is_active=False`, revoga sessões, tokens, API keys
pessoais e dispositivos confiáveis e impede uso de MFA. Os vínculos são
suspensos, não removidos; vínculo suspenso continua consumindo seat. Convites
pendentes ficam indisponíveis enquanto a conta está inativa.

O único proprietário ativo de uma organização ativa não pode se desativar,
solicitar exclusão nem ser rebaixado. A operação bloqueia organização e
vínculos de proprietário com `select_for_update()` para que duas transferências
concorrentes não deixem a organização sem responsável. Antes, a pessoa deve
transferir a propriedade ou encerrar a organização.

A reativação é pública e tem duas etapas: solicitação com resposta genérica e
confirmação por token assinado enviado ao e-mail atual. Ela também cancela uma
exclusão ainda no período de arrependimento. Sessões antigas não voltam e, por
padrão, vínculos permanecem suspensos até reativação explícita por
administrador ou proprietário da organização.

### 7.2. Exclusão em duas fases, sem modelo de solicitação

`Usuario` recebe:

- `exclusao_solicitada_em`;
- `exclusao_agendada_para`.

A solicitação exige autenticação recente e MFA quando habilitado, usa sete dias
por padrão configurável e executa a desativação imediatamente. Nova solicitação
durante o prazo retorna `account.deletion_already_scheduled`, informa a data
existente em contexto não sensível e não reinicia o prazo. Portanto, o efeito
continua idempotente.

Uma task periódica e idempotente seleciona registros vencidos em lotes. Para
cada conta, abre transação, usa `select_for_update()` e:

- anonimiza o `identificador` e remove logicamente `IdentidadeExterna` sem
  conservar o `sub` em histórico ou auditlog;
- revoga credenciais, fatores MFA e dispositivos confiáveis restantes;
- troca o e-mail por `deleted-<pk>-<uuid>@invalid.local`;
- limpa nome, sobrenome, telefone e `email_verificado_em`;
- define senha inutilizável e aplica o soft delete;
- remove vínculos e cancela convites dirigidos ao antigo e-mail;
- preserva IDs técnicos e histórico financeiro sem PII.

Depois da anonimização não existe recuperação de conta. O e-mail original
fica disponível para um cadastro novo. Logs e auditoria nunca recebem token,
payload de identidade ou o e-mail anonimizado como substituto de uma limpeza de
PII.

## 8. Ciclo da organização e tenancy

### 8.1. Onboarding inicial

O padrão é criar uma assinatura gratuita ativa, não um trial. A troca para
trial exige somente configuração:

```text
ASSINATURAS_ONBOARDING_MODO=gratuito | trial
ASSINATURAS_ONBOARDING_PLANO=<codigo>
ASSINATURAS_ONBOARDING_PERIODICIDADE=mensal | anual
```

As configurações escolhem modo e códigos; preços, seats, recursos e carências
sempre vêm do catálogo versionado.

### 8.2. Resolução do tenant no middleware

O `AuthenticationMiddleware` existente resolve o principal antes do
`OrganizacaoMiddleware`. O middleware de organização passa a ser o ponto único
que abre a transação, resolve o tenant e valida seus estados antes da view:

1. rotas públicas ou marcadas `no_tenancy` não exigem organização;
2. sessão pessoal resolve o slug do `X-Organization` e procura associação do
   usuário, inclusive inativa, sem revelar organizações alheias;
3. API key deriva a organização da própria credencial e rejeita header
   divergente;
4. organização, vínculo humano e credencial precisam estar ativos;
5. depois da validação, aplica `SET LOCAL` do tenant para o RLS;
6. carrega assinatura, ocupação de seats e política de acesso;
7. anexa um contexto imutável à request.

```python
@dataclass(frozen=True)
class ContextoOrganizacao:
    organizacao: Organizacao
    vinculo: Vinculo | None       # `None` para API key
    assinatura: AssinaturaOrganizacao
    utilizacao_seats: UtilizacaoSeats
    situacao_acesso: SituacaoAcesso
```

`TenantPermission` deixa de consultar o banco e apenas exige o contexto pronto
nas rotas tenantizadas. O resolver de permissão/cache passa a distinguir
`sem_vinculo`, `vinculo_inativo` e `organizacao_inativa`; uma resposta mais
específica só é permitida quando a associação entre aquele usuário e a
organização existe. Isso conserva a proteção contra enumeração.

### 8.3. Encerramento da organização

`Organizacao` recebe `encerramento_solicitado_em` e
`encerramento_agendado_para`; não há modelo separado. Somente proprietário com
autenticação recente e MFA pode solicitar ou cancelar.

- organização gratuita encerra imediatamente;
- organização paga encerra ao fim do período já contratado;
- enquanto aguarda, continua acessível, mas não aceita novo checkout, upgrade,
  convite nem expansão de seats;
- o proprietário pode cancelar antes da efetivação;
- na efetivação, a task encerra a assinatura, inativa vínculos, revoga API
  keys tenantizadas, cancela convites e aplica soft delete à organização e aos
  dados sujeitos à política de retenção;
- usuários não são excluídos.

A operação é idempotente, auditada e não oferece reabertura depois de
efetivada nesta versão.

### 8.4. Organizações existentes

O rollout não pode ativar a exigência do middleware antes de inicializar as
organizações que já existem. Depois das migrations de schema:

1. executa `sync_plans --apply` para criar o catálogo gratuito;
2. executa `initialize_subscriptions` em dry-run e depois com `--apply`;
3. o comando percorre organizações sem contrato corrente, abre o contexto RLS
   de cada uma e chama `Assinaturas.criar_gratuita`;
4. repetir o comando não cria outro contrato;
5. um check de implantação exige zero organizações ativas sem assinatura antes
   de habilitar a aplicação da política no middleware.

Novos onboardings sempre criam organização, proprietário e assinatura na mesma
unidade transacional, portanto a exceção só existe durante o rollout controlado.

## 9. Catálogo de planos

### 9.1. Modelos globais e imutáveis

`Plano` é a identidade estável do produto: código, nome, descrição,
visibilidade e atividade. `VersaoPlano` é uma versão concreta e imutável do
catálogo, nunca uma versão por cliente. `PrecoPlano` contém os valores de uma
periodicidade e moeda.

Campos essenciais:

| Modelo | Campos de domínio |
|---|---|
| `Plano` | `codigo`, `nome`, `descricao`, `visivel`, `is_active` |
| `VersaoPlano` | `plano`, `numero`, `atual`, `seats_inclusos`, `limite_seats_trial`, `duracao_trial_dias`, `carencia_pagamento_dias`, `carencia_excesso_seats_dias`, `expansao_automatica_seats`, `recursos`, `publicada_em` |
| `PrecoPlano` | `versao_plano`, `periodicidade`, `moeda`, `valor_base_centavos`, `valor_seat_centavos` |

`limite_seats_trial` nulo informa que a versão não admite trial. O limite do
trial é uma capacidade temporária, separada dos seats incluídos no preço.
O recurso tipado `PAPEIS_ISENTOS_SEAT` é vazio por padrão e só pode receber
valores válidos de `Papel`; ele permite termos enterprise sem cobrança de
determinados papéis e sem criar uma coluna específica.

Constraints garantem:

- código de `Plano` único entre registros não excluídos;
- `(plano, numero)` único;
- no máximo uma `VersaoPlano.atual=True` por plano;
- `(versao_plano, periodicidade, moeda)` único;
- moeda ISO 4217 em três letras maiús;
- valores monetários não negativos.

Os termos de uma versão publicada e seus preços nunca são editados. Somente os
marcadores operacionais `atual` e `is_active` podem mudar. Uma mudança comercial
gera outro número de versão. Desativar uma versão impede novas contratações,
mas não altera assinaturas existentes.

### 9.2. Definições em código e `sync_plans`

As estruturas tipadas declaradas em `catalogs.py` se chamam
`DefinicaoPlano`, `DefinicaoVersaoPlano` e `DefinicaoPrecoPlano`. Não se usa o
termo blueprint. Cada definição informa explicitamente código, número da
versão, valores, recursos e qual versão deve ser atual.

O comando `sync_plans`:

1. opera em dry-run por padrão e só escreve com `--apply`;
2. cria `Plano`, `VersaoPlano` e `PrecoPlano` ausentes que estejam declarados;
3. marca a versão declarada como atual e retira essa marca da anterior;
4. é idempotente para a mesma entrada;
5. se encontra o mesmo `(plano, numero)` com termos diferentes, falha e manda
   declarar o próximo número, em vez de mutar histórico;
6. não cria versões implícitas, não altera assinaturas e não chama gateway nem
   `django-checkouts`.

As definições são o bootstrap reproduzível; o banco é a fonte operacional para
a aplicação. Referências externas de preço pertencem ao faturamento e são
provisionadas por operação administrativa separada.

## 10. Recursos de plano tipados e calculados

Recursos não viram campos fixos. `features.py` declara objetos genéricos:

```python
T = TypeVar("T")

@dataclass(frozen=True)
class RecursoPlano(Generic[T]):
    chave: str
    titulo: str
    descricao: str
    tipo: type[T]
    padrao: T
    unidade: str | None
    exemplo: T
```

Cada recurso também fornece validação tipada. Um `CatalogoRecursos` rejeita
chaves duplicadas, valida snapshots e gera JSON Schema, exemplos e documentação
para OpenAPI. Django system checks falham para declarações inconsistentes.

Consumidores nunca consultam strings soltas:

```python
limite: int = recursos.obter(QUANTIDADE_PROJETOS)
```

`VersaoPlano`, `PropostaComercial` e `AssinaturaOrganizacao` persistem um JSON
validado. Uma nova assinatura materializa todos os recursos conhecidos no
snapshot. Se uma assinatura antiga não tiver um recurso adicionado depois, a
resolução retorna o default tipado declarado em código. O valor efetivo é
calculado em tempo de execução e não usa propriedades de model que disparem
queries ocultas.

## 11. Contrato da organização

### 11.1. `AssinaturaOrganizacao`

Cada organização tem no máximo um contrato corrente. O registro guarda os
termos completos aplicáveis ao cliente, mesmo quando vieram do catálogo:

| Grupo | Campos principais |
|---|---|
| Origem | `versao_plano` xor `proposta_comercial` |
| Estado | `status`, `status_financeiro`, `revisao` |
| Preço | `periodicidade`, `moeda`, `valor_base_centavos`, `valor_seat_centavos` |
| Seats | `seats_inclusos`, `seats_contratados`, `expansao_automatica_seats`; isenções vêm de `recursos` |
| Recursos | `recursos` validado e completo |
| Trial | `politica_trial`, `trial_iniciado_em`, `trial_termina_em` |
| Período | `periodo_atual_iniciado_em`, `periodo_atual_termina_em` |
| Carências | dias copiados, início e fim independentes para pagamento e excesso de seats |
| Encerramento | `cancelamento_agendado_para`, `encerrada_em`, `motivo_encerramento` |

O snapshot impede que alterações futuras de `VersaoPlano` ou
`PropostaComercial` mudem contratos existentes. A assinatura aponta exatamente
para uma origem: versão de catálogo nos planos comuns ou proposta comercial no
enterprise.

```python
class StatusAssinatura(models.IntegerChoices):
    PENDENTE = 10, _("Pendente")
    EM_TRIAL = 20, _("Em trial")
    ATIVA = 30, _("Ativa")
    ENCERRADA = 40, _("Encerrada")


class StatusFinanceiro(models.IntegerChoices):
    ISENTO = 10, _("Isento")
    PENDENTE = 20, _("Pendente")
    REGULAR = 30, _("Regular")
    INADIMPLENTE = 40, _("Inadimplente")
    IRRECUPERAVEL = 50, _("Irrecuperável")
```

Plano gratuito nasce `ATIVA/ISENTO`; checkout ainda não confirmado nasce
`PENDENTE/PENDENTE`; trial nasce `EM_TRIAL/ISENTO`; pagamento confirmado muda
para `ATIVA/REGULAR`. Cancelamento agendado permanece `ATIVA` até a data efetiva
e então muda para `ENCERRADA`.

Uma constraint parcial limita a uma assinatura `PENDENTE`, `EM_TRIAL` ou
`ATIVA` por organização. Outra constraint exige data e motivo em
`ENCERRADA` e os proíbe nos demais estados. Uma terceira garante o xor da
origem. Todas incluem `organizacao` quando a unicidade é tenant-específica.

O mesmo registro corrente recebe snapshots confirmados e incrementa `revisao`.
Uma nova `AssinaturaOrganizacao` só nasce para um novo ciclo contratual: uma
organização encerrada que recontrata, uma migração que o gateway exija como
novo contrato ou a aceitação de uma proposta que substitua formalmente o
contrato anterior.

### 11.2. Preço calculado

Para uma periodicidade, o total é:

`Periodicidade` usa `MENSAL=10` e `ANUAL=20`.

```text
seats_cobrados = max(0, seats_contratados - seats_inclusos)
total_centavos = valor_base_centavos + seats_cobrados * valor_seat_centavos
```

Um preço anual representa o período anual inteiro, não um equivalente mensal.
Somente seats acima da franquia são cobrados.

### 11.3. `AlteracaoAssinatura`

Toda mudança relevante cria antes um registro cujo pedido, snapshots, chave de
idempotência e solicitante são imutáveis. Somente status e metadados de
processamento evoluem. Ele guarda tipo, momento de aplicação, snapshot anterior,
snapshot pretendido, timestamps, evento de gateway e falha normalizada.

```python
class StatusAlteracaoAssinatura(models.IntegerChoices):
    SOLICITADA = 10, _("Solicitada")
    AGUARDANDO_GATEWAY = 20, _("Aguardando gateway")
    CONFIRMADA = 30, _("Confirmada")
    FALHOU = 40, _("Falhou")
    CANCELADA = 50, _("Cancelada")
```

O snapshot da assinatura só muda depois da confirmação local ou do gateway.
Repetir a mesma chave retorna a alteração existente. Eventos atrasados podem
completar o histórico, mas não sobrescrevem uma revisão mais nova.

Padrões de aplicação:

| Alteração | Momento padrão |
|---|---|
| upgrade de plano | imediato, com prorrata quando suportada |
| aumento absoluto de seats | imediato |
| downgrade de plano | próximo ciclo |
| redução de seats | próximo ciclo; capacidade solicitada deve cobrir o consumo efetivo |
| mensal para anual | próximo ciclo |
| fallback de trial | imediato para gratuito |

Um operador interno pode escolher outro momento somente por fluxo auditado. A
redução não remove membros; convites que não couberem na capacidade futura
devem ser cancelados ou permanecer impossibilitados de aceitação.

## 12. Proposta comercial enterprise

`PropostaComercial` representa negociação específica de uma organização. Ela
pode referenciar um plano como ponto de partida, mas guarda seus próprios
valores, moeda, periodicidade, seats incluídos e contratados, recursos,
carências, validade e modo de ativação. Criar ou enviar proposta não concede
acesso.

```python
class StatusPropostaComercial(models.IntegerChoices):
    RASCUNHO = 10, _("Rascunho")
    ENVIADA = 20, _("Enviada")
    ACEITA = 30, _("Aceita")
    ATIVADA = 40, _("Ativada")
    RECUSADA = 50, _("Recusada")
    EXPIRADA = 60, _("Expirada")
    CANCELADA = 70, _("Cancelada")


class ModoAtivacaoProposta(models.IntegerChoices):
    PAGAMENTO = 10, _("Pagamento")
    CONTRATUAL = 20, _("Contratual")
```

Somente proprietário aceita. Aceite é transacional, revalida organização,
vigência, status e revisão esperada. No modo `PAGAMENTO`, ele marca a proposta
como `ACEITA`, abre um checkout e aguarda confirmação. No modo `CONTRATUAL`,
somente operador interno autorizado no Django Admin pode ativá-la, registrando
ator, data e justificativa.

Ativação por pagamento confirmado ou por contrato chama
`Assinaturas.criar_enterprise`, encerra o contrato corrente e cria uma nova
assinatura originada exclusivamente da proposta, na mesma transação. Somente
então a proposta passa a `ATIVADA` e concede acesso. Uma proposta `ACEITA` cujo
checkout falhou pode receber nova tentativa enquanto continuar válida.

## 13. Seats

### 13.1. Fatos e cálculo em tempo de execução

Somente `seats_contratados` é persistido como capacidade do contrato. A
ocupação vem de `apps.organizacoes` em uma consulta agregada explícita:

- vínculo ativo consome um seat;
- vínculo suspenso consome um seat;
- papel explicitamente isento nos termos não consome;
- convite pendente e não expirado reserva um seat, mas não é cobrado;
- vínculo removido libera o seat;
- convite aceito, cancelado ou expirado deixa de reservar;
- API key e conta de serviço não consomem seat.

```python
@dataclass(frozen=True)
class OcupacaoSeats:
    consumidos: int
    reservados: int


@dataclass(frozen=True)
class UtilizacaoSeats:
    contratados: int
    consumidos: int
    reservados: int
    comprometidos: int
    disponiveis: int
    excesso_real: int
    excesso_comprometido: int
```

```text
comprometidos = consumidos + reservados
disponiveis = max(contratados - comprometidos, 0)
excesso_real = max(consumidos - contratados, 0)
excesso_comprometido = max(comprometidos - contratados, 0)
```

`Vinculos` calcula `OcupacaoSeats`; `Assinaturas.calcular_utilizacao(...)`
produz o segundo objeto sem fazer query. O chamador deve pedir esses dados
explicitamente e reutilizá-los, evitando propriedades com N+1.

### 13.2. Concorrência e excesso

Criar convite e aceitar convite bloqueiam a assinatura corrente com
`select_for_update()`, recalculam a ocupação na mesma transação e impedem que
duas requisições conquistem o último seat. Por padrão, convite ou aceite que
geraria excesso comprometido falha com erro claro.

Se `expansao_automatica_seats` estiver ativa nos termos, o caso de uso cria uma
`AlteracaoAssinatura` idempotente para a quantidade absoluta necessária. A
expansão só libera a operação depois da confirmação exigida pelo contrato e fica
auditada.

Excesso real não nasce de uma nova operação comum. Ele pode nascer de downgrade,
redução agendada, fallback de trial ou correção administrativa. Ninguém é
removido automaticamente; inicia-se a carência de excesso e bloqueiam-se novos
convites e aceites.

## 14. Trial, carências e acesso

### 14.1. Trial

Trial é um estado temporário de uma versão paga, não um plano separado:

```python
class PoliticaTrial(models.IntegerChoices):
    SEM_FORMA_PAGAMENTO = 10, _("Sem forma de pagamento")
    COM_FORMA_PAGAMENTO = 20, _("Com forma de pagamento")
```

No modo local, nenhuma assinatura remota é criada até a conversão. No modo com
forma de pagamento, o subapp cria a relação remota se a capability do gateway
permitir. Durante o trial, a capacidade contratada efetiva é
`limite_seats_trial` e não há cobrança.

`Assinaturas.encerrar_trial(...)` é central e idempotente. Uma task trata trials
locais e um evento confirmado pode tratar trials remotos. Para converter, os
seats pagos precisam cobrir o consumo real. Se não houver pagamento válido ou a
primeira cobrança falhar, o fallback para o plano gratuito é imediato. A
primeira cobrança de trial não abre carência financeira; se o plano gratuito
não comportar os membros existentes, abre apenas a carência de excesso de
seats. O fallback cria uma `AlteracaoAssinatura`, confirma um novo snapshot no
mesmo registro de assinatura e incrementa sua revisão; não cria uma assinatura
nova.

### 14.2. Duas carências independentes

- Falha de renovação de contrato antes regular abre carência financeira de sete
  dias por padrão. Novas falhas não reiniciam a data original. Pagamento
  confirmado regulariza e limpa a carência.
- Excesso real de seats abre carência de seats de sete dias por padrão. Reduzir
  ocupação ou ampliar o contrato encerra a carência.

As durações são copiadas do catálogo ou proposta para a assinatura. Quando as
duas causas coexistem, ambas aparecem no resultado.

### 14.3. Política calculada

Não existe modelo `AcessoAssinatura`. Existe um value object calculado:

```python
@dataclass(frozen=True)
class SituacaoAcesso:
    status: StatusAcesso
    motivos: tuple[MotivoRestricao, ...]
    regularizar_ate: datetime | None
```

A interface única do módulo é
`PoliticaAcessoAssinatura.avaliar(assinatura: AssinaturaOrganizacao, utilizacao: UtilizacaoSeats, agora: datetime) -> SituacaoAcesso`.

`StatusAcesso` possui `LIBERADO=10`, `EM_CARENCIA=20` e `RESTRITO=30`. Os motivos
incluem `payment_grace_period_expired` e
`seat_overage_grace_period_expired`. Durante a carência o acesso normal continua
e a API expõe aviso e prazo. Depois do prazo:

- membros comuns recebem 403 em rotas tenantizadas;
- proprietário e administrador acessam somente views ou actions marcadas com
  `@regularizacao_assinatura`;
- nenhuma allowlist baseada em texto de URL é aceita;
- o marcador permite apenas visualizar e regularizar, nunca ampliar poderes do
  papel.

## 15. Faturamento e `django-checkouts`

### 15.1. Regra de integração

Somente os módulos do subapp de faturamento importam `django-checkouts`. Views
não chamam a biblioteca diretamente. Eles constroem DTOs tipados, consultam
capabilities, escolhem `CatalogPrice` ou `InlinePrice` e traduzem resultados e
erros para o domínio local. As views de operações pagas pertencem ao subapp,
mesmo quando sua URL pública fica sob `/assinatura/`; elas chamam as interfaces
`preparar_*`, `confirmar_*` e `falhar_*` do núcleo.

O fluxo principal não importa Stripe, Asaas ou qualquer SDK de gateway. A
variante configurada é resolvida pelo registry de `django-checkouts`; as
implementações de cada gateway pertencem à biblioteca, dentro de seus próprios
módulos, e chamam o código comum de gateway definido por ela.

`provider_options` da biblioteca não vira JSON livre no núcleo comercial. Cada
operação do faturamento constrói as opções tipadas da variante a partir de
configuração interna e dados normalizados. O `raw` devolvido pelos DTOs pode ser
usado para diagnóstico imediato dentro da integração, mas não governa estado e
não é persistido nesta versão.

### 15.2. Modelos do subapp

#### `AssinaturaGateway`

Relação one-to-one com `AssinaturaOrganizacao`, variante e identificador
externo da assinatura. Essa separação permite que contratos gratuitos ou
enterprise contratuais não tenham campos artificiais de gateway.

Ela também é um índice interno de roteamento anterior ao tenant: declara
`organizacao` explicitamente, herda `BaseGlobal`, não é exposta por API e exige
unicidade global de `(variante, identificador_externo)`. Esse é o mesmo motivo
arquitetural pelo qual `Vinculo` pertence ao control plane: exigir contexto RLS
para descobrir qual contexto aplicar criaria um impasse. Depois de resolver o
ID externo, todo acesso à assinatura acontece sob o RLS da organização.
O módulo de criação valida e testa a invariância
`assinatura.organizacao_id == organizacao_id`. Um mapeamento encerrado fica
inativo, mas não é apagado enquanto eventos atrasados ainda puderem chegar.

#### `ReferenciaPrecoGateway`

Relaciona `PrecoPlano`, variante, componente e ID externo. Componentes usam
`BASE=10` e `SEAT=20`. Uma constraint parcial garante apenas uma referência
ativa por `(preco_plano, variante, componente)`.

Se a variante suporta preço inline, o faturamento pode construir `InlinePrice`.
Se não suporta, as referências obrigatórias precisam existir antes de qualquer
I/O externo. Para um checkout, envia item base com quantidade um quando seu
valor for positivo e item de seat com quantidade
`max(seats_contratados - seats_inclusos, 0)` quando positiva.

#### `CheckoutCobranca`

Guarda organização, assinatura, finalidade, status, chave de idempotência,
variante, ID externo nullable, URL, valor e moeda esperados e timestamps. Pode
referenciar uma `AlteracaoAssinatura` ou `PropostaComercial`, conforme a
finalidade. `FinalidadeCheckout` usa `CONTRATACAO=10`, `ALTERACAO=20`,
`PROPOSTA=30` e `FORMA_PAGAMENTO=40`. Constraints garantem a combinação
coerente:

- contratação: assinatura pendente, sem alteração nem proposta aceita anterior;
- alteração: exatamente uma alteração;
- proposta: exatamente uma proposta aceita e a assinatura corrente usada como
  contexto, sem alteração;
- forma de pagamento: assinatura corrente, sem alteração nem proposta e valor
  esperado igual a zero.

Cada tentativa cria um checkout local distinto. A referência enviada ao
gateway é gerada a partir do ID local de `CheckoutCobranca`, mas carrega também
o ID da organização e uma assinatura criptográfica. Assim, o webhook consegue
validar a referência e estabelecer o tenant antes de consultar o checkout sob
RLS. A referência não concede acesso e nenhuma parte recebida é usada sem
verificar a assinatura. Ela tem formato versionado e usa `django.core.signing`
com salt exclusivo; não expira como uma credencial porque eventos podem chegar
atrasados e a rota ainda revalida todos os estados. Rotação de chave respeita
`SECRET_KEY_FALLBACKS`.

A chave idempotente é única por `(organizacao, chave_idempotencia)`. Repetir a
mesma chave devolve o registro existente; um retry depois de estado terminal
usa outra chave e cria outro registro. O ID externo, quando presente, é único
por variante. O status segue `CRIADO=10`,
`AGUARDANDO_GATEWAY=20`, `ABERTO=30`, `CONCLUIDO=40`, `EXPIRADO=50`,
`CANCELADO=60`, `FALHOU=70`.

#### `FaturaAssinatura`

Representa uma invoice normalizada, não uma nota fiscal. Guarda organização,
assinatura, variante, ID externo, status e motivo, subtotal, desconto, imposto,
total, moeda, período, vencimento, pagamento, próxima tentativa, número de
tentativas, URL hospedada e timestamps. A unicidade é
`(organizacao, variante, identificador_externo)`.

Os estados são `ABERTA=10`, `PAGA=20`, `VENCIDA=30`, `IRRECUPERAVEL=40` e
`ANULADA=50`. O código trata transições nominalmente; uma fatura vencida ainda
pode ser paga.

#### `EventoCobranca`

Existe uma única tabela para evento recebido e processado. Ela guarda variante,
ID externo do evento, tipo normalizado, organização nullable, IDs externos para
roteamento, status, tentativas, payload normalizado, hash do payload, erro e
timestamps. Não guarda o body bruto. O payload persistido é uma allowlist dos
campos normalizados necessários para reconciliação; campos desconhecidos e PII
desnecessária são descartados antes do `JSONField`.

A idempotência externa é global em `(variante, identificador_evento)`, sem
`organizacao`: o evento chega antes de o tenant ser confiavelmente conhecido e
um ID do mesmo gateway não pode produzir efeitos em duas organizações. A coluna
`organizacao` tem índice próprio para isolamento e consultas tenantizadas.

Como `Base` exige organização não nula, este modelo é a exceção explícita:
combina `BaseGlobal` com `RLSModel`, declara o FK nullable e policies próprias.
As expressões de `USING` e `WITH CHECK` permitem:

- linha roteada somente quando `organizacao_id` coincide com `rls.tenant_id`;
- linha ainda não roteada somente sob contexto operacional restrito com
  `tenant_id=0` e `rls.billing_ingress=1`;
- o contexto de ingresso pode inserir uma linha nula e atualizar essa linha uma
  única vez para a organização validada, mas não pode selecionar ou alterar
  linhas já roteadas.

`billing_ingress` entra em `DJANGO_RLS.REGISTERED_CONTEXT_KEYS` para ser limpo
em toda troca de conexão/request. Apenas o módulo de ingestão e a recuperação
operacional abrem esse contexto. Testes executados com papel PostgreSQL sujeito
a RLS devem provar SELECT, INSERT e UPDATE; a implementação não pode depender do
bypass do dono da tabela. Depois do roteamento, a linha deixa de ser visível ao
contexto de ingresso e passa a ser visível somente ao tenant correspondente.

O status do evento segue `RECEBIDO=10`, `ROTEADO=20`, `PROCESSANDO=30`,
`PROCESSADO=40`, `IGNORADO=50` e `FALHOU=60`. Um check impede marcar como
processado um tipo que exige tenant enquanto `organizacao` for nula. O modelo
mantém separadamente `tentativas_roteamento`, `tentativas_processamento` e
`proxima_tentativa_em`; esses contadores usam `PositiveSmallIntegerField`.

## 16. Checkout e eventos assíncronos

### 16.1. Checkout

O endpoint chama `CheckoutsCobranca.criar(...)` com finalidade e snapshot
esperado. O objeto usa três fases para não manter transação ou lock durante I/O
de rede:

1. em uma transação curta, bloqueia assinatura e alteração/proposta, valida
   papel, revisão, status, idempotência, capability e preços e cria o checkout
   local em `AGUARDANDO_GATEWAY`;
2. fora da transação, chama `django-checkouts` com a chave idempotente e a
   referência local assinada;
3. em outra transação curta, bloqueia o checkout, confirma que a tentativa
   ainda é vigente, persiste somente a resposta normalizada e devolve a URL.

Falha incerta depois do I/O não cria outra tentativa automaticamente com nova
chave. A reconciliação consulta o checkout pela referência/ID antes de permitir
retry, evitando dupla assinatura remota.

### 16.2. Webhook

O endpoint público é específico por variante. O caminho síncrono:

1. captura body bruto e headers;
2. usa `django-checkouts` para verificar assinatura antes de persistir;
3. rejeita assinatura inválida sem criar evento;
4. normaliza o evento;
5. sob o contexto RLS de ingresso, cria ou encontra
   `(variante, identificador_evento)`;
6. roteia por referência local assinada ou por
   `AssinaturaGateway.identificador_externo`;
7. agenda processamento com `transaction.on_commit()`;
8. responde sucesso também para duplicata válida.

Uma duplicata com o mesmo hash não agenda efeitos repetidos. O mesmo ID externo
com hash diferente é rejeitado, gera alerta de segurança e nunca sobrescreve o
evento original.

O worker Celery também separa banco e rede. Primeiro reivindica o evento em uma
transação curta com `select_for_update()` e trata `PROCESSADO` e `IGNORADO` como
no-op. Depois recupera o recurso remoto sem lock aberto. Por fim, abre nova
transação, bloqueia evento e assinatura, confirma a revisão esperada e aplica o
resultado. Falhas incrementam tentativas, usam backoff exponencial e fazem no
máximo oito tentativas automáticas. Depois o evento fica `FALHOU`, com erro
normalizado, e permite retry operacional. Uma task de recuperação reenfileira
eventos persistidos que não foram agendados ou cujo processamento ficou
interrompido. Evento reconhecido ainda sem tenant permanece pendente de
roteamento e nunca executa efeito financeiro.

Eventos são gatilhos, não fonte final de verdade:

- evento de checkout recupera o checkout atual;
- evento de assinatura recupera a assinatura remota atual;
- evento de fatura recupera primeiro a invoice e depois a assinatura;
- a resposta normalizada atualiza modelos locais pelas interfaces do núcleo;
- evento atrasado completa histórico, mas não regride revisão nem estado;
- tipo desconhecido é persistido como `IGNORADO`, não como falha;
- tipo conhecido com status normalizado desconhecido é falha de protocolo e
  não toma decisão financeira.

A reconciliação periódica usa o mesmo registro e o mesmo processador dos
webhooks. O sistema oferece processamento idempotente com entrega pelo menos
uma vez; não promete exactly-once distribuído.

## 17. Autorização

| Papel/principal | Capacidades financeiras |
|---|---|
| Proprietário | contratar, alterar plano/ciclo/seats, aceitar proposta, cancelar assinatura e encerrar organização |
| Administrador | consultar assinatura, utilização, faturas e checkouts; atualizar dados de cobrança e forma de pagamento |
| Gestor, membro, visualizador | nenhuma rota financeira |
| API key tenantizada | nenhuma rota financeira por padrão, independentemente de scopes genéricos |
| Operador interno | ativação enterprise contratual somente pelo Django Admin, com permissão, MFA e auditoria |

Administrador não altera o contrato principal nesta versão. Marcadores de
regularização e scopes nunca superam essa matriz de papel.

## 18. Endpoints

### 18.1. Conta e autenticação

| Método | Rota | Autenticação |
|---|---|---|
| POST | `/auth/email/verify/` | pública, token assinado |
| POST | `/auth/email/verification/resend/` | pública, resposta genérica |
| POST | `/auth/google/` | pública |
| POST | `/auth/google/connect/` | sessão e autenticação recente |
| DELETE | `/auth/google/disconnect/` | sessão e autenticação recente |
| POST | `/account/email/change/` | sessão, recente e MFA |
| POST | `/account/email/change/confirm/` | pública, token assinado |
| POST | `/account/deactivate/` | sessão, recente e MFA |
| POST | `/account/reactivation/` | pública, resposta genérica |
| POST | `/account/reactivation/confirm/` | pública, token assinado |
| POST | `/account/deletion/` | sessão, recente e MFA |

Como solicitar exclusão desativa a conta e revoga sua sessão, o cancelamento
não usa um `DELETE` autenticado inutilizável. A confirmação pública de
reativação é o único caminho de cancelamento durante o prazo.

### 18.2. Organização, assinatura e faturamento

| Método | Rota | Regra principal |
|---|---|---|
| POST | `/organizacoes/` | onboarding; e-mail verificado |
| POST | `/organizacoes/{id}/encerramento/` | proprietário |
| DELETE | `/organizacoes/{id}/encerramento/` | proprietário, antes da efetivação |
| GET | `/planos/` | catálogo contratável, sem tenant |
| GET | `/assinatura/` | proprietário ou administrador |
| GET | `/assinatura/recursos/` | contexto tenant; valores efetivos tipados |
| GET | `/assinatura/utilizacao-seats/` | proprietário ou administrador |
| POST | `/assinatura/checkouts/` | proprietário |
| POST | `/assinatura/alteracoes/` | proprietário |
| POST | `/assinatura/cancelamento/` | proprietário |
| DELETE | `/assinatura/cancelamento/` | proprietário, antes da efetivação |
| POST | `/assinatura/propostas/{id}/aceitar/` | proprietário |
| GET | `/faturamento/faturas/` | proprietário ou administrador |
| GET | `/faturamento/checkouts/` | proprietário ou administrador |
| POST | `/faturamento/forma-pagamento/checkouts/` | proprietário ou administrador |
| POST | `/faturamento/webhooks/{variante}/` | pública, assinatura do gateway obrigatória |

As actions de regularização recebem o marcador declarativo. Rotas públicas e
sem tenancy entram nos registries/decorators existentes, nunca em exceções
textuais escondidas no middleware.

## 19. Erros públicos

Todos os erros usam o envelope existente `{"errors": [...], "request_id": ...}`
e membros de `models.TextChoices` registrados. Mensagens são claras, estáveis e
não carregam segredo ou payload de gateway.

| HTTP | Código | Mensagem padrão |
|---:|---|---|
| 403 | `account.email_not_verified` | Verifique seu e-mail para continuar. |
| 409 | `account.email_already_verified` | Este e-mail já foi verificado. |
| 400 | `account.email_verification_invalid` | O link de verificação é inválido ou expirou. |
| 409 | `account.email_already_in_use` | Este e-mail já está em uso. |
| 401 | `auth.google_token_invalid` | Não foi possível validar a identidade do Google. |
| 409 | `account.external_identity_conflict` | Esta identidade externa já está vinculada a outra conta. |
| 409 | `account.external_identity_last_login` | Defina uma senha antes de desvincular seu único meio de acesso. |
| 409 | `account.owner_transfer_required` | Transfira a propriedade ou encerre suas organizações antes de continuar. |
| 409 | `account.deletion_already_scheduled` | A exclusão desta conta já está agendada. |
| 403 | `organizations.membership_required` | Você não possui vínculo com esta organização. |
| 403 | `organizations.membership_inactive` | Seu vínculo com esta organização está inativo. |
| 403 | `organizations.organization_inactive` | Esta organização está inativa. |
| 503 | `billing.subscription_required` | A assinatura desta organização ainda não foi inicializada. |
| 409 | `billing.subscription_conflict` | A assinatura mudou. Atualize os dados e tente novamente. |
| 409 | `billing.checkout_pending` | Já existe um checkout pendente para esta operação. |
| 422 | `billing.checkout_unavailable` | Este plano não pode ser contratado com o gateway configurado. |
| 409 | `billing.proposal_invalid` | A proposta não está disponível para aceite. |
| 409 | `billing.seat_limit_reached` | Não há seats disponíveis. Aumente a quantidade contratada ou libere um seat. |
| 403 | `billing.organization_restricted` | O acesso à organização está restrito. Regularize a assinatura para continuar. |
| 503 | `billing.gateway_unavailable` | O serviço de pagamento está temporariamente indisponível. |
| 400 | `billing.webhook_signature_invalid` | Assinatura do webhook inválida. |

`billing.organization_restricted` inclui em `context` somente `reasons` e
`regularize_by`. `billing.seat_limit_reached` pode incluir totais não sensíveis
de contratados, consumidos e reservados. Erros de gateway são mapeados para
códigos locais; somente classe do erro, operação e IDs não sensíveis entram em
telemetria interna. Request, response, token, headers e `raw` do gateway não são
logados.

## 20. RLS, auditoria e dados sensíveis

- `Plano`, `VersaoPlano`, `PrecoPlano` e `IdentidadeExterna` são globais por
  necessidade de bootstrap/identidade e herdam `BaseGlobal`.
- `AssinaturaOrganizacao`, `AlteracaoAssinatura`, `PropostaComercial`,
  `CheckoutCobranca` e `FaturaAssinatura` herdam `Base` e recebem organização e
  `TenantPolicy` por padrão.
- `ReferenciaPrecoGateway` é catálogo operacional global e
  `AssinaturaGateway` é índice de roteamento do control plane; ambos herdam
  `BaseGlobal`, não são expostos diretamente e todo uso posterior dos dados
  contratuais ocorre sob o tenant resolvido.
- `EventoCobranca` usa a policy nullable especial definida na seção 15.2.
- Workers recebem ou resolvem `organizacao_id` e abrem
  `organizacao_atual_privilegiada` antes de consultar modelos tenantizados.
- Constraints e testes incluem `organizacao` em toda identidade local ao
  tenant. Somente IDs garantidamente globais do gateway e índices necessários
  para descobrir o tenant usam unicidade global.
- Auditlog registra ator, organização, antes/depois, idempotência e motivo de
  operações administrativas. Tokens, secrets, body bruto, dados de cartão e
  payloads completos nunca entram na auditoria.
- O template não manipula PAN ou CVV. Dados de pagamento permanecem no gateway.

## 21. Observabilidade e operação

Métricas devem cobrir checkout criado/concluído/falho, latência e erro por
variante/operação, webhook recebido/duplicado/inválido/processado/ignorado,
tentativas e idade da fila, reconciliação divergente, fallback de trial,
início/fim de carência e organização restrita.

Logs estruturados carregam `request_id`, organização quando conhecida,
variante, recurso e identificadores locais. IDs externos podem aparecer quando
não forem segredo. Payloads, headers de assinatura, tokens, e-mail e dados de
cobrança não aparecem.

O Django Admin oferece operações seguras para:

- consultar catálogo e snapshots sem editar versões publicadas;
- provisionar/desativar `ReferenciaPrecoGateway`;
- ativar proposta contratual com justificativa;
- reenfileirar evento falho ou executar reconciliação;
- consultar trilha, sem exibir segredos ou raw.

Operações que possam duplicar efeito exigem dry-run ou chave idempotente.

## 22. Testes e critérios de aceite

### 22.1. Conta e Google

- cadastro e login por senha continuam inalterados;
- cadastro automático e login por Google reutilizam a emissão local de sessão;
- assinatura, expiração, issuer e audience inválidos falham;
- corridas de cadastro não duplicam usuário ou identidade;
- e-mail igual nunca produz vínculo implícito;
- Gmail/Workspace autoritativos verificam localmente e endereço externo não;
- conectar, desconectar e definir senha obedecem autenticação recente;
- verificação, reenvio, troca concorrente e revogação de sessões/API keys;
- desativação, reativação, agendamento e anonimização idempotentes;
- proteção do único proprietário sob concorrência.

### 22.2. Catálogo, recursos e assinatura

- onboarding gratuito por padrão e troca configurável para trial;
- `sync_plans` dry-run, aplicação, idempotência e recusa de mutação;
- imutabilidade e constraints de plano, versão e preço;
- validação, inferência de tipo, defaults antigos e geração de schema dos
  recursos;
- snapshot integral da assinatura e proposta, xor de origem e uma corrente por
  organização;
- cálculo mensal/anual, franquia e quantidade excedente;
- alterações imediatas/agendadas, revisão otimista e idempotência;
- aceitação de proposta paga e ativação contratual auditada;
- encerramento e recontratação como novo ciclo.

### 22.3. Seats e acesso

- ativo e suspenso consomem; convite válido reserva; expirado libera;
- papel enterprise isento e conta de serviço não consomem;
- duas transações no último seat permitem somente uma operação;
- expansão automática é idempotente;
- downgrade e fallback não removem membros e iniciam a carência correta;
- primeira cobrança de trial falha para gratuito sem carência financeira;
- renovação falha não reinicia prazo em novas tentativas;
- duas carências coexistem e a política retorna ambos os motivos;
- middleware anexa contexto, diferencia erros sem enumerar tenant e libera
  somente rotas marcadas de regularização para os papéis permitidos.

### 22.4. Faturamento

- fake de `django-checkouts` cobre o template; a biblioteca mantém sandbox e
  contract tests próprios;
- capability e referência de preço ausentes falham antes do I/O;
- checkouts de contratação, alteração e proposta respeitam idempotência;
- assinatura inválida não persiste evento;
- duplicata, evento atrasado, desconhecido e conhecido com status inválido;
- falha, backoff, limite de tentativas e recuperação de evento abandonado;
- reconciliação usa o mesmo processador e não regride estado;
- RLS real cobre assinatura, checkout, fatura e as fases nula/roteada do evento;
- unicidade do evento é global e a da fatura inclui organização.

Testes de concorrência, constraints e RLS usam PostgreSQL real com papel que
não seja superusuário nem dono da tabela. A entrega também exige Ruff,
type-checking adotado pelo projeto, `makemigrations --check --dry-run`, testes,
cobertura de pelo menos 80% do código novo e build estrito da documentação.

## 23. Fora do escopo e evolução prevista

Ficam explicitamente fora da primeira versão:

- arquitetura genérica de classes para outros provedores de identidade; o
  modelo já comporta novos provedores, mas somente Google será implementado;
- Asaas, PagBank e outras variantes no template; elas entram por futuras
  implementações autocontidas de `django-checkouts`;
- documento fiscal/nota fiscal; `FaturaAssinatura` representa somente invoice;
- persistência de body bruto de webhook. Se necessária, será cifrada, com
  acesso operacional restrito e expiração definida;
- criação automática de produtos/preços remotos pelo `sync_plans`;
- remoção automática de membros para resolver excesso de seats;
- exatamente uma entrega em sistemas distribuídos;
- solicitações de administrador sujeitas à aprovação do proprietário.

A evolução de aprovações permitirá que administrador solicite mudança de
plano, seats, proposta e outras alterações contratuais, e que o proprietário
aprove ou rejeite. O proprietário continuará podendo executar diretamente.
Nenhum modelo, status ou endpoint antecipado para esse fluxo será criado agora.
