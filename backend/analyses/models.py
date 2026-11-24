from django.conf import settings
from django.db import models


# ==================================================
# 6개 동작 (두 모델이 함께 사용)
# ==================================================
# 왼쪽 값("bend_return")은 DB에 저장되는 값 → FastAPI의 model.classes_와 철자가 똑같아야 함
# 오른쪽 값("허리 굽혔다 펴기")은 Admin·화면에 보이는 이름
class ActionLabel(models.TextChoices):
    BEND_RETURN = "bend_return", "허리 굽혔다 펴기"
    DRINK_RETURN = "drink_return", "음료 마시고 돌아오기"
    LEG_RAISE_LOWER = "leg_raise_lower", "다리 들었다 내리기"
    SIT_STAND = "sit_stand", "앉았다 일어나기"
    WALK_TURN_WALK = "walk_turn_walk", "걷다가 돌아 걷기"
    WAVE = "wave", "손 흔들기"


# ==================================================
# 분석 요청 1건 (사용자가 "AI 분석 시작"을 한 번 누를 때마다 1행)
# ==================================================
class AnalysisJob(models.Model):
    class InputType(models.TextChoices):
        UPLOAD = "UPLOAD", "영상 업로드"
        WEBCAM = "WEBCAM", "IP Webcam"

    # 프로젝트 전체에서 쓸 상태 표기 (Celery의 상태 이름과 맞춤)
    class Status(models.TextChoices):
        PENDING = "PENDING", "대기"
        STARTED = "STARTED", "처리 중"
        SUCCESS = "SUCCESS", "완료"
        FAILURE = "FAILURE", "실패"

    # --- 누가 요청했나 ---
    # 사용자 1명 → 작업 여러 개 (1:N). 사용자를 지우면 그 사람의 작업도 같이 삭제(CASCADE)
    # related_name 덕분에 user.analysis_jobs.all() 로 "이 사람의 작업 목록"을 가져올 수 있음
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="analysis_jobs",
        verbose_name="요청 사용자",
    )

    # --- 사용자가 수정할 수 있는 정보 (DRF PATCH 대상) ---
    title = models.CharField("제목", max_length=100)
    memo = models.TextField("메모", blank=True)

    # --- 무엇을 분석하나 ---
    input_type = models.CharField("입력 방식", max_length=10, choices=InputType.choices)
    # 영상 파일 자체는 media/videos/년/월/일/ 폴더에 저장되고, DB에는 "경로 문자열"만 저장됨
    video = models.FileField("영상 파일", upload_to="videos/%Y/%m/%d/", blank=True)
    # 카메라 URL은 저장하지 않음(접속정보 보호) → 허용 목록의 "이름"만 저장
    camera_name = models.CharField("카메라 이름", max_length=50, blank=True)
    capture_seconds = models.PositiveIntegerField("수집 시간(초)", null=True, blank=True)
    # 사용자가 "이 동작을 했다"고 고른 값 (검증된 정답 아님, 선택 안 해도 됨)
    expected_action = models.CharField(
        "사용자가 선택한 동작",
        max_length=20,
        choices=ActionLabel.choices,
        blank=True,
        default="",
    )

    # --- 처리 상태 (Celery Worker만 바꾸는 값) ---
    status = models.CharField("상태", max_length=10, choices=Status.choices, default=Status.PENDING)
    celery_task_id = models.CharField("Celery 작업 ID", max_length=255, blank=True)
    error_message = models.TextField("오류 메시지", blank=True)       # 실패 사유
    result_message = models.CharField("결과 메시지", max_length=255, blank=True)  # "사람을 찾지 못했습니다" 등

    # --- FastAPI 응답에서 가져오는 정보 ---
    processing_ms = models.PositiveIntegerField("추론 시간(ms)", null=True, blank=True)
    video_frames = models.PositiveIntegerField("영상 Frame 수", null=True, blank=True)

    # --- 시간 ---
    created_at = models.DateTimeField("요청 시각", auto_now_add=True)  # 생성 순간 자동 기록
    completed_at = models.DateTimeField("완료 시각", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]          # 최신 작업이 먼저
        verbose_name = "분석 작업"           # Admin에 보일 이름
        verbose_name_plural = "분석 작업"

    def __str__(self):
        # get_status_display(): "PENDING" 대신 "대기"처럼 화면용 이름을 돌려줌
        return f"#{self.pk} {self.title} ({self.get_status_display()})"

    @property
    def top_prediction(self):
        """사람별 예측 중 Confidence가 가장 높은 1개 (비교 화면·이력 표의 "대표 예측"). 없으면 None

        @property는 DB 칸이 아니라 "계산해서 보여 주는 값"이라 마이그레이션이 필요 없습니다.
        prefetch_related("predictions")로 미리 불러왔다면 DB를 다시 조회하지 않습니다.
        """
        predictions = list(self.predictions.all())
        return max(predictions, key=lambda p: p.confidence) if predictions else None


# ==================================================
# 작업 안의 사람 1명 결과 (영상에 2명이면 작업 1건에 2행)
# ==================================================
class PersonPrediction(models.Model):
    # 작업 1개 → 사람별 결과 여러 개 (1:N). 작업을 지우면 결과도 같이 삭제
    # job.predictions.all() 로 "이 작업의 사람별 결과"를 가져올 수 있음
    job = models.ForeignKey(
        AnalysisJob,
        on_delete=models.CASCADE,
        related_name="predictions",
        verbose_name="분석 작업",
    )
    # 영상 안에서만 의미 있는 추적 번호 (영구적인 사람 ID가 아님!)
    track_id = models.PositiveIntegerField("Track ID")
    action_label = models.CharField("예측 동작", max_length=20, choices=ActionLabel.choices)
    confidence = models.FloatField("Confidence")
    is_uncertain = models.BooleanField("판단 불확실", default=False)
    valid_frames = models.PositiveIntegerField("유효 Frame 수")
    # 6개 동작의 확률 전체를 {"wave": 0.63, ...} 형태로 저장 (오류 분석용)
    probabilities = models.JSONField("동작별 확률", default=dict, blank=True)
    created_at = models.DateTimeField("저장 시각", auto_now_add=True)

    class Meta:
        ordering = ["job", "track_id"]
        verbose_name = "사람별 예측"
        verbose_name_plural = "사람별 예측"
        # 같은 작업 안에서 track_id가 두 번 저장되지 않게 DB가 막아 줌
        constraints = [
            models.UniqueConstraint(fields=["job", "track_id"], name="unique_track_per_job"),
        ]

    def __str__(self):
        return f"Job#{self.job_id} Track {self.track_id}: {self.action_label} ({self.confidence:.2f})"