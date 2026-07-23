"""Correlation ID por request.

Cada request recebe um identificador único que atravessa log, Sentry e as tasks
do Celery disparadas por ela. É o que permite pegar um erro no Sentry e achar a
linha de log correspondente (e vice-versa) sem depender de timestamp.

O id é guardado em `contextvars` em vez de `threading.local` porque o `contextvar`
acompanha corretamente código assíncrono e não vaza entre requests que reaproveitam
a mesma thread do pool.
"""
import logging
import time
import uuid
from contextvars import ContextVar

from django.utils.deprecation import MiddlewareMixin

# Header aceito na entrada e sempre devolvido na resposta. Se o proxy/gateway já
# gera um id (nginx, ALB, Cloudflare), ele é reaproveitado.
HEADER_REQUEST_ID = "X-Request-ID"

META_HEADER_REQUEST_ID = "HTTP_X_REQUEST_ID"

_request_id = ContextVar("request_id", default=None)

access_logger = logging.getLogger("api.access")


def get_request_id():
    """Retorna o id da request corrente, ou `None` fora do ciclo de request."""
    return _request_id.get()


def set_request_id(request_id):
    """Define o id da request corrente e devolve o token para reversão."""
    return _request_id.set(request_id)


def reset_request_id(token):
    """Restaura o id anterior a partir do token devolvido por `set_request_id`."""
    _request_id.reset(token)


def gerar_request_id():
    """Gera um novo id de request."""
    return uuid.uuid4().hex


def normalizar_request_id(valor):
    """Valida o id recebido do cliente.

    Um header vindo de fora não pode ser confiado cegamente: ele vira campo de
    log e tag do Sentry, então valores gigantes ou com caracteres de controle
    poluiriam (ou quebrariam) o pipeline. Só aceita hexadecimal/UUID.
    """
    if not valor:
        return None

    valor = valor.strip()

    if len(valor) > 64:
        return None

    candidato = valor.replace("-", "")
    if not candidato.isalnum():
        return None

    return valor


class RequestIDMiddleware(MiddlewareMixin):
    """Atribui o correlation id e emite o log de acesso da request.

    Deve ficar o mais externo possível (logo após o `SecurityMiddleware`) para
    que qualquer log emitido pelos middlewares seguintes já tenha o id.
    """

    def process_request(self, request):
        recebido = normalizar_request_id(request.META.get(META_HEADER_REQUEST_ID))

        request.id = recebido or gerar_request_id()
        request._request_id_token = set_request_id(request.id)
        request._iniciada_em = time.monotonic()

        self._marcar_no_sentry(request.id)

    def process_response(self, request, response):
        request_id = getattr(request, "id", None)
        if request_id is None:
            return response

        response[HEADER_REQUEST_ID] = request_id

        self._logar_acesso(request, response)
        self._limpar_contexto(request)

        return response

    def process_exception(self, request, exception):
        # A exceção sobe para o handler do Django; aqui só liberamos o contexto,
        # já que nesse fluxo o `process_response` pode não ser chamado.
        self._limpar_contexto(request)
        return

    def _marcar_no_sentry(self, request_id):
        # Import local: o SDK só é inicializado em produção, mas `set_tag` é
        # inofensivo quando não há client configurado.
        import sentry_sdk

        sentry_sdk.set_tag("request_id", request_id)

    def _logar_acesso(self, request, response):
        """Emite uma linha estruturada por request.

        Este é o substituto do `drf-api-logger` em produção: o log de acesso vai
        para stdout/arquivo (e daí para o Loki), nunca para o banco. Guardar
        request log em tabela é o que faz um banco de produção passar de dezenas
        de GB em poucos dias.
        """
        iniciada_em = getattr(request, "_iniciada_em", None)
        duracao_ms = None
        if iniciada_em is not None:
            duracao_ms = round((time.monotonic() - iniciada_em) * 1000, 2)

        access_logger.info(
            "%s %s %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "http_method": request.method,
                "http_path": request.path,
                "http_status": response.status_code,
                "duracao_ms": duracao_ms,
            },
        )

    def _limpar_contexto(self, request):
        token = getattr(request, "_request_id_token", None)
        if token is not None:
            reset_request_id(token)
            request._request_id_token = None
