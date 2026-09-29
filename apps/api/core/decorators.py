from rest_framework import status
from rest_framework.response import Response


def method_not_allowed(fn):
    def wrapper():
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)
    return wrapper
