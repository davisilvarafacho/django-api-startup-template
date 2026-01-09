from django.utils import timezone


class UpdateTokenLastUsedMiddleware:
    """Atualiza o campo last_used toda vez que o token é usado"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'auth') and request.auth:
            if hasattr(request.auth, 'metadata'):
                request.auth.metadata.last_used = timezone.now()
                request.auth.metadata.save(update_fields=['last_used'])

        response = self.get_response(request)
        return response
