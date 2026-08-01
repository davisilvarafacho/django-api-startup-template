from rest_framework import status

from apps.api.autenticacao.errors import AuthErrorCode

from .errors import CoreErrorCode, error_response


def custom_400_handler(request, exception):
    return error_response(CoreErrorCode.BAD_REQUEST, status_code=status.HTTP_400_BAD_REQUEST)


def custom_401_handler(request, exception=None):
    return error_response(AuthErrorCode.NOT_AUTHENTICATED, status_code=status.HTTP_401_UNAUTHORIZED)


def custom_404_handler(request, exception):
    return error_response(CoreErrorCode.NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)


def custom_403_handler(request, exception):
    return error_response(AuthErrorCode.PERMISSION_DENIED, status_code=status.HTTP_403_FORBIDDEN)


def custom_500_handler(request):
    return error_response(CoreErrorCode.INTERNAL_ERROR, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
