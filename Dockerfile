FROM python:3.10-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Instalar dependências do sistema necessárias
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    iputils-ping \
    vim \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM base AS production

# Criar usuário não-root para segurança
RUN groupadd -r django && useradd -r -g django django

# Definir diretório de trabalho
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copiar código da aplicação
COPY --chown=django:django . .

# Criar diretórios necessários
RUN mkdir -p /app/logs /app/staticfiles /app/mediafiles \
    && chown -R django:django /app/logs /app/staticfiles /app/mediafiles

# Mudar para usuário não-root
USER django

# Coletar arquivos estáticos
RUN python manage.py collectstatic --noinput --clear

# Expor porta
EXPOSE 80

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:80/health/ || exit 1 

# Comando padrão usando gunicorn
CMD ["gunicorn", "api.wsgi:application", "--config", "gunicorn.conf.py"]
