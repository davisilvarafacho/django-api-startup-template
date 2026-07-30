# MFA, ciclo de senha e HaveIBeenPwned — Design

## Objetivo

Completar a autenticação por senha da API Knox com MFA opt-in, dispositivos
confiáveis, reautenticação recente, alteração e redefinição de senha, bloqueio de
senhas vazadas pelo HaveIBeenPwned (HIBP) e limpeza operacional dos tokens
expirados.

O escopo protege somente a API. O login do Django Admin não receberá MFA nesta
entrega.

## Fundação de autenticação

A branch da funcionalidade incorporará somente a fundação necessária que já
existe no trabalho de Batch 5:

- modelo de token substituível e compatível com Knox;
- emissão transacional centralizada de token e metadados;
- validação de tipo e estado do token;
- erros tipados da autenticação;
- throttling de login e reautenticação;
- `RecentAuthenticationPermission` e `@require_recent_auth`.

O restante da branch remota de Batch 5 não será mesclado indiscriminadamente.
As dependências serão integradas na branch `feat/mfa-2fa-hibp`, mantendo a
`main` estável até a conclusão da feature.

## Tipos de token

`TokenType` terá os seguintes valores:

- `TOKEN = 1`: sessão autenticada;
- `RESET_PASSWORD = 2`: autorização para definir uma nova senha;
- `PRE_AUTH = 3`: senha primária validada, MFA ainda pendente;
- `API_KEY = 999`: credencial de integração com escopos.

`TypedTokenAuthentication` continuará aceitando somente `TOKEN` e `API_KEY`.
`RESET_PASSWORD` e `PRE_AUTH` não autenticam endpoints normais da API e possuem
autenticadores exclusivos para seus próprios fluxos.

O modelo `AuthToken` declarará:

```python
EPHEMERAL_TYPES = frozenset(
    {
        TokenType.PRE_AUTH,
        TokenType.RESET_PASSWORD,
    }
)
```

A docstring do modelo documentará que `EPHEMERAL_TYPES` reúne credenciais de
curta duração que podem ser removidas assim que expiram.

`AuthToken.is_expired` será uma propriedade calculada e documentada. Ela retorna
verdadeiro quando `expiry` não é nulo e é menor ou igual ao horário atual.
Tokens efêmeros obrigatoriamente possuem `expiry`.

## Arquitetura MFA

O domínio será próprio. PyOTP será usado apenas como primitivo para gerar,
verificar e provisionar TOTP. O fluxo de API, persistência, replay, entrega,
throttling e auditoria permanecerá sob controle do projeto.

As responsabilidades serão separadas em:

- **orquestrador MFA:** coordena enrollment, desafio, verificação, recuperação e
  trusted devices;
- **backends de fator:** implementam as diferenças entre e-mail, SMS, TOTP e
  recovery;
- **entrega:** usa o backend de e-mail Django e uma interface SMS plugável;
- **persistência:** armazena fatores, desafios, recovery codes e dispositivos;
- **integração Knox:** troca um `PRE_AUTH` consumido por um `TOKEN`;
- **reautenticação:** adapta a fundação existente para desafios MFA em múltiplas
  etapas.

Um usuário pode manter e-mail, SMS e TOTP ativos ao mesmo tempo, mas só pode
possuir uma configuração confirmada de cada tipo. MFA é opt-in e
`user_has_mfa_enabled()` deriva o resultado da existência de ao menos um fator
confirmado e ativo; não haverá booleano duplicado no usuário.

## Modelo de dados

### Usuário

`Usuario` receberá:

- `phone_number`: número E.164 criptografado pela infraestrutura de sensitive
  fields existente;
- `phone_verified_at`: instante de confirmação do telefone.

O telefone não será único nem indexado. Alterá-lo invalida a confirmação,
desativa o fator SMS e revoga desafios, pré-autenticações e trusted devices que
dependiam da configuração anterior.

Alterar o e-mail produz o mesmo efeito para o fator de e-mail.

### Fator MFA

`MFAFactor` representa um fator de e-mail, SMS ou TOTP e possui unicidade por
usuário e tipo. Registra confirmação, ativação, desativação e último uso.

O fator TOTP armazena:

- segredo criptografado com `utils.sensitive_fields.encrypt`;
- último intervalo TOTP consumido, impedindo replay;
- parâmetros do algoritmo necessários para reproduzir a URI de enrollment.

O segredo e a URI `otpauth://` aparecem somente na resposta inicial de setup,
antes da confirmação. A API não os retorna novamente.

### Desafio

`MFAChallenge` registra:

