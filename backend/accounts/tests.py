"""
회원 기능 테스트 (실행: python manage.py test accounts)
"""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SignUpLoginTests(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("signup"),
            {"username": "student1", "password1": "VisionPass!2026", "password2": "VisionPass!2026"},
        )
        self.assertRedirects(response, reverse("analyses:index"))
        self.assertTrue(User.objects.filter(username="student1").exists())
        # 가입 직후 자동 로그인 → 본인 정보 API 접근 가능
        self.assertEqual(self.client.get(reverse("api-me")).json()["username"], "student1")

    def test_signup_password_mismatch_shows_error(self):
        response = self.client.post(
            reverse("signup"),
            {"username": "student2", "password1": "VisionPass!2026", "password2": "different!2026"},
        )
        self.assertEqual(response.status_code, 200)   # 가입 실패 → 같은 화면에 오류 표시
        self.assertFalse(User.objects.filter(username="student2").exists())

    def test_login_and_logout(self):
        User.objects.create_user("student3", password="VisionPass!2026")
        response = self.client.post(reverse("login"), {"username": "student3", "password": "VisionPass!2026"})
        self.assertRedirects(response, reverse("analyses:index"))
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("analyses:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_anonymous_api_is_forbidden(self):
        self.assertIn(self.client.get(reverse("api-me")).status_code, (401, 403))
