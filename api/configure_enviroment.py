def configure_devlopment_environment():
    dev_middlewares = [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        "zeal.middleware.zeal_middleware",
        "django_cprofile_middleware.middleware.ProfilerMiddleware",
        "silk.middleware.SilkyMiddleware",
        "drf_api_logger.middleware.api_logger_middleware.APILoggerMiddleware",
        "hijack.middleware.HijackUserMiddleware",
    ]

    dev_apps = [
        "django_extensions",
        "debug_toolbar",
        "drf_api_logger",
        "hijack",
        "hijack.contrib.admin",
        "silk",
        "zeal",
    ]

    dev_storages = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
    }

    return dev_middlewares, dev_apps, dev_storages


def configure_production_environment():
    prod_middlewares = []

    prod_apps = [
        "cachalot",
        "dbbackup",
    ]

    prod_storages = {
        "default": {
            "BACKEND": "apps.api.core.b2_storage.BackblazeB2Storage",
        },
        "dbbackup": {
            "BACKEND": "apps.api.core.b2_storage.BackblazeB2Storage",
            "OPTIONS": {
                "location": "backups/",
            },
        },
    }

    return prod_middlewares, prod_apps, prod_storages


def configure_test_environment():
    test_middlewares = []

    test_apps = []

    test_storages = {
        "default": {
            "BACKEND": "django.core.files.storage.InMemoryStorage",
        },
    }

    return test_middlewares, test_apps, test_storages


def configure_enviroment(environment):
    if environment == "development":
        return configure_devlopment_environment()

    if environment == "production":
        return configure_production_environment()

    if environment == "test":
        return configure_test_environment()

    raise NotImplementedError(f"Environment '{environment}' is not supported.")


def build_checkout_variants(*, api_key: str, webhook_secret: str, sandbox: bool, in_production: bool) -> dict:
    """Monta `CHECKOUT_VARIANTS` do django-checkouts para o ambiente.

    Sem chave fora de produção a variante Stripe não é registrada: o check da
    lib acusaria erro e impediria `manage.py` (migrate, makemigrations, runserver)
    em dev, CI e test. Em produção a variante é sempre registrada, para que uma
    chave faltando falhe alto no check.

    Args:
        api_key: Chave secreta da Stripe (`STRIPE_SECRET_KEY`).
        webhook_secret: Segredo de webhook da Stripe (`STRIPE_WEBHOOK_SECRET`).
        sandbox: Se o gateway opera em sandbox (`STRIPE_SANDBOX`). Com chave live
            e sandbox ligado, a lib emite aviso.
        in_production: Se o ambiente efetivo é produção.

    Returns:
        O dict de variantes: vazio sem chave fora de produção, com a entrada
        `stripe` nos demais casos.
    """
    if not api_key and not in_production:
        return {}
    return {
        "stripe": (
            "django_checkouts.gateways.stripe.StripeGateway",
            {"api_key": api_key, "webhook_secret": webhook_secret, "sandbox": sandbox},
        ),
    }
