FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    g++ \
    gcc \
    git \
    libpq-dev \
    make \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

RUN groupadd --gid 1000 vscode \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash vscode \
    && mkdir -p /workspace /opt/venv \
    && chown -R vscode:vscode /workspace /opt/venv

WORKDIR /workspace

COPY --chown=vscode:vscode pyproject.toml uv.lock ./

USER vscode

RUN uv sync --frozen --group dev --group observability --no-install-project

CMD ["sleep", "infinity"]
