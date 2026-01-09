# Imagem base otimizada
FROM python:3.10-slim AS base

# Variáveis de ambiente para otimização do Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

# Instalar dependências do sistema necessárias
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    iputils-ping \
    vim \
    && rm -rf /var/lib/apt/lists/*

# Stage de build - apenas para compilar dependências
FROM base AS builder

# Instalar dependências de compilação
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Criar diretório para as wheels
WORKDIR /wheels

# Copiar arquivos de dependências
COPY requirements.txt .

# Compilar as dependências em wheels para instalação mais rápida
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Stage final - imagem de produção
FROM base AS production

# Criar usuário não-root para segurança
RUN groupadd -r django && useradd -r -g django django

# Definir diretório de trabalho
WORKDIR /app

# Copiar wheels do stage de build
COPY --from=builder /wheels /wheels

# Instalar dependências a partir das wheels (apenas arquivos .whl)
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl \
    && rm -rf /wheels

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