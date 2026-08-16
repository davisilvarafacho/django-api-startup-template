from django.http import HttpResponse
from django.test import RequestFactory

from apps.api.core.context import RequestContextMiddleware, request_atual


def test_request_context_publica_e_limpa_request():
    request = RequestFactory().get("/health/")
    seen = {}

    def response(inner_request):
        seen["request"] = request_atual.get(raise_exception=True)
        return HttpResponse("ok")

    RequestContextMiddleware(response)(request)

    assert seen["request"] is request
    assert request_atual.is_set() is False
