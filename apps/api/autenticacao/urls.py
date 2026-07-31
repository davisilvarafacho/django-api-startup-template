from django.urls import include, path

from rest_framework.routers import DefaultRouter

from knox import views as knox_views

from .views import (
    AuthTokenViewSet,
    LoginView,
    MFAAdminResetView,
    MFAChallengeStartView,
    MFAChallengeVerifyView,
    MFAFactorConfirmView,
    MFAFactorDeleteView,
    MFAFactorSetupView,
    MFARecoveryCodesView,
    ReauthenticateChallengeStartView,
    ReauthenticateChallengeVerifyView,
    ReauthenticateView,
    TrustedDeviceDetailView,
    TrustedDeviceListView,
)

router = DefaultRouter()
router.register("tokens", AuthTokenViewSet, "auth_tokens")


urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="knox_login"),
    path("auth/reauthenticate/", ReauthenticateView.as_view(), name="reauthenticate"),
    path("auth/reauthenticate/challenge/start/", ReauthenticateChallengeStartView.as_view(), name="reauthenticate-challenge-start"),
    path("auth/reauthenticate/challenge/verify/", ReauthenticateChallengeVerifyView.as_view(), name="reauthenticate-challenge-verify"),
    path("auth/mfa/factors/<str:factor_type>/setup/", MFAFactorSetupView.as_view(), name="mfa-factor-setup"),
    path("auth/mfa/factors/<str:factor_type>/confirm/", MFAFactorConfirmView.as_view(), name="mfa-factor-confirm"),
    path("auth/mfa/factors/<str:factor_type>/", MFAFactorDeleteView.as_view(), name="mfa-factor-delete"),
    path("auth/mfa/recovery-codes/", MFARecoveryCodesView.as_view(), name="mfa-recovery-codes"),
    path("auth/mfa/challenge/start/", MFAChallengeStartView.as_view(), name="mfa-challenge-start"),
    path("auth/mfa/challenge/verify/", MFAChallengeVerifyView.as_view(), name="mfa-challenge-verify"),
    path("auth/mfa/admin-reset/", MFAAdminResetView.as_view(), name="mfa-admin-reset"),
    path("auth/trusted-devices/", TrustedDeviceListView.as_view(), name="trusted-device-list"),
    path("auth/trusted-devices/<int:pk>/", TrustedDeviceDetailView.as_view(), name="trusted-device-detail"),
    path("auth/logout/", knox_views.LogoutView.as_view(), name="knox_logout"),
    path("auth/logoutall/", knox_views.LogoutAllView.as_view(), name="knox_logoutall"),
    path("auth/", include(router.urls)),
]