- propósito: login, enrollment ou reautenticação;
- usuário e fator escolhido;
- vínculo opcional ao `PRE_AUTH` ou à sessão;
- expiração, cooldown e contagem de tentativas;
- digest do OTP quando aplicável;
- estado de entrega e consumo.

Trocar o fator durante login é permitido, mas não zera o limite global de
tentativas do `PRE_AUTH`. Verificação e consumo usam transação e bloqueio de
linha para impedir sucesso concorrente.

### Recovery codes

`MFARecoveryCode` mantém um lote de dez códigos aleatórios por usuário. Os
códigos são armazenados com hash lento e salgado, podem ser consumidos em
qualquer ordem e aparecem em texto puro uma única vez.

Gerar um lote novo invalida todos os códigos anteriores. A operação exige
reautenticação recente.

### Trusted devices

`TrustedDevice` guarda somente o digest de uma credencial aleatória, além de
nome, metadados de dispositivo, expiração, último uso e revogação.

A credencial pura aparece somente na criação e é rotacionada quando utilizada
em um login. IP e user-agent servem para observabilidade de risco, mas não são
vínculos rígidos que possam bloquear legitimamente o usuário.

Trusted devices são revogados quando:

- a senha muda;
- telefone ou e-mail de segurança muda;
- um fator é ativado, desativado ou substituído;
- o usuário solicita revogação individual ou global;
- ocorre reset administrativo de MFA.

## Proteção de segredos

- Segredo TOTP e telefone: Fernet com o keyring e rotação já existentes.
- OTP de e-mail/SMS: HMAC com chave do servidor e separação de domínio.
- Recovery codes: hash lento e salgado.
- Tokens Knox e trusted-device tokens: somente digest.
- Comparações de códigos e digests: tempo constante quando aplicável.

Senha, OTP, recovery code, segredo TOTP, token puro e número de telefone não
entram em logs, auditoria, PostHog ou mensagens de erro.

## Enrollment MFA

Toda gestão de MFA exige sessão Knox e reautenticação recente.

1. O usuário inicia o setup de e-mail, SMS ou TOTP.
2. E-mail e SMS criam um desafio de confirmação e enfileiram a entrega.
3. TOTP cria um segredo, persiste-o criptografado e retorna a URI
   `otpauth://`.
4. O fator permanece inativo até a confirmação de um código válido.
5. Ao confirmar o primeiro fator, a API gera o primeiro lote de recovery codes.
6. Confirmar, remover ou substituir um fator revoga todos os trusted devices e
   desafios incompatíveis.

O fator de e-mail usa o e-mail da conta. O fator SMS usa `phone_number`; o
telefone só se torna verificado quando o OTP de enrollment é aceito.

## Login MFA

1. `POST /auth/login/` valida e-mail e senha e aceita opcionalmente uma
   credencial de trusted device.
2. Usuário sem MFA recebe uma sessão `TOKEN`.
3. Usuário com trusted device válido recebe uma sessão `TOKEN` e a credencial
   do dispositivo é rotacionada.
4. Usuário com MFA e sem trusted device recebe um `PRE_AUTH`, além da lista de
   fatores ativos com destinos mascarados.
5. Nenhum OTP é enviado automaticamente; o cliente escolhe o fator.
6. E-mail/SMS enfileiram entrega. TOTP e recovery ficam disponíveis sem envio.
7. A verificação válida revoga o `PRE_AUTH` e emite um `TOKEN` na mesma
   transação.
8. Se o cliente solicitar, a resposta também cria um trusted device.

O `PRE_AUTH` usa o header:

```text
Authorization: PreAuth <token>
```

Ele tem validade padrão de cinco minutos, no máximo cinco tentativas e uso
único. Expiração, excesso de tentativas, troca de senha ou alteração da
configuração MFA o revogam.

## Reautenticação recente

`POST /auth/reauthenticate/` sempre confirma a senha da sessão atual.

- Sem MFA ativo, a confirmação atualiza `reauthenticated_at`.
- Com MFA ativo, a resposta abre um `MFAChallenge` de reautenticação e lista os
  fatores disponíveis.
- O cliente escolhe e verifica um fator por endpoints próprios.
- Somente após a segunda etapa `reauthenticated_at` é atualizado.
- API keys e tokens efêmeros nunca satisfazem reautenticação recente.

Setup/remoção de fatores, recuperação, telefone, trusted devices e alteração de
senha exigem `@require_recent_auth()`.

## Recuperação administrativa de MFA

Um operador com permissão específica pode remover a configuração MFA de outro
usuário após validação externa de identidade.

A operação exige:

- sessão comum, nunca API key;
- reautenticação recente do operador;
- justificativa não vazia;
- evento de auditoria com operador, alvo, horário e justificativa.

