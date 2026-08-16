# Django Anymail com Resend

## Objetivo

Substituir o backend Resend mantido pelo projeto pelo backend oficial do
`django-anymail`, preservando as variáveis de ambiente existentes e o contrato
de envio de e-mails do Django.

Esta entrega cobre somente o envio transacional pelo Resend. Webhooks de status,
recebimento de eventos e seleção dinâmica de provedor ficam fora do escopo.

## Decisões

- Instalar `django-anymail[resend]`.
- Remover a dependência direta do SDK Python `resend`.
- Adicionar `anymail` a `INSTALLED_APPS`.
- Configurar `anymail.backends.resend.EmailBackend` como `EMAIL_BACKEND`.
- Preservar `RESEND_API_KEY` e `RESEND_FROM_EMAIL`, evitando mudanças nos
  ambientes existentes.
- Remover o backend próprio `apps.api.core.email_backends.ResendEmailBackend`.
- Usar o backend de testes do Anymail durante testes automatizados.

## Configuração

`api/settings.py` traduzirá as variáveis existentes para a configuração esperada
pelo Anymail:

```python
ANYMAIL = {
    "RESEND_API_KEY": get_env_var("RESEND_API_KEY"),
}

DEFAULT_FROM_EMAIL = get_env_var(
    "RESEND_FROM_EMAIL",
    "nao-responda@base.com.br",
)

EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
```

O app `anymail` será incluído na lista comum de aplicações, pois a configuração
é válida em todos os ambientes. No ambiente de testes, o backend será
`anymail.backends.test.EmailBackend`, impedindo chamadas externas e expondo as
mensagens em `django.core.mail.outbox`.

## Fluxo de envio

Os consumidores continuarão usando as APIs padrão do Django:

```text
send_mail / EmailMessage / EmailMultiAlternatives
    -> backend Anymail
    -> API do Resend
```

Não será criada uma abstração adicional no projeto. A fronteira de
interoperabilidade é o próprio contrato de e-mail do Django, e o provedor pode
ser trocado futuramente por configuração do Anymail.

## Compatibilidade

Devem permanecer disponíveis:

- mensagens em texto;
- alternativas HTML;
- CC e BCC;
- `reply_to`;
- headers adicionais;
- anexos compatíveis com o Django.

O remetente padrão continuará vindo de `RESEND_FROM_EMAIL`. Chamadas que
informem `from_email` explicitamente continuarão prevalecendo.

## Tratamento de erros

- Uma tentativa real de envio sem `RESEND_API_KEY` deve falhar explicitamente
  pelo backend do Anymail.
- Erros retornados pelo Resend devem ser propagados normalmente.
- Quando o chamador usar `fail_silently=True`, o contrato padrão do backend
  deve impedir que a exceção seja propagada.
- O projeto não adicionará captura genérica de exceções em torno do Anymail.

## Testes

Os testes do backend próprio serão removidos e substituídos por testes que
validem:

1. o backend Resend do Anymail configurado fora do ambiente de testes;
2. o backend de testes do Anymail selecionado no ambiente de testes;
3. o mapeamento de `RESEND_API_KEY` para `ANYMAIL["RESEND_API_KEY"]`;
4. a preservação de `RESEND_FROM_EMAIL` em `DEFAULT_FROM_EMAIL`;
5. o envio de texto e HTML, metadados de destinatários, headers e anexo por meio
   de `EmailMultiAlternatives`, sem acesso à rede;
6. a ausência do backend próprio e da dependência direta `resend`.

## Documentação

Serão atualizados:

- `README.md`, mantendo as instruções das variáveis atuais e informando que o
  envio usa Anymail;
- `.env.example`, mantendo `RESEND_API_KEY` e `RESEND_FROM_EMAIL`;
- `CHANGELOG.md`;
- `docs/ROADMAP.md`, marcando `django-anymail` como concluído e o Batch 8 como
  em andamento.

## Critérios de aceite

- O projeto não importa nem depende diretamente do SDK `resend`.
- E-mails continuam sendo enviados pelo Resend através do Anymail.
- Nenhuma variável de ambiente existente precisa ser renomeada.
- Testes de e-mail não realizam chamadas externas.
- A suíte direcionada de configuração e envio de e-mails passa.
- Lint e verificação de migrations continuam passando.
