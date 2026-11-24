from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import page_views
from .camera_preview import camera_preview
from .views import AnalysisJobViewSet

# Router가 목록/상세/생성/수정/삭제 URL과 @action(status) URL을 자동으로 만들어 줍니다.
router = DefaultRouter()
router.register("analyses", AnalysisJobViewSet, basename="analysis")

app_name = "analyses"

urlpatterns = [
    # 화면
    path("", page_views.index, name="index"),
    path("analyses/<int:pk>/", page_views.job_detail, name="detail"),
    path("dashboard/", page_views.dashboard, name="dashboard"),
    # IP Webcam 실시간 미리보기 (카메라 "이름"으로만 접근)
    path("cameras/<str:name>/preview/", camera_preview, name="camera-preview"),
    # API: /api/analyses/, /api/analyses/{id}/, /api/analyses/{id}/status/
    path("api/", include(router.urls)),
]