"""
analyses/tasks.py: Celery 비동기 작업
==================================================
run_analysis(job_id) 처리 순서
    1. DB에서 AnalysisJob을 읽고 상태를 STARTED로 변경
    2. 분석할 영상 확보
         - 업로드: 이미 media 폴더에 저장된 파일
         - IP Webcam: camera_capture로 N초 수집 → media/webcam/ 에 MP4로 저장
    3. FastAPI /predict/video 호출 (services.py)
    4. 결과를 Django ORM으로 저장 → AnalysisJob(SUCCESS) + PersonPrediction(사람 수만큼)
    5. 실패하면 FAILURE + 사용자에게 보여 줄 오류 메시지 저장

FastAPI는 결과 JSON만 돌려주고, DB 저장은 여기(Celery Worker + Django ORM)에서만 합니다.
"""

import logging
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .camera_capture import CameraCaptureError, capture_clip
from .models import AnalysisJob, PersonPrediction
from .services import PredictionServiceError, request_prediction

logger = logging.getLogger(__name__)


def _mark_failure(job: AnalysisJob, message: str) -> None:
    job.status = AnalysisJob.Status.FAILURE
    job.error_message = message
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at"])
    logger.warning("분석 실패: job=%s reason=%s", job.id, message)


def _prepare_video(job: AnalysisJob) -> Path:
    """분석할 영상 파일 경로를 돌려줍니다. (업로드 / IP Webcam 공통 출구)"""
    if job.input_type == AnalysisJob.InputType.UPLOAD:
        if not job.video:
            raise PredictionServiceError("업로드된 영상이 없습니다.")
        return Path(job.video.path)

    # IP Webcam: 이름 → 주소 변환은 서버 설정에서만 (DB에는 주소를 저장하지 않음)
    source_url = settings.IP_WEBCAM_SOURCES.get(job.camera_name)
    if not source_url:
        raise CameraCaptureError("등록되지 않은 카메라입니다.")

    relative = Path("webcam") / f"{timezone.localtime():%Y/%m/%d}" / f"job_{job.id}.mp4"
    output_path = Path(settings.MEDIA_ROOT) / relative
    seconds = job.capture_seconds or settings.WEBCAM_CAPTURE_SECONDS

    info = capture_clip(source_url, seconds, output_path, settings.WEBCAM_CONNECT_TIMEOUT_MS)
    logger.info("웹캠 수집 완료: job=%s frames=%s %.1fs", job.id, info["frames"], info["elapsed_seconds"])

    # 수집한 영상을 작업에 연결 → 상세 화면에서 다시 볼 수 있음
    job.video.name = relative.as_posix()
    job.save(update_fields=["video"])
    return output_path


@shared_task(bind=True, name="analyses.run_analysis")
def run_analysis(self, job_id: int) -> dict:
    try:
        job = AnalysisJob.objects.get(pk=job_id)
    except AnalysisJob.DoesNotExist:
        logger.error("작업을 찾을 수 없습니다: job=%s", job_id)
        return {"job_id": job_id, "status": "MISSING"}

    # 1) 처리 시작
    job.status = AnalysisJob.Status.STARTED
    job.error_message = ""
    job.save(update_fields=["status", "error_message"])

    try:
        # 2) 영상 확보 → 3) FastAPI 추론
        video_path = _prepare_video(job)
        result = request_prediction(video_path)
    except (CameraCaptureError, PredictionServiceError) as error:
        _mark_failure(job, str(error))
        return {"job_id": job_id, "status": AnalysisJob.Status.FAILURE}
    except Exception as error:   # 예상하지 못한 오류도 FAILURE로 남겨 "처리 중"에 멈추지 않게
        logger.exception("예상하지 못한 오류: job=%s", job_id)
        _mark_failure(job, f"처리 중 알 수 없는 오류가 발생했습니다: {error.__class__.__name__}")
        return {"job_id": job_id, "status": AnalysisJob.Status.FAILURE}

    # 4) 결과 저장 - 작업 상태와 사람별 결과를 "한 번에" 저장 (중간에 실패하면 전부 취소)
    with transaction.atomic():
        job.predictions.all().delete()   # 같은 작업을 재실행해도 결과가 중복되지 않게
        PersonPrediction.objects.bulk_create(
            [
                PersonPrediction(
                    job=job,
                    track_id=item["track_id"],
                    action_label=item["action_label"],
                    confidence=item["confidence"],
                    is_uncertain=item["is_uncertain"],
                    valid_frames=item["valid_frames"],
                    probabilities=item.get("probabilities", {}),
                )
                for item in result.get("predictions", [])
            ]
        )
        job.status = AnalysisJob.Status.SUCCESS
        job.result_message = result.get("message", "")
        job.processing_ms = result.get("processing_ms")
        job.video_frames = (result.get("video") or {}).get("frames")
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "result_message", "processing_ms", "video_frames", "completed_at"])

    logger.info("분석 완료: job=%s people=%s", job_id, len(result.get("predictions", [])))
    return {"job_id": job_id, "status": AnalysisJob.Status.SUCCESS, "people": len(result.get("predictions", []))}
