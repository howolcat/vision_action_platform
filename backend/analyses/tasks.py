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
       (IP Webcam이면 저장 전에 브라우저 재생용 WebM 사본을 만들어 상세 화면에 연결)
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
from .video_playback import make_playback_copy

logger = logging.getLogger(__name__)


def _mark_failure(job: AnalysisJob, message: str) -> None:
    job.status = AnalysisJob.Status.FAILURE
    job.error_message = message
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at"])
    logger.warning("분석 실패: job=%s reason=%s", job.id, message)


def _prepare_video(job: AnalysisJob) -> tuple[Path, float | None]:
    """분석할 영상 파일 경로와 (IP Webcam이면) 실제 수집 속도(fps)를 돌려줍니다."""
    if job.input_type == AnalysisJob.InputType.UPLOAD:
        if not job.video:
            raise PredictionServiceError("업로드된 영상이 없습니다.")
        return Path(job.video.path), None

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

    # 실제 수집 속도 = 받은 프레임 수 ÷ 걸린 시간 (카메라가 알려 주는 fps는 부정확한 경우가 많음)
    actual_fps = info["frames"] / info["elapsed_seconds"] if info["elapsed_seconds"] > 0 else None
    return output_path, actual_fps


def _attach_playback_copy(job: AnalysisJob, video_path: Path | None, fps: float | None) -> None:
    """IP Webcam 수집 영상(mp4v)을 크롬이 재생할 수 있는 WebM으로 바꿔 상세 화면에 연결합니다.

    실패해도 분석 결과에는 영향이 없도록, 오류는 로그만 남기고 원본을 그대로 둡니다.
    """
    if job.input_type != AnalysisJob.InputType.WEBCAM or video_path is None or not Path(video_path).exists():
        return
    try:
        playback = make_playback_copy(video_path, fps=fps)
    except Exception:
        logger.exception("재생용 사본 생성 실패: job=%s", job.id)
        return
    if playback is None:
        return
    job.video.name = Path(playback).relative_to(settings.MEDIA_ROOT).as_posix()
    job.save(update_fields=["video"])
    Path(video_path).unlink(missing_ok=True)   # 분석이 끝난 mp4v 원본은 정리 (작업 삭제 시 남는 파일 방지)
    logger.info("재생용 WebM 연결: job=%s", job.id)


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

    video_path, capture_fps = None, None
    try:
        # 2) 영상 확보 → 3) FastAPI 추론
        video_path, capture_fps = _prepare_video(job)
        result = request_prediction(video_path)
    except (CameraCaptureError, PredictionServiceError) as error:
        _attach_playback_copy(job, video_path, capture_fps)   # 실패해도 무엇이 찍혔는지 볼 수 있게
        _mark_failure(job, str(error))
        return {"job_id": job_id, "status": AnalysisJob.Status.FAILURE}
    except Exception as error:   # 예상하지 못한 오류도 FAILURE로 남겨 "처리 중"에 멈추지 않게
        logger.exception("예상하지 못한 오류: job=%s", job_id)
        _mark_failure(job, f"처리 중 알 수 없는 오류가 발생했습니다: {error.__class__.__name__}")
        return {"job_id": job_id, "status": AnalysisJob.Status.FAILURE}

    # 재생용 사본 (분석은 이미 끝났으므로 결과에는 영향 없음)
    _attach_playback_copy(job, video_path, capture_fps)

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
