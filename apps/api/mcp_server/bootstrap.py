import os

import django

import dotenv


def setup_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")
    dotenv.load_dotenv(".env", override=False)
    django.setup()
