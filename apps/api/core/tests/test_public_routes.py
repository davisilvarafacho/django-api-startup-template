from django.test import override_settings
from django.urls import path

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

import pytest

from apps.api.core.route_markers import public


def test_public_applies_allow_any_and_accepts_anonymous_request():
    @public
    class PublicView(APIView):
        permission_classes = [IsAuthenticated]
        authentication_classes = []

        def get(self, request):
            return Response({"public": True})

    assert PublicView.as_view()(APIRequestFactory().get("/public/")).status_code == 200
    assert PublicView.permission_classes == [AllowAny]


@pytest.mark.parametrize(
    ("declared", "permissions", "error_id"),
    [
        (False, [AllowAny], "core.E010"),
        (True, [IsAuthenticated], "core.E011"),
        (True, [AllowAny], None),
    ],
)
def test_public_permission_checks(declared, permissions, error_id):
    from apps.api.core import route_markers

    class View(APIView):
        permission_classes = permissions

    View._rota_publica = declared
    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": [path("_check/", View.as_view())]})):
        errors = route_markers.check_public_routes(None)
    assert [error.id for error in errors] == ([error_id] if error_id else [])


def test_public_prefix_allows_external_view(monkeypatch):
    from apps.api.core import route_markers
    from apps.api.core.routes_registry import routes_registry

    monkeypatch.setattr(routes_registry, "_paths", {"/_check/"})

    class View(APIView):
        permission_classes = [AllowAny]

    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": [path("_check/", View.as_view())]})):
        assert route_markers.check_public_routes(None) == []


def test_public_bypasses_authentication_middleware_and_drf():
    from apps.api.autenticacao.middleware import AuthenticationMiddleware

    @public
    class View(APIView):
        authentication_classes = []

        def get(self, request):
            return Response({"ok": True})

    endpoint = View.as_view()
    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": [path("_public/", endpoint)]})):
        response = AuthenticationMiddleware(endpoint)(APIRequestFactory().get("/_public/"))
    assert response.status_code == 200
    assert response.data == {"ok": True}


def test_public_prefix_rejects_restrictive_route_override(monkeypatch):
    from django.urls import include

    from apps.api.core.route_markers import check_public_routes
    from apps.api.core.routes_registry import routes_registry

    monkeypatch.setattr(routes_registry, "_paths", {"/_external/"})

    class View(APIView):
        permission_classes = [AllowAny]

    patterns = [path("_external/", include([path("resource/", View.as_view(permission_classes=[IsAuthenticated]))]))]
    with override_settings(ROOT_URLCONF=type("URLConf", (), {"urlpatterns": patterns})):
        assert [error.id for error in check_public_routes(None)] == ["core.E011"]
