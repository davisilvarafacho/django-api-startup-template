import os
from uuid import uuid4

from django.conf import settings
from django.core.cache import caches
from django.test import override_settings

from rest_framework.test import APIClient

import pytest
from cryptography.fernet import Fernet
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from threadlocals.threadlocals import set_current_user, set_thread_variable

from apps.usuarios.factories import UsuarioFactory


@pytest.fixture(autouse=True)
def _isolar_usuario_da_thread():
    """Impede que o usuário de um teste vaze para o seguinte.

    `Base.save()` preenche `created_by` com `get_current_user()`, que vive num
    threadlocal populado pelo middleware de autenticação. Sem limpar entre
    testes, um teste que fez request autenticada deixa o usuário lá; o rollback
    apaga a linha e o teste seguinte grava um `created_by` órfão, estourando a
    FK na hora do commit/teardown.
    """
    set_current_user(None)
    set_thread_variable("request", None)
    yield
    set_current_user(None)
    set_thread_variable("request", None)


@pytest.fixture
def usuario(db):
    return UsuarioFactory(password="Senha123!")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture(autouse=True)
def sensitive_field_key(monkeypatch):
    monkeypatch.setenv("SENSITIVE_FIELD_KEYS", Fernet.generate_key().decode())


@pytest.fixture(scope="session")
def _redis_permission_cache_url():
    """Resolve a URL do Redis real uma vez por sessão e checa disponibilidade."""
    redis_url = os.environ.get("AUTHORIZATION_REDIS_URL") or (
        f"redis://{os.environ.get('REDIS_HOST', '127.0.0.1')}:{os.environ.get('REDIS_PORT', '6379')}/15"
    )
    client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)

    try:
        client.ping()
    except (RedisConnectionError, RedisTimeoutError):
        client.close()
        if os.environ.get("CI"):
            pytest.fail("Redis real indisponível no CI")
        pytest.skip("Redis real indisponível")

    client.close()
    return redis_url


@pytest.fixture
def redis_permission_cache(_redis_permission_cache_url):
    """Aponta o alias `permissions` para o Redis real durante um único teste.

    Precisa ser de escopo de função: `override_settings` troca o `settings`
    global por um `UserSettingsHolder`, cujo `SETTINGS_MODULE` é `None`. Mantido
    aberto por toda a sessão, isso vazaria para qualquer teste posterior que leia
    `settings.SETTINGS_MODULE`.
    """
    redis_url = _redis_permission_cache_url
    key_prefix = f"authz:test:{uuid4().hex}"
    client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)

    permission_cache = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": redis_url,
        "KEY_PREFIX": key_prefix,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "SERIALIZER": "django_redis.serializers.json.JSONSerializer",
            "REDIS_CLIENT_KWARGS": {
                "socket_connect_timeout": 1,
                "socket_timeout": 1,
            },
        },
    }
    configured_caches = {**settings.CACHES, "permissions": permission_cache}

    caches.close_all()
    with override_settings(CACHES=configured_caches):
        try:
            yield redis_url
        finally:
            caches.close_all()
            keys = list(client.scan_iter(match=f"{key_prefix}:*"))
            if keys:
                client.delete(*keys)
            client.close()
    caches.close_all()
