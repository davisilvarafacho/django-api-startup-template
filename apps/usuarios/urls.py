from django.urls import path

from .views import EmailChangeConfirmView, EmailChangeView, EmailVerificationResendView, EmailVerifyView

PUBLIC_ROUTES = [
    "/auth/email/verify/",
    "/auth/email/verification/resend/",
    "/account/email/change/confirm/",
]


urlpatterns = [
    path("auth/email/verify/", EmailVerifyView.as_view(), name="email-verify"),
    path("auth/email/verification/resend/", EmailVerificationResendView.as_view(), name="email-verification-resend"),
    path("account/email/change/", EmailChangeView.as_view(), name="email-change"),
    path("account/email/change/confirm/", EmailChangeConfirmView.as_view(), name="email-change-confirm"),
]
