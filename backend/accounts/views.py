"""
accounts/views.py: 회원가입 + 본인 정보 API
==================================================
로그인·로그아웃은 Django가 제공하는 LoginView, LogoutView를 그대로 사용합니다. (accounts/urls.py)
회원 테이블도 Django 기본 User(auth_user)를 그대로 씁니다.
"""

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView
from rest_framework.decorators import api_view
from rest_framework.response import Response


class SignUpForm(UserCreationForm):
    """Django 기본 회원가입 폼 (아이디 + 비밀번호 2번)"""

    class Meta(UserCreationForm.Meta):
        fields = ("username",)


class SignUpView(CreateView):
    form_class = SignUpForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("analyses:index")

    def dispatch(self, request, *args, **kwargs):
        # 이미 로그인한 사용자는 가입 화면 대신 분석 화면으로
        if request.user.is_authenticated:
            return redirect("analyses:index")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)   # 사용자 저장
        login(self.request, self.object)      # 가입 직후 자동 로그인
        return response


@api_view(["GET"])
def me(request):
    """GET /accounts/api/me/ : 로그인한 본인 정보 (기본 권한 IsAuthenticated 적용)"""
    user = request.user
    return Response(
        {
            "id": user.id,
            "username": user.username,
            "is_staff": user.is_staff,
            "date_joined": user.date_joined,
            "analysis_count": user.analysis_jobs.count(),
        }
    )
