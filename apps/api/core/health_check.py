from django.db import connection
from django.http import JsonResponse

from rest_framework import status


def health_check(request):
    connection.ensure_connection()
    return JsonResponse({"ok": True}, status=status.HTTP_200_OK)
