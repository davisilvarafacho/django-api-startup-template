import time

from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.api.mcp_server.authentications import OidcJwtTokenVerifier, build_http_auth

HTTP_AUTH_SETTINGS = {
    "IN_PRODUCTION": True,
    "MCP_SERVER_URL": "https://mcp.example.com/mcp",
    "MCP_AUTH_ISSUER_URL": "https://auth.example.com",
    "MCP_AUTH_AUDIENCE": "https://mcp.example.com/mcp",
    "MCP_AUTH_JWKS_URL": "https://auth.example.com/.well-known/jwks.json",
    "MCP_AUTH_ALGORITHMS": ["RS256"],
    "MCP_ALLOWED_HOSTS": ["mcp.example.com"],
    "MCP_ALLOWED_ORIGINS": ["https://client.example.com"],
}


class StaticJwkClient:
    def __init__(self, public_key):
        jwk_json = jwt.algorithms.RSAAlgorithm.to_jwk(public_key)
        self.signing_key = jwt.PyJWK.from_json(jwk_json)
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        return self.signing_key


class MissingJwkClient:
    def __init__(self):
        self.calls = 0

    def get_signing_key_from_jwt(self, token):
        self.calls += 1
        raise jwt.PyJWKClientError("unknown kid")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def make_token(private_key, *, algorithm="RS256", key_id="test-key", token_type="at+jwt", remove=(), **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://auth.example.com",
        "aud": "https://mcp.example.com/mcp",
        "exp": now + 300,
        "nbf": now - 1,
        "client_id": "test-client",
        "sub": "user-1",
        "scope": "health:read profile",
    }
    claims.update(overrides)
    for claim in remove:
        claims.pop(claim)
    return jwt.encode(claims, private_key, algorithm=algorithm, headers={"kid": key_id, "typ": token_type})


def make_verifier(public_key):
    return OidcJwtTokenVerifier(
        issuer="https://auth.example.com",
        audience="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=["RS256"],
        jwks_client=StaticJwkClient(public_key),
    )


@override_settings(**HTTP_AUTH_SETTINGS)
def test_build_http_auth_preserves_canonical_urls_without_adding_slash():
    configuration = build_http_auth()

    serialized = configuration.auth.model_dump(mode="json")
    assert serialized["issuer_url"] == "https://auth.example.com"
    assert serialized["resource_server_url"] == "https://mcp.example.com/mcp"
    assert configuration.allowed_hosts == ["mcp.example.com"]
    assert configuration.allowed_origins == ["https://client.example.com"]


@pytest.mark.parametrize(
    ("setting", "value", "message"),
    [
        ("MCP_AUTH_AUDIENCE", "https://other.example.com/mcp", "must equal MCP_SERVER_URL"),
        ("MCP_ALLOWED_HOSTS", ["other.example.com"], "canonical host"),
        ("MCP_ALLOWED_HOSTS", ["*"], "wildcards"),
        ("MCP_ALLOWED_HOSTS", ["https://mcp.example.com"], "host entries"),
        ("MCP_ALLOWED_ORIGINS", ["*"], "wildcards"),
        ("MCP_ALLOWED_ORIGINS", ["https://client.example.com/path"], "origin entries"),
        ("MCP_AUTH_ALGORITHMS", ["HS256"], "asymmetric"),
    ],
)
def test_build_http_auth_rejects_inconsistent_security_settings(setting, value, message):
    configured = HTTP_AUTH_SETTINGS | {setting: value}

    with override_settings(**configured), pytest.raises(ImproperlyConfigured, match=message):
        build_http_auth()


@override_settings(**(HTTP_AUTH_SETTINGS | {"MCP_SERVER_URL": ""}))
def test_build_http_auth_rejects_missing_settings():
    with pytest.raises(ImproperlyConfigured, match="MCP_SERVER_URL"):
        build_http_auth()


@override_settings(**(HTTP_AUTH_SETTINGS | {"MCP_SERVER_URL": "http://mcp.example.com/mcp", "MCP_AUTH_AUDIENCE": "http://mcp.example.com/mcp"}))
def test_build_http_auth_rejects_http_in_production():
    with pytest.raises(ImproperlyConfigured, match="HTTPS"):
        build_http_auth()


@pytest.mark.parametrize("server_url", ["not-a-url", "https://mcp.example.com/other"])
def test_build_http_auth_rejects_invalid_resource_url(server_url):
    configured = HTTP_AUTH_SETTINGS | {
        "MCP_SERVER_URL": server_url,
        "MCP_AUTH_AUDIENCE": server_url,
    }

    with override_settings(**configured), pytest.raises(ImproperlyConfigured, match="valid absolute HTTP URL|/mcp endpoint"):
        build_http_auth()


