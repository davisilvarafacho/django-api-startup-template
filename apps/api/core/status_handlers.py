from django.http import JsonResponse

from rest_framework import status


def custom_400_handler(request, exception):
    return JsonResponse({"mensagem": "Acesso inválido"}, status=status.HTTP_400_BAD_REQUEST)


def custom_401_handler(request, exception=None):
    return JsonResponse({"mensagem": "Não autorizado"}, status=status.HTTP_401_UNAUTHORIZED)


def custom_404_handler(request, exception):
    return JsonResponse({"mensagem": "Endpoint não encontrado"}, status=status.HTTP_400_BAD_REQUEST)


def custom_403_handler(request, exception):
    return JsonResponse({"mensagem": "Acesso negado"}, status=status.HTTP_403_FORBIDDEN)


def custom_500_handler(request):
    return JsonResponse({"mensagem": "Erro interno"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