Ela desativa fatores, invalida recovery codes e desafios e revoga sessões,
pré-autenticações e trusted devices do usuário afetado. A senha do usuário não
é alterada. O fluxo não oferece recuperação automática por e-mail.

## Alteração de senha autenticada

`POST /auth/password/change/` exige reautenticação recente e recebe
`new_password` e `new_password_confirmation`.

Em sucesso:

- executa todos os validadores Django, incluindo HIBP;
- define a senha atomicamente;
- revoga todas as sessões, inclusive a atual;
- revoga `PRE_AUTH` e `RESET_PASSWORD` pendentes;
- revoga todos os trusted devices e desafios abertos;
- preserva fatores MFA e recovery codes;
- registra evento de segurança e enfileira aviso por e-mail;
- exige novo login.

## Redefinição de senha deslogada

### Solicitação

`POST /auth/password/reset/request/` recebe o e-mail e sempre retorna a mesma
resposta, independentemente da existência ou estado da conta.

O endpoint aplica throttling por IP e por identificador de e-mail sanitizado.
Para uma conta ativa, a task:

1. revoga tokens de reset anteriores;
2. emite `RESET_PASSWORD` com validade padrão de trinta minutos;
3. monta o link a partir de uma URL de frontend obrigatória por ambiente;
4. envia o e-mail sem persistir ou registrar o token puro.

A task recebe apenas o ID do usuário; o token puro não trafega no payload do
broker.

### Confirmação

`POST /auth/password/reset/confirm/` recebe token, senha nova e confirmação.
O token é bloqueado, validado e consumido atomicamente.

Conforme decisão de produto, o token recebido por e-mail é suficiente mesmo
quando MFA está ativo. O reset não exige outro fator.

Após sucesso, o fluxo executa as mesmas revogações da alteração autenticada e
envia um aviso de segurança. Tokens inexistentes, expirados, revogados e
consumidos retornam o mesmo erro público.

## Validação de senha e HIBP

`PwnedPasswordValidator` será registrado em `AUTH_PASSWORD_VALIDATORS`. Todos os
fluxos suportados para definir senha usam um serviço central que chama
`validate_password(password, user)` antes de `set_password()`.

O cliente HIBP:

1. calcula localmente SHA-1 da senha UTF-8;
2. envia somente os cinco primeiros caracteres para a API Pwned Passwords;
3. solicita `Add-Padding: true`;
4. compara localmente o sufixo restante;
5. rejeita qualquer senha com contagem maior que zero;
6. descarta imediatamente a resposta.

A mensagem pública informa apenas que a senha apareceu em vazamentos; a
contagem não é exposta.

Timeout, erro de rede, indisponibilidade ou resposta inválida seguem fail-open:
os demais validadores continuam valendo e a operação não é bloqueada. Logs e
métricas registram somente classe do erro, latência e resultado operacional.
Não se registra prefixo, sufixo, senha ou identidade.

O cliente tem timeouts e ativação configuráveis e é substituído por fake em
testes. Testes nunca acessam a rede real.

### Exceção de superusuário

`UsuarioManager.create_user()` executa a validação central. Entretanto,
`UsuarioManager.create_superuser()` não executa nenhum
`AUTH_PASSWORD_VALIDATOR`, incluindo HIBP, por decisão explícita de produto. A
senha ainda é armazenada pelo hasher seguro do Django.

Alterações futuras da senha do superusuário pelos fluxos normais voltam a
executar todos os validadores.

## Entrega de e-mail e SMS

E-mail usa o backend Django já existente. SMS usa uma interface configurada por
settings, com:

- backend de memória para testes;
- backend de console para desenvolvimento;
- contrato documentado para providers de produção.

`manage.py check --deploy` falha se SMS estiver habilitado sem backend de
produção configurado.

Tasks de OTP recebem somente o ID do desafio. A própria task gera o código,
persiste seu HMAC e chama o backend. Não há retry automático de entrega, para
não produzir códigos concorrentes; falhas ficam sanitizadas no desafio e o
cliente pode solicitar reenvio após cooldown.

## Contrato da API

### Rotas públicas

- `POST /auth/login/`
- `POST /auth/password/reset/request/`
- `POST /auth/password/reset/confirm/`

### Rotas autenticadas por `PRE_AUTH`

- `POST /auth/mfa/challenge/`
- `POST /auth/mfa/verify/`

### Rotas autenticadas por sessão Knox

- `GET /auth/mfa/`
- `POST /auth/mfa/factors/<type>/setup/`
- `POST /auth/mfa/factors/<type>/confirm/`
- `DELETE /auth/mfa/factors/<type>/`
- `POST /auth/mfa/recovery-codes/regenerate/`
- `GET /auth/trusted-devices/`
- `DELETE /auth/trusted-devices/<id>/`
- `DELETE /auth/trusted-devices/`
- `POST /auth/reauthenticate/`
- `POST /auth/reauthenticate/challenge/`
- `POST /auth/reauthenticate/verify/`
- `POST /auth/password/change/`
- `POST /auth/mfa/admin-reset/`

