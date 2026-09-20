"""Router infrastructure has explicit session permissions, outside model policy."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.routers import APIRootView
from rest_framework.routers import DefaultRouter as DRFDefaultRouter

from apps.api.autenticacao.permissions import TokenScopePermission
from apps.organizacoes.permissions import TenantPermission


class SessionAPIRootView(APIRootView):
    permission_classes = [IsAuthenticated, TenantPermission, TokenScopePermission]
    session_only = True


class DefaultRouter(DRFDefaultRouter):
    APIRootView = SessionAPIRootView
