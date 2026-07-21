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
