"""Contrato entre o `AuthenticationMiddleware` e a `PassthroughAuthentication`.

O resultado é gravado como atributo da própria `HttpRequest`, e não em
threadlocal: `threadlocals.set_request_variable` cai num fallback global da
thread quando não há request corrente, o que vazaria a decisão de uma requisição
para a seguinte no mesmo worker — justamente o cenário que a trava da
`PassthroughAuthentication` precisa detectar.
"""

# Atributo da HttpRequest com o que o middleware decidiu (RESOLVED_*).
REQUEST_ATTR_RESOLVED = "autenticacao_resolvida"

RESOLVED_PUBLIC = "public"
RESOLVED_PRIVATE = "private"
