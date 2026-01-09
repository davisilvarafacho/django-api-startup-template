from django.urls import include, path

from rest_framework.routers import DefaultRouter

from knox import views as knox_views

from .views import AuthTokenViewSet, LoginView

router = DefaultRouter()
router.register("tokens", AuthTokenViewSet, "auth_tokens")


urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="knox_login"),
    path("auth/logout/", knox_views.LogoutView.as_view(), name="knox_logout"),
    path("auth/logoutall/", knox_views.LogoutAllView.as_view(), name="knox_logoutall"),
    path("auth/", include(router.urls)),
]
