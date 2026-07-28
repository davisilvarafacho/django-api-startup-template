from django.urls import include, path

from rest_framework.routers import DefaultRouter

from .views import APIKeyViewSet, LoginView, LogoutAllView, LogoutView, ReauthenticateView, SessionViewSet

router = DefaultRouter()
router.register("sessions", SessionViewSet, "auth_sessions")
router.register("api_keys", APIKeyViewSet, "auth_api_keys")


urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/reauthenticate/", ReauthenticateView.as_view(), name="reauthenticate"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/logout_all/", LogoutAllView.as_view(), name="logout_all"),
    path("auth/", include(router.urls)),
]
