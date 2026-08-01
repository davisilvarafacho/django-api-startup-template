"""Contrato entre o `AuthenticationMiddleware` e a `PassthroughAuthentication`.

O resultado vive como atributo da própria `HttpRequest`, e não no contexto
compatível de `threadlocals`: o marcador faz parte do estado HTTP da requisição
e precisa acompanhar exatamente esse objeto.
"""

# Atributo da HttpRequest com o que o middleware decidiu (RESOLVED_*).
REQUEST_ATTR_RESOLVED = "autenticacao_resolvida"

RESOLVED_PUBLIC = "public"
RESOLVED_PRIVATE = "private"
