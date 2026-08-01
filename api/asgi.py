import os

from django.core.asgi import get_asgi_application

import dotenv

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "api.settings")

dotenv.load_dotenv(".env")

application = get_asgi_application()
