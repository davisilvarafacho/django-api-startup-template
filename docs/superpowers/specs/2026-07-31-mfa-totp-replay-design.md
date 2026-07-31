# Prevenção de Replay TOTP — Design

## Objetivo

Impedir que um mesmo intervalo TOTP seja consumido mais de uma vez nos fluxos
de enrollment, login MFA e reautenticação.

## Decisões

- `MFAFactor.totp_last_counter` representa o maior contador TOTP aceito para o
  fator e permanece `NULL` até o primeiro uso válido.
- Um helper calcula os contadores da janela atual (`-1`, `0`, `+1`), compara os
  códigos em tempo constante e retorna somente um contador estritamente maior
  que o último armazenado.
- O fator é carregado com `select_for_update()` e o contador é persistido na
  mesma transação que consome o desafio ou emite a sessão; duas requisições não
  podem aceitar o mesmo contador.
- A tolerância existente de um período anterior/posterior continua permitida,
  desde que o contador aceito seja novo.
- A confirmação inicial do enrollment também grava o contador, evitando que o
  mesmo código seja reutilizado imediatamente para outro fluxo.

## Testes

Os testes fixam o tempo do TOTP e cobrem primeiro uso, repetição sequencial,
contador seguinte, tolerância de relógio e duas tentativas concorrentes do
mesmo código.