O reset administrativo recebe usuário alvo e justificativa e exige permissão
específica.

## Parâmetros padrão

- `PRE_AUTH`: cinco minutos, cinco tentativas.
- OTP de e-mail/SMS: seis dígitos e cinco minutos.
- Cooldown de reenvio: sessenta segundos.
- TOTP: período de trinta segundos e janela de um intervalo anterior/posterior.
- Recovery codes: dez por lote.
- `RESET_PASSWORD`: trinta minutos.
- Trusted device: trinta dias.
- Retenção de sessões expiradas: noventa dias.

Todos os valores temporais e limites são configuráveis por settings.

## Erros, abuso e observabilidade

Os fluxos usam o envelope e o registry de erros tipados da fundação de Batch 5.
Haverá códigos estáveis para:

- credenciais primárias inválidas;
- fator indisponível;
- desafio/token inválido ou expirado;
- código incorreto;
- cooldown e throttling;
- excesso de tentativas;
- senha vazada;
- backend de entrega indisponível;
- reautenticação necessária.

Login, solicitação de reset, reautenticação, envio e verificação de OTP terão
throttling apropriado. Respostas públicas não confirmam existência de conta,
fator ou telefone.

Eventos de segurança registram somente identificadores internos e dados
sanitizados: enrollment, remoção de fator, recuperação, trusted device,
sucesso/falha de MFA, alteração/reset de senha e reset administrativo.

## Limpeza de tokens expirados

Um serviço único implementa a seleção e remoção. Ele será reutilizado por
management command e Celery task.

`cleanup_expired_auth_tokens` oferece:

- `--dry-run`;
- `--batch-size`;
- `--session-retention-days`;
- contagem de examinados/removidos por tipo;
- lotes com transações curtas;
- comportamento idempotente.

Política:

- `PRE_AUTH` e `RESET_PASSWORD`: removidos assim que expirarem;
- `TOKEN`: removido após a retenção, contada desde sua expiração;
- `API_KEY`: nunca removida;
- token sem `expiry`: nunca selecionado;
- dependências: removidas por cascade.

A task Celery reutiliza o serviço, possui retries com backoff apenas para falhas
transitórias do banco e registra duração e contagens.

`CELERY_BEAT_SCHEDULE` provisiona a execução todos os dias às `00:00`.
`CELERY_TIMEZONE = TIME_ZONE` e o `DatabaseScheduler` existente garantem o fuso
`America/Sao_Paulo`. Nenhum cadastro manual no Django Admin é necessário.

## Testes de aceitação

- Usuário sem MFA mantém o login atual.
- E-mail, SMS e TOTP podem ficar ativos simultaneamente.
- Cada fator só ativa após confirmação.
- O segredo TOTP nunca aparece após setup e fica cifrado no banco.
- OTP, recovery, TOTP, `PRE_AUTH` e `RESET_PASSWORD` são de uso único.
- Verificações concorrentes produzem no máximo um sucesso.
- Trocar fator não contorna o limite de tentativas.
- Trusted device válido pula MFA, rotaciona sua credencial e expira/revoga.
- Reautenticação exige senha e MFA quando ativo.
- Gestão de MFA exige reautenticação recente.
- Reset administrativo exige permissão, reautenticação e justificativa.
- Alteração/reset de senha aplica os validadores e revoga credenciais.
- Reset deslogado não exige MFA e não enumera contas.
- Senha encontrada no HIBP é recusada.
- Indisponibilidade do HIBP segue fail-open e gera observabilidade sanitizada.
- `create_superuser()` ignora validadores; demais fluxos não.
- `is_expired` respeita `expiry=None`, futuro e limite exato.
- Command e task removem efêmeros, respeitam retenção de sessão e preservam API
  keys.
- Beat registra a limpeza diária à meia-noite no fuso do projeto.
- Backends externos são substituídos por fakes; testes não usam rede.
- OpenAPI documenta respostas, autenticações e códigos de erro.

## Fora do escopo

- MFA no Django Admin.
- WebAuthn/passkeys.
- Mais de um fator do mesmo tipo por usuário.
- Provider comercial de SMS incluído no núcleo.
- Interface frontend ou geração de imagem QR; a API retorna a URI
  `otpauth://`.
- Verificação de senha vazada durante login.
- Política organizacional ou obrigatoriedade global de MFA.
- Social login, verificação geral de e-mail e exclusão/desativação de conta.
