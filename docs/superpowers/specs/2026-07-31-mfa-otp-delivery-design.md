# Entrega de OTP MFA — Design

## Objetivo

Tornar a entrega de OTP por e-mail e SMS segura, observável e uniforme nos
fluxos de enrollment, login e reautenticação.

## Decisões

- A task Celery recebe somente `MFAChallenge.pk`; ela gera o OTP, persiste seu
  HMAC e realiza a entrega. O código nunca transita no payload da task.
- O service cria um desafio pendente com validade de cinco minutos e agenda a
  task em `transaction.on_commit()`, evitando uma entrega para uma transação
  revertida.
- Um novo envio do mesmo fator/finalidade antes de 60 segundos não cria outro
  desafio; o service retorna um erro de cooldown. Depois disso, o desafio
  anterior é consumido e um novo é criado.
- A task marca a entrega como `sent` e registra `delivered_at`; em falha, marca
  `failed`, não expõe destino ou OTP em logs, e deixa o desafio inválido para
  confirmação.
- E-mail usa o backend Django configurado; SMS usa `MFA_SMS_BACKEND`.

## Fluxo

1. A view chama o service para iniciar um desafio de e-mail/SMS.
2. O service bloqueia o fator, aplica cooldown, cria `MFAChallenge(pending)` e
   agenda a task após commit.
3. A task bloqueia o desafio, ignora desafios expirados/consumidos e gera o OTP.
4. A task persiste o HMAC, entrega o OTP e atualiza o estado.
5. A confirmação aceita apenas desafio `sent`, ainda válido e não consumido.

## Testes

Os testes cobrem: task com somente ID, OTP de seis dígitos/HMAC, sucesso e
falha de entrega, cooldown de 60 segundos, consumo do desafio anterior após
novo envio e rejeição de confirmação sem entrega bem-sucedida.
