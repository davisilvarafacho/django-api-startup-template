import os

from django.core.wsgi import get_wsgi_application

import dotenv

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'api.settings')

dotenv.load_dotenv('.env')

application = get_wsgi_application()
