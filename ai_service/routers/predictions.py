"""
routers/predictions.py
==================================================
POST /predict/video : Celery Worker가 보낸 영상 1건을 받아 사람별 예측을 JSON으로 반환

이 파일의 역할은 "입구 관리"입니다.
    - 들어온 파일이 처리 가능한지 확인하고 (확장자·크기)
    - 임시 파일로 저장해서 서비스 함수에 넘기고
    - 결과를 약속한 JSON 형식(API 계약)으로 돌려줍니다.
실제 영상 처리·예측은 services/ 폴더가 담당합니다.

HTTP 상태 코드로 상황을 구분합니다. (Celery Worker가 이 코드를 보고 판단)
    200 : 처리 성공 (사람이 없으면 predictions가 빈 목록)
    400 : 지원하지 않는 파일 형식
    413 : 파일이 너무 큼
    422 : 영상을 읽을 수 없음 (손상 등)
    503 : 모델이 준비되지 않음
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from services.action_inference import UNCERTAIN_THRESHOLD, predict_tracks
from services.pose_pipeline import VideoReadError, extract_track_features

router = APIRouter(prefix="/predict", tags=["predictions"])

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv"}
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))


# ---------- 응답 형식(API 계약) ----------
# Pydantic 모델로 적어 두면 /docs에 응답 예시가 자동으로 나타납니다.
class VideoInfo(BaseModel):
    frames: int
    fps: float
    width: int
    height: int
    truncated: bool


class PersonPrediction(BaseModel):
    track_id: int
    action_label: str
    confidence: float
    is_uncertain: bool
    valid_frames: int
    first_frame: int
    last_frame: int
    probabilities: dict[str, float]


class SkippedTrack(BaseModel):
    track_id: int
    valid_frames: int


class PredictResponse(BaseModel):
    status: str
    message: str
    processing_ms: int
    uncertain_threshold: float
    video: VideoInfo
    predictions: list[PersonPrediction]
    skipped_tracks: list[SkippedTrack]


# ---------- 엔드포인트 ----------
# async def가 아니라 일반 def로 만든 이유:
#   YOLO·Random Forest 계산은 CPU를 오래 쓰는 "막히는(blocking)" 작업입니다.
#   일반 def로 두면 FastAPI가 별도 스레드에서 실행해 주므로
#   추론 중에도 /health 같은 다른 요청에 응답할 수 있습니다.
@router.post("/video", response_model=PredictResponse)
def predict_video(request: Request, file: UploadFile = File(...)) -> PredictResponse:
    bundle = request.app.state.bundle
    if bundle is None:
        raise HTTPException(status_code=503, detail="모델이 준비되지 않았습니다. /health를 확인하세요.")

    # ① 확장자 검사
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 형식입니다({suffix or '확장자 없음'}). 허용: {sorted(ALLOWED_EXTENSIONS)}",
        )

    started = time.perf_counter()

    # ② OpenCV는 "파일 경로"로 영상을 열기 때문에 임시 파일에 저장합니다.
    #    with 블록이 끝나면 임시 폴더가 통째로 삭제됩니다.
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = Path(tmp_dir) / f"input{suffix}"
        with video_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        # ③ 크기 검사
        size_mb = video_path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            raise HTTPException(status_code=413, detail=f"파일이 너무 큽니다({size_mb:.1f}MB > {MAX_UPLOAD_MB}MB).")
        if size_mb == 0:
            raise HTTPException(status_code=422, detail="빈 파일입니다.")

        # ④ 영상 → 사람별 Feature
        try:
            pose_result = extract_track_features(video_path, bundle.pose_weights, bundle.pose_config)
        except VideoReadError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    # ⑤ 사람별 Feature → 동작 예측
    predictions, skipped = predict_tracks(bundle, pose_result)

    # ⑥ 사람이 없을 때와 정보가 부족할 때를 메시지로 구분
    if predictions:
        message = ""
    elif skipped:
        message = f"사람은 검출됐지만 유효 Frame이 {bundle.min_valid_frames}개 미만이라 판단하지 않았습니다."
    else:
        message = "영상에서 사람을 찾지 못했습니다."

    return PredictResponse(
        status="ok",
        message=message,
        processing_ms=int((time.perf_counter() - started) * 1000),
        uncertain_threshold=UNCERTAIN_THRESHOLD,
        video=VideoInfo(
            frames=pose_result.frame_count,
            fps=round(pose_result.fps, 2),
            width=pose_result.width,
            height=pose_result.height,
            truncated=pose_result.truncated,
        ),
        predictions=predictions,
        skipped_tracks=skipped,
    )
