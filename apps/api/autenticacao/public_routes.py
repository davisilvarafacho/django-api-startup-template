"""Rotas de autenticação que **não** exigem token.

Descobertas automaticamente pelo `RouteRegistry` (ver
`apps.api.core.routes_registry`). Declare prefixos granulares: a comparação é
por `startswith`, então `/auth/` tornaria público inclusive `logout/`.
"""
PUBLIC_ROUTES = [
    "/auth/login/",
]
