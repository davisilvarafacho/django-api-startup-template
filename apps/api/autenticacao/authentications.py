from django.conf import settings

from knox.auth import TokenAuthentication


class QueryParamTokenAuthentication(TokenAuthentication):
    def authenticate(self, request):
        token = request.query_params.get("token")
        if not token:
            return None

        user, auth_token = self.authenticate_credentials(token.encode())

        if user.email not in settings.ADMINS_EMAILS:
            return None

        return (user, auth_token)
