"""Contrato entre o `AuthenticationMiddleware` e a `PassthroughAuthentication`.

O resultado é gravado como atributo da própria `HttpRequest`, e não em
variável de contexto: o resultado fica no próprio objeto da request, evitando
estado global e vazamento entre requisições reutilizando o mesmo worker.
"""

# Atributo da HttpRequest com o que o middleware decidiu (RESOLVED_*).
REQUEST_ATTR_RESOLVED = "autenticacao_resolvida"

RESOLVED_PUBLIC = "public"
RESOLVED_PRIVATE = "private"
