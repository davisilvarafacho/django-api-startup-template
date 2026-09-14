from django.urls import path

from .views import (
    AccountDeactivateView,
    AccountDeletionView,
    AccountReactivationConfirmView,
    AccountReactivationView,
    EmailChangeConfirmView,
    EmailChangeView,
    EmailVerificationResendView,
    EmailVerifyView,
)

PUBLIC_ROUTES = [
    "/auth/email/verify/",
    "/auth/email/verification/resend/",
    "/account/email/change/confirm/",
    "/account/reactivation/",
    "/account/reactivation/confirm/",
]


urlpatterns = [
    path("auth/email/verify/", EmailVerifyView.as_view(), name="email-verify"),
    path("auth/email/verification/resend/", EmailVerificationResendView.as_view(), name="email-verification-resend"),
    path("account/email/change/", EmailChangeView.as_view(), name="email-change"),
    path("account/email/change/confirm/", EmailChangeConfirmView.as_view(), name="email-change-confirm"),
    path("account/deactivate/", AccountDeactivateView.as_view(), name="account-deactivate"),
    path("account/deletion/", AccountDeletionView.as_view(), name="account-deletion"),
    path("account/reactivation/", AccountReactivationView.as_view(), name="account-reactivation"),
    path("account/reactivation/confirm/", AccountReactivationConfirmView.as_view(), name="account-reactivation-confirm"),
]
