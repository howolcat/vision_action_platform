"""
main.py
==================================================
FastAPI 앱 시작점

실행 방법 (반드시 ai_service 폴더 안에서):
    cd ai_service
    uvicorn main:app --reload --port 8001
    → http://localhost:8001/docs

왜 ai_service 폴더 안에서 실행해야 하나요?
    src/ 파일들이 `from src.feature_utils import ...` 형태로 서로를 import합니다.
    파이썬은 "현재 실행 위치"를 기준으로 src 폴더를 찾기 때문입니다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from routers import predictions
from services.model_loader import ModelLoadError, load_model_bundle

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버가 켜질 때 한 번 실행되고(yield 앞), 꺼질 때 정리합니다(yield 뒤)."""
    app.state.bundle = None
    app.state.load_error = None
    try:
        # 모델을 한 번만 불러와 app.state에 보관 → 모든 요청이 재사용
        app.state.bundle = load_model_bundle()
        logger.info(
            "모델 준비 완료: %d classes, %d features",
            len(app.state.bundle.classes),
            len(app.state.bundle.feature_columns),
        )
    except (ModelLoadError, FileNotFoundError, KeyError) as error:
        # 서버를 죽이지 않고 에러를 기록해 둡니다. → /health에서 원인을 확인할 수 있습니다.
        app.state.load_error = str(error)
        logger.error("모델 로드 실패: %s", error)
    yield


app = FastAPI(
    title="Vision Action AI Service",
    description="YOLO Pose + Random Forest(6일차) 동작 인식 추론 서버",
    lifespan=lifespan,
)

app.include_router(predictions.router)


@app.get("/health", tags=["health"])
def health():
    """서버가 살아 있는지 + 추론할 준비가 됐는지 확인합니다.

    모델이 준비되지 않았으면 503을 반환합니다.
    (나중에 Docker healthcheck가 이 응답 코드로 상태를 판단할 수 있습니다.)
    """
    bundle = app.state.bundle
    if bundle is None:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "model_ready": False, "error": app.state.load_error},
        )

    return {
        "status": "ok",
        "model_ready": True,
        "classes": bundle.classes,
        "feature_count": len(bundle.feature_columns),
        "sequence_steps": bundle.sequence_steps,
        "min_valid_frames": bundle.min_valid_frames,
        "pose_model": bundle.pose_weights.name,
    }
