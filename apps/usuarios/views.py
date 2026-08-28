from django.db import transaction

from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle
from rest_framework.views import APIView

from apps.api.autenticacao.recent_auth import RecentAuthenticationPermission, require_recent_auth
from apps.api.core.route_markers import no_tenancy, public

from .accounts import Contas
from .serializers import EmailChangeSerializer, EmailResendSerializer, EmailTokenSerializer

REACTIVATION_PUBLIC_DETAIL = "Se houver uma conta inativa com esse e-mail, o link de reativação foi enviado."


@public
class EmailVerifyView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = EmailTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Contas.verificar_email(serializer.validated_data["token"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@public
class EmailVerificationResendView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "auth_email_verification"

    def post(self, request):
        serializer = EmailResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        from apps.api.autenticacao.tasks import send_email_verification

        token = Contas.solicitar_verificacao_email(serializer.validated_data["email"])
        if token is not None:
            transaction.on_commit(lambda: send_email_verification.delay(token))

        return Response({"detail": "Se houver uma conta com esse e-mail, o link de verificação foi enviado."}, status=status.HTTP_202_ACCEPTED)


@no_tenancy
class EmailChangeView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request):
        serializer = EmailChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = Contas.solicitar_troca_email(request.user, serializer.validated_data["email"])
        from apps.api.autenticacao.tasks import send_email_change_confirmation

        transaction.on_commit(lambda: send_email_change_confirmation.delay(token))
        return Response(status=status.HTTP_202_ACCEPTED)


@public
class EmailChangeConfirmView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = EmailTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        usuario, previous_email = Contas.confirmar_troca_email(serializer.validated_data["token"])
        from apps.api.autenticacao.tasks import notify_email_changed

        transaction.on_commit(lambda: notify_email_changed.delay(usuario.email, previous_email))
        return Response(status=status.HTTP_204_NO_CONTENT)


@no_tenancy
class AccountDeactivateView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request):
        Contas.desativar(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@no_tenancy
class AccountDeletionView(APIView):
    permission_classes = (IsAuthenticated, RecentAuthenticationPermission)

    @require_recent_auth()
    def post(self, request):
        conta = Contas.agendar_exclusao(request.user)
        return Response(
            {"scheduled_for": conta.exclusao_agendada_para.isoformat()},
            status=status.HTTP_202_ACCEPTED,
        )


@public
class AccountReactivationView(APIView):
    permission_classes = (AllowAny,)
    throttle_classes = [AnonRateThrottle, ScopedRateThrottle]
    throttle_scope = "account_reactivation"

    def post(self, request):
        serializer = EmailResendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = Contas.solicitar_reativacao(serializer.validated_data["email"])
        if token is not None:
            from .tasks import send_account_reactivation

            transaction.on_commit(lambda: send_account_reactivation.delay(token))
        return Response({"detail": REACTIVATION_PUBLIC_DETAIL}, status=status.HTTP_202_ACCEPTED)


@public
class AccountReactivationConfirmView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        serializer = EmailTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Contas.confirmar_reativacao(serializer.validated_data["token"])
        return Response(status=status.HTTP_204_NO_CONTENT)
