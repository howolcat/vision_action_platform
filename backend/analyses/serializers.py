"""
analyses/serializers.py
==================================================
Serializer = "DB 객체 ↔ JSON" 변환기 + 입력 검사기

역할 분담:
    PersonPredictionSerializer     사람별 결과를 JSON으로 (읽기 전용)
    AnalysisJobSerializer          분석 요청 생성(입력 검사) + 조회 응답
    AnalysisJobUpdateSerializer    PATCH: 제목·메모만 수정 가능
    AnalysisJobStatusSerializer    상태 조회 API용 가벼운 응답
"""

from pathlib import Path

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from .models import AnalysisJob, PersonPrediction


class PersonPredictionSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source="get_action_label_display", read_only=True)

    class Meta:
        model = PersonPrediction
        fields = [
            "track_id",
            "action_label",
            "action_display",
            "confidence",
            "is_uncertain",
            "valid_frames",
            "probabilities",
        ]
        read_only_fields = fields


class AnalysisJobSerializer(serializers.ModelSerializer):
    """생성(POST)과 조회(GET)에 사용합니다."""

    predictions = PersonPredictionSerializer(many=True, read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    input_type_display = serializers.CharField(source="get_input_type_display", read_only=True)
    expected_action_display = serializers.CharField(source="get_expected_action_display", read_only=True)
    video_url = serializers.SerializerMethodField()
    is_finished = serializers.SerializerMethodField()
    top_prediction = serializers.SerializerMethodField()   # 이력 표·비교용 대표 예측
    title = serializers.CharField(max_length=100, required=False, allow_blank=True)

    class Meta:
        model = AnalysisJob
        fields = [
            "id",
            "title",
            "memo",
            "input_type",
            "input_type_display",
            "video",
            "video_url",
            "camera_name",
            "capture_seconds",
            "expected_action",
            "expected_action_display",
            "status",
            "status_display",
            "is_finished",
            "celery_task_id",
            "error_message",
            "result_message",
            "processing_ms",
            "video_frames",
            "created_at",
            "completed_at",
            "top_prediction",
            "predictions",
        ]
        # ⭐ 사용자가 보낼 수 없는(서버만 바꾸는) 값들
        #    상태·AI 결과를 사용자가 조작하면 DB 기록과 통계를 믿을 수 없게 됩니다.
        read_only_fields = [
            "status",
            "celery_task_id",
            "error_message",
            "result_message",
            "processing_ms",
            "video_frames",
            "created_at",
            "completed_at",
        ]
        extra_kwargs = {"video": {"write_only": True, "required": False}}

    def get_video_url(self, job) -> str:
        return job.video.url if job.video else ""

    def get_is_finished(self, job) -> bool:
        return job.status in (AnalysisJob.Status.SUCCESS, AnalysisJob.Status.FAILURE)

    def get_top_prediction(self, job):
        top = job.top_prediction
        if top is None:
            return None
        return {
            "track_id": top.track_id,
            "action_label": top.action_label,
            "action_display": top.get_action_label_display(),
            "confidence": top.confidence,
            "is_uncertain": top.is_uncertain,
        }

    # ---------- 입력 검사 ----------
    def validate(self, attrs):
        input_type = attrs.get("input_type")

        if input_type == AnalysisJob.InputType.UPLOAD:
            video = attrs.get("video")
            if not video:
                raise serializers.ValidationError({"video": "분석할 영상 파일을 선택하세요."})

            suffix = Path(video.name).suffix.lower()
            if suffix not in settings.ALLOWED_VIDEO_EXTENSIONS:
                raise serializers.ValidationError(
                    {"video": f"지원하지 않는 형식입니다({suffix or '확장자 없음'}). "
                              f"허용: {', '.join(settings.ALLOWED_VIDEO_EXTENSIONS)}"}
                )
            if video.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise serializers.ValidationError({"video": f"{settings.MAX_UPLOAD_MB}MB 이하 파일만 올릴 수 있습니다."})

            # 업로드 방식에서는 카메라 정보를 쓰지 않습니다.
            attrs["camera_name"] = ""
            attrs["capture_seconds"] = None

        elif input_type == AnalysisJob.InputType.WEBCAM:
            camera_name = attrs.get("camera_name", "")
            # ⭐ 서버에 등록된 카메라 이름만 허용 → 임의의 외부 URL 호출 차단
            if camera_name not in settings.IP_WEBCAM_SOURCES:
                raise serializers.ValidationError({"camera_name": "등록된 카메라가 아닙니다. 관리자에게 문의하세요."})

            seconds = attrs.get("capture_seconds") or settings.WEBCAM_CAPTURE_SECONDS
            if not settings.WEBCAM_MIN_SECONDS <= seconds <= settings.WEBCAM_MAX_SECONDS:
                raise serializers.ValidationError(
                    {"capture_seconds": f"수집 시간은 {settings.WEBCAM_MIN_SECONDS}~{settings.WEBCAM_MAX_SECONDS}초입니다."}
                )
            attrs["capture_seconds"] = seconds

            if attrs.get("video"):
                raise serializers.ValidationError({"video": "IP Webcam 분석에는 파일을 올리지 않습니다."})

        # 제목을 비워 두면 자동으로 만들어 줍니다.
        if not attrs.get("title"):
            label = "업로드" if input_type == AnalysisJob.InputType.UPLOAD else "IP Webcam"
            attrs["title"] = f"{label} 분석 {timezone.localtime():%m-%d %H:%M}"

        return attrs


class AnalysisJobUpdateSerializer(serializers.ModelSerializer):
    """PATCH /api/analyses/{id}/ : 제목·메모만 수정할 수 있습니다."""

    class Meta:
        model = AnalysisJob
        fields = ["title", "memo"]


class AnalysisJobStatusSerializer(serializers.ModelSerializer):
    """GET /api/analyses/{id}/status/ : 브라우저가 2초마다 호출하는 가벼운 응답"""

    status_display = serializers.CharField(source="get_status_display", read_only=True)
    is_finished = serializers.SerializerMethodField()
    prediction_count = serializers.IntegerField(source="predictions.count", read_only=True)

    class Meta:
        model = AnalysisJob
        fields = [
            "id",
            "status",
            "status_display",
            "is_finished",
            "error_message",
            "result_message",
            "prediction_count",
            "completed_at",
        ]
        read_only_fields = fields

    def get_is_finished(self, job) -> bool:
        return job.status in (AnalysisJob.Status.SUCCESS, AnalysisJob.Status.FAILURE)