@override_settings(
    **(
        HTTP_AUTH_SETTINGS
        | {
            "IN_PRODUCTION": False,
            "MCP_SERVER_URL": "http://127.0.0.1:8001/mcp",
            "MCP_AUTH_ISSUER_URL": "http://localhost:9000/realms/test",
            "MCP_AUTH_AUDIENCE": "http://127.0.0.1:8001/mcp",
            "MCP_AUTH_JWKS_URL": "http://localhost:9000/realms/test/jwks",
            "MCP_ALLOWED_HOSTS": ["127.0.0.1:8001"],
            "MCP_ALLOWED_ORIGINS": ["http://localhost:3000"],
        }
    )
)
def test_build_http_auth_allows_local_http_outside_production():
    configuration = build_http_auth()

    assert configuration.auth.model_dump(mode="json")["resource_server_url"] == "http://127.0.0.1:8001/mcp"


@pytest.mark.anyio
async def test_verifier_accepts_valid_signed_jwt(key_pair):
    private_key, public_key = key_pair

    access = await make_verifier(public_key).verify_token(make_token(private_key))

    assert access is not None
    assert access.client_id == "test-client"
    assert access.scopes == ["health:read", "profile"]
    assert access.resource == "https://mcp.example.com/mcp"
    assert access.subject == "user-1"


@pytest.mark.anyio
async def test_verifier_accepts_audience_list_and_azp_fallback(key_pair):
    private_key, public_key = key_pair
    token = make_token(
        private_key,
        aud=["https://another.example.com", "https://mcp.example.com/mcp"],
        client_id=None,
        azp="fallback-client",
    )

    access = await make_verifier(public_key).verify_token(token)

    assert access is not None
    assert access.client_id == "fallback-client"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://other.example.com/mcp"},
        {"iss": "https://other.example.com"},
        {"exp": 1},
        {"client_id": None},
        {"scope": ["health:read"]},
    ],
)
async def test_verifier_rejects_invalid_claims(key_pair, overrides):
    private_key, public_key = key_pair

    access = await make_verifier(public_key).verify_token(make_token(private_key, **overrides))

    assert access is None


@pytest.mark.anyio
async def test_verifier_rejects_token_before_not_before_time(key_pair):
    private_key, public_key = key_pair

    access = await make_verifier(public_key).verify_token(make_token(private_key, nbf=int(time.time()) + 300))

    assert access is None


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["exp", "iss", "aud"])
async def test_verifier_rejects_missing_required_claims(key_pair, claim):
    private_key, public_key = key_pair

    access = await make_verifier(public_key).verify_token(make_token(private_key, remove=[claim]))

    assert access is None


@pytest.mark.anyio
async def test_verifier_rejects_signed_token_with_unapproved_algorithm(key_pair):
    private_key, public_key = key_pair

    access = await make_verifier(public_key).verify_token(make_token(private_key, algorithm="RS512"))

    assert access is None


@pytest.mark.anyio
async def test_verifier_rejects_token_without_access_token_type_before_jwks_lookup(key_pair):
    private_key, public_key = key_pair
    jwks_client = StaticJwkClient(public_key)
    verifier = OidcJwtTokenVerifier(
        issuer="https://auth.example.com",
        audience="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=["RS256"],
        jwks_client=jwks_client,
    )

    access = await verifier.verify_token(make_token(private_key, token_type="JWT"))

    assert access is None
    assert jwks_client.calls == 0


@pytest.mark.anyio
async def test_verifier_throttles_repeated_unknown_key_refreshes(key_pair):
    private_key, _ = key_pair
    jwks_client = MissingJwkClient()
    verifier = OidcJwtTokenVerifier(
        issuer="https://auth.example.com",
        audience="https://mcp.example.com/mcp",
        jwks_url="https://auth.example.com/.well-known/jwks.json",
        algorithms=["RS256"],
        jwks_client=jwks_client,
    )

    first = await verifier.verify_token(make_token(private_key, key_id="unknown-1"))
    repeated = await verifier.verify_token(make_token(private_key, key_id="unknown-1"))
    another = await verifier.verify_token(make_token(private_key, key_id="unknown-2"))

    assert first is repeated is another is None
    assert jwks_client.calls == 1


@pytest.mark.anyio
async def test_verifier_rejects_unbounded_numeric_dates(key_pair):
    private_key, public_key = key_pair

    access = await make_verifier(public_key).verify_token(make_token(private_key, exp=float("inf")))

    assert access is None
