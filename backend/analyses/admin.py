"""
analyses/admin.py: Django Admin 등록
==================================================
관리자(is_staff)는 /admin/ 에서 모든 사용자의 작업과 사람별 결과를 검색·필터할 수 있습니다.
AI 결과와 상태는 읽기 전용으로 두어 관리 화면에서도 실수로 바꾸지 않게 합니다.
"""

from django.contrib import admin

from .models import AnalysisJob, PersonPrediction


class PersonPredictionInline(admin.TabularInline):
    """작업 상세 화면 아래에 사람별 결과를 표로 보여 줍니다."""

    model = PersonPrediction
    extra = 0
    can_delete = False
    fields = ["track_id", "action_label", "confidence", "is_uncertain", "valid_frames"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AnalysisJob)
class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "user", "input_type", "status", "expected_action",
                    "prediction_count", "processing_ms", "created_at"]
    list_display_links = ["id", "title"]
    list_filter = ["status", "input_type", "expected_action", "created_at"]
    search_fields = ["title", "memo", "user__username", "celery_task_id"]
    date_hierarchy = "created_at"
    list_select_related = ["user"]
    inlines = [PersonPredictionInline]
    readonly_fields = ["user", "input_type", "video", "camera_name", "capture_seconds", "status",
                       "celery_task_id", "error_message", "result_message", "processing_ms",
                       "video_frames", "created_at", "completed_at"]
    fieldsets = [
        ("요청", {"fields": ["user", "title", "memo", "input_type", "video", "camera_name",
                             "capture_seconds", "expected_action"]}),
        ("처리 상태", {"fields": ["status", "celery_task_id", "error_message", "result_message",
                                  "processing_ms", "video_frames", "created_at", "completed_at"]}),
    ]

    @admin.display(description="사람 수")
    def prediction_count(self, job):
        return job.predictions.count()

    def has_add_permission(self, request):
        # 작업은 사용자 화면(분석 시작)에서만 만들어지게 합니다.
        return False


@admin.register(PersonPrediction)
class PersonPredictionAdmin(admin.ModelAdmin):
    list_display = ["id", "job", "job_user", "track_id", "action_label", "confidence",
                    "is_uncertain", "valid_frames", "created_at"]
    list_filter = ["action_label", "is_uncertain", "job__input_type", "created_at"]
    search_fields = ["job__title", "job__user__username"]
    list_select_related = ["job", "job__user"]
    readonly_fields = ["job", "track_id", "action_label", "confidence", "is_uncertain",
                       "valid_frames", "probabilities", "created_at"]

    @admin.display(description="요청 사용자", ordering="job__user__username")
    def job_user(self, prediction):
        return prediction.job.user

    def has_add_permission(self, request):
        return False
