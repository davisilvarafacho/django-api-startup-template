# the-best-django-api-template

A mais robusta base para apis rest com django e rest framework do open source brasileiro🇧🇷.

## Ambiente de desenvolvimento

O projeto usa [uv](https://docs.astral.sh/uv/) para gerenciar o Python e as dependências.

```bash
uv sync
uv run python manage.py runserver
```

Comandos comuns:

```bash
uv run pytest
uv run python manage.py migrate
uv lock --upgrade
```

O arquivo `uv.lock` deve ser versionado. Após modificar as dependências, use `uv add <pacote>` (ou `uv remove <pacote>`) e inclua as alterações em `pyproject.toml` e `uv.lock` no commit.

## Documentação da API

A especificação OpenAPI é gerada pelo `drf-spectacular` e exibida com o Scalar:

- Schema OpenAPI: [`/api/schema/`](/api/schema/)
- Referência interativa: [`/api/docs/`](/api/docs/)

Para validar o schema antes de publicar alterações:

```bash
uv run python manage.py spectacular --validate --file schema.yml
```

## Arquivos no Backblaze B2

O storage padrão usa `b2sdk` e o backend `apps.api.core.b2_storage.BackblazeB2Storage`.
Configure as credenciais da *Application Key* com permissão para o bucket:

```bash
BACKBLAZE_APPLICATION_ID="..."
BACKBLAZE_APPLICATION_KEY="..."
BACKBLAZE_BUCKET_NAME="meu-bucket"
```

Opcionalmente, defina `BACKBLAZE_BUCKET_ID` para evitar a busca pelo nome do bucket, `BACKBLAZE_LOCATION` para usar um prefixo (por exemplo, `media`) e `BACKBLAZE_PUBLIC_BASE_URL` para servir arquivos por um domínio próprio/CDN. Se a última variável não estiver configurada, `FieldFile.url` usa a URL de download do próprio B2. Para buckets privados, use URLs assinadas ou uma camada de entrega autenticada; a URL padrão não concede acesso por si só.

## Emails com Resend

O backend padrão de email usa a API do Resend. Configure uma chave com permissão de envio e um remetente de um domínio verificado no Resend:

```bash
RESEND_API_KEY="re_..."
RESEND_FROM_EMAIL="Minha API <nao-responda@exemplo.com>"
```

O backend suporta mensagens texto, HTML (`EmailMultiAlternatives`), cópia, cópia oculta, `reply_to`, cabeçalhos extras e anexos comuns do Django.

## Infraestrutura local (Postgres + Redis + Celery)

O banco padrão passou a ser **PostgreSQL** e o cache/broker usa **Redis**. Para
subir a infra local:

```bash
cp .env.example .env   # e preencha DATABASE_* / REDIS_*
docker compose up -d db redis
uv run python manage.py migrate
uv run python manage.py runserver
```

Subir a stack completa (web + worker + beat) em containers:

```bash
docker compose up --build
```

### Celery

- Worker: `celery -A api worker -l info`
- Beat (agendador via banco): `celery -A api beat -l info --scheduler django_celery_beat.schedulers:DatabaseScheduler`
- Em testes, as tasks rodam de forma síncrona (`CELERY_TASK_ALWAYS_EAGER`).

### Cache

- `default` (Redis db 1): cache de aplicação e throttling do DRF.
- `cachalot` (Redis db 3): cache **compartilhado** do django-cachalot — necessário
  para invalidação correta entre workers do gunicorn.
- `BaseModelViewSet` expõe `build_cache_key(...)` para cachear respostas com TTL e
  a action `POST .../invalidate_cache/` para flush manual (invalidação O(1) por
  versão de namespace).

### Throttling

Limites padrão do DRF: `anon` 100/h, `user` 1000/h. O escopo `auth` (10/min) está
reservado para endpoints de login/reset.
