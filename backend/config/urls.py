"""
config/urls.py: 전체 URL 연결
==================================================
/admin/            Django Admin (관리자)
/accounts/...      회원가입·로그인·로그아웃
/                  분석 화면 (analyses.urls)
/analyses/<id>/    분석 상세 화면
/dashboard/        관리자 전용 대시보드
/api/analyses/...  DRF API
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Vision Action Platform 관리자"
admin.site.site_title = "Vision Action Admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("analyses.urls")),
]

# 개발 모드(DEBUG=1)에서만 업로드 영상을 Django가 직접 제공합니다.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
