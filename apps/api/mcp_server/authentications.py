import ipaddress
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import SplitResult, urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import anyio
import jwt
from jwt.exceptions import PyJWKClientError, PyJWTError
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

logger = logging.getLogger(__name__)

ASYMMETRIC_JWT_ALGORITHMS = frozenset(
    {
        "EdDSA",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "RS256",
        "RS384",
        "RS512",
    }
)
ACCESS_TOKEN_TYPES = frozenset({"at+jwt", "application/at+jwt"})
JWK_CACHE_SECONDS = 300
UNKNOWN_KID_CACHE_SECONDS = 30
JWKS_REFRESH_COOLDOWN_SECONDS = 2
MAX_CACHED_KIDS = 128


class JwkClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK: ...


@dataclass(frozen=True)
class HttpAuthConfiguration:
    verifier: TokenVerifier
    auth: AuthSettings
    allowed_hosts: list[str]
    allowed_origins: list[str]


def _is_local_hostname(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_url(name: str, value: str, *, in_production: bool, allow_query: bool = False) -> SplitResult:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name} must be a valid absolute HTTP URL") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ImproperlyConfigured(f"{name} must be a valid absolute HTTP URL")
    if parsed.fragment or (parsed.query and not allow_query):
        raise ImproperlyConfigured(f"{name} must not contain query or fragment")
    if port is not None and not 1 <= port <= 65535:
        raise ImproperlyConfigured(f"{name} contains an invalid port")
    if in_production and parsed.scheme != "https":
        raise ImproperlyConfigured(f"{name} must use HTTPS in production")
    if parsed.scheme == "http" and not in_production and not _is_local_hostname(parsed.hostname):
        raise ImproperlyConfigured(f"{name} may use HTTP only for localhost or loopback")
    return parsed


def _validate_hosts(hosts: list[str], canonical_host: str) -> list[str]:
    if not hosts:
        raise ImproperlyConfigured("MCP_ALLOWED_HOSTS must not be empty")

    validated = []
    for host in hosts:
        if "*" in host:
            raise ImproperlyConfigured("MCP_ALLOWED_HOSTS does not accept wildcards")
        if not host or "://" in host or any(character in host for character in "/?#@"):
            raise ImproperlyConfigured("MCP_ALLOWED_HOSTS must contain host entries without scheme or path")
        try:
            parsed = urlsplit(f"//{host}")
            _ = parsed.port
        except ValueError as exc:
            raise ImproperlyConfigured("MCP_ALLOWED_HOSTS contains an invalid host entry") from exc
        if not parsed.hostname:
            raise ImproperlyConfigured("MCP_ALLOWED_HOSTS contains an invalid host entry")
        validated.append(host)

    if canonical_host.lower() not in {host.lower() for host in validated}:
        raise ImproperlyConfigured("MCP_ALLOWED_HOSTS must contain the MCP_SERVER_URL canonical host")
    return validated


def _validate_origins(origins: list[str], *, in_production: bool) -> list[str]:
    validated = []
    for origin in origins:
        if "*" in origin:
            raise ImproperlyConfigured("MCP_ALLOWED_ORIGINS does not accept wildcards")
        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except ValueError as exc:
            raise ImproperlyConfigured("MCP_ALLOWED_ORIGINS contains an invalid origin entry") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ImproperlyConfigured("MCP_ALLOWED_ORIGINS must contain exact origin entries without path")
        if in_production and parsed.scheme != "https":
            raise ImproperlyConfigured("MCP_ALLOWED_ORIGINS must use HTTPS in production")
        if parsed.scheme == "http" and not in_production and not _is_local_hostname(parsed.hostname):
            raise ImproperlyConfigured("MCP_ALLOWED_ORIGINS may use HTTP only for localhost or loopback")
        validated.append(origin)
    return validated


class OidcJwtTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: list[str],
        jwks_client: JwkClient | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.algorithms = algorithms
        self.jwks_client = jwks_client or jwt.PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=300, timeout=5)
        self._jwk_lock = threading.Lock()
        self._signing_keys: OrderedDict[str, tuple[jwt.PyJWK, float]] = OrderedDict()
        self._unknown_kids: OrderedDict[str, float] = OrderedDict()
        self._refresh_blocked_until = 0.0

    @staticmethod
    def _remember(cache: OrderedDict, key: str, value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > MAX_CACHED_KIDS:
            cache.popitem(last=False)

    def _get_signing_key(self, token: str) -> jwt.PyJWK:
        header = jwt.get_unverified_header(token)
        token_type = header.get("typ")
        key_id = header.get("kid")
        if not isinstance(token_type, str) or token_type.lower() not in ACCESS_TOKEN_TYPES:
            raise jwt.InvalidTokenError("token is not an RFC 9068 access token")
        if not isinstance(key_id, str) or not key_id:
            raise jwt.InvalidTokenError("token does not identify a signing key")

        with self._jwk_lock:
            now = time.monotonic()
            cached_key = self._signing_keys.get(key_id)
            if cached_key is not None:
                signing_key, expires_at = cached_key
                if expires_at > now:
                    self._signing_keys.move_to_end(key_id)
                    return signing_key
                self._signing_keys.pop(key_id)

            unknown_until = self._unknown_kids.get(key_id, 0)
            if unknown_until > now:
                raise PyJWKClientError("signing key is temporarily unavailable")
            self._unknown_kids.pop(key_id, None)
            if self._refresh_blocked_until > now:
                self._remember(self._unknown_kids, key_id, now + UNKNOWN_KID_CACHE_SECONDS)
                raise PyJWKClientError("JWKS refresh is cooling down")

            try:
                signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            except PyJWKClientError:
                self._refresh_blocked_until = now + JWKS_REFRESH_COOLDOWN_SECONDS
                self._remember(self._unknown_kids, key_id, now + UNKNOWN_KID_CACHE_SECONDS)
                raise

            self._remember(self._signing_keys, key_id, (signing_key, now + JWK_CACHE_SECONDS))
            return signing_key

    def _decode(self, token: str) -> dict[str, object]:
        signing_key = self._get_signing_key(token)
        return jwt.decode(
            token,
            signing_key,
            algorithms=self.algorithms,
            audience=self.audience,
            issuer=self.issuer,
            options={"require": ["exp", "iss", "aud"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
            client_id = claims.get("client_id") or claims.get("azp")
            scope = claims.get("scope", "")
            if not isinstance(client_id, str) or not isinstance(scope, str):
                return None

            expires_at = claims.get("exp")
            subject = claims.get("sub")
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=scope.split(),
                expires_at=int(expires_at) if isinstance(expires_at, int | float) and not isinstance(expires_at, bool) else None,
                resource=self.audience,
                subject=subject if isinstance(subject, str) else None,
                claims=claims,
            )
        except (PyJWTError, PyJWKClientError, OverflowError, TypeError, ValueError) as exc:
            logger.debug("MCP bearer token rejected", extra={"error_type": type(exc).__name__})
            return None


def build_http_auth() -> HttpAuthConfiguration:
    values = {
        "MCP_SERVER_URL": settings.MCP_SERVER_URL,
        "MCP_AUTH_ISSUER_URL": settings.MCP_AUTH_ISSUER_URL,
        "MCP_AUTH_AUDIENCE": settings.MCP_AUTH_AUDIENCE,
        "MCP_AUTH_JWKS_URL": settings.MCP_AUTH_JWKS_URL,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ImproperlyConfigured(f"Missing MCP HTTP settings: {', '.join(missing)}")
    if settings.MCP_AUTH_AUDIENCE != settings.MCP_SERVER_URL:
        raise ImproperlyConfigured("MCP_AUTH_AUDIENCE must equal MCP_SERVER_URL")

    algorithms = settings.MCP_AUTH_ALGORITHMS
    if not algorithms or set(algorithms) - ASYMMETRIC_JWT_ALGORITHMS:
        raise ImproperlyConfigured("MCP_AUTH_ALGORITHMS must contain only supported asymmetric algorithms")

    server_url = _validate_url("MCP_SERVER_URL", settings.MCP_SERVER_URL, in_production=settings.IN_PRODUCTION)
    _validate_url("MCP_AUTH_ISSUER_URL", settings.MCP_AUTH_ISSUER_URL, in_production=settings.IN_PRODUCTION)
    _validate_url("MCP_AUTH_JWKS_URL", settings.MCP_AUTH_JWKS_URL, in_production=settings.IN_PRODUCTION, allow_query=True)
    if server_url.path != "/mcp":
        raise ImproperlyConfigured("MCP_SERVER_URL must use the /mcp endpoint")

    hosts = _validate_hosts(settings.MCP_ALLOWED_HOSTS, server_url.netloc)
    origins = _validate_origins(settings.MCP_ALLOWED_ORIGINS, in_production=settings.IN_PRODUCTION)
    verifier = OidcJwtTokenVerifier(
        issuer=settings.MCP_AUTH_ISSUER_URL,
        audience=settings.MCP_AUTH_AUDIENCE,
        jwks_url=settings.MCP_AUTH_JWKS_URL,
        algorithms=algorithms,
    )
    auth = AuthSettings.model_validate(
        {
            "issuer_url": settings.MCP_AUTH_ISSUER_URL,
            "resource_server_url": settings.MCP_SERVER_URL,
            "required_scopes": ["health:read"],
        }
    )
    return HttpAuthConfiguration(verifier=verifier, auth=auth, allowed_hosts=hosts, allowed_origins=origins)
