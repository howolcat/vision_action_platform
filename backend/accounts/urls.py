from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("signup/", views.SignUpView.as_view(), name="signup"),
    # 템플릿: templates/registration/login.html
    path(
        "login/",
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name="login",
    ),
    # Django 5 이상에서 로그아웃은 POST로만 가능합니다. (base.html의 로그아웃 버튼이 form POST)
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/me/", views.me, name="api-me"),
]
