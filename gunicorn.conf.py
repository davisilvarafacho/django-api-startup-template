"""Configuração do Gunicorn para produção."""
import multiprocessing
import os

# Endereço de bind
bind = "0.0.0.0:80"

# Workers
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50

# Timeouts
timeout = 120
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "/app/logs/gunicorn_access.log"
errorlog = "/app/logs/gunicorn_error.log"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "base_api"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (descomente se usar HTTPS)
# keyfile = "/path/to/key.pem"
# certfile = "/path/to/cert.pem"

# Server hooks
def on_starting(server):
    """Chamado antes do master process ser inicializado."""
    server.log.info("Gunicorn iniciando...")


def on_reload(server):
    """Chamado quando o servidor recarrega."""
    server.log.info("Gunicorn recarregando...")


def when_ready(server):
    """Chamado quando o servidor está pronto."""
    server.log.info("Gunicorn pronto para receber requisições")


def pre_fork(server, worker):
    """Chamado antes de cada worker ser criado."""


def post_fork(server, worker):
    """Chamado após cada worker ser criado."""
    server.log.info("Worker %s iniciado", worker.pid)


def pre_exec(server):
    """Chamado antes de um novo master process ser criado."""
    server.log.info("Forked child, re-executing.")


def worker_int(worker):
    """Chamado quando um worker recebe um sinal INT ou QUIT."""
    worker.log.info("Worker %s recebeu sinal de interrupção", worker.pid)


def worker_abort(worker):
    """Chamado quando um worker recebe um sinal SIGABRT."""
    worker.log.info("Worker %s recebeu SIGABRT", worker.pid)


def child_exit(server, worker):
    """Remove as métricas do worker que morreu.

    Com `PROMETHEUS_MULTIPROC_DIR` cada worker escreve num arquivo próprio que o
    `/metrics` consolida. Sem limpar na saída, os contadores de workers mortos
    continuam sendo somados para sempre — e o gunicorn recicla worker a cada
    `max_requests` (1000), então o vazamento é rápido.
    """
    if not os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        return

    # Import local: prometheus_client só é necessário quando o modo multiprocesso
    # está ligado.
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
