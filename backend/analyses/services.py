"""
analyses/services.py: FastAPI 추론 서버 호출
==================================================
Celery Worker가 영상 파일을 HTTP(multipart)로 FastAPI에 보냅니다.

왜 파일 경로가 아니라 파일 자체를 보내나요?
    FastAPI 컨테이너는 Django·Celery와 파일 시스템을 공유하지 않습니다.
    경로("/app/media/videos/a.mp4")만 보내면 FastAPI 쪽에는 그 파일이 없습니다.
    또 컨테이너마다 localhost가 "자기 자신"이라 주소는 서비스 이름(http://fastapi:8000)을 씁니다.
"""

from pathlib import Path

import requests
from django.conf import settings


class PredictionServiceError(Exception):
    """FastAPI 호출이 실패했을 때. message는 사용자 화면에 그대로 보여 줄 문장입니다."""


def request_prediction(video_path: str | Path) -> dict:
    video_path = Path(video_path)
    url = f"{settings.FASTAPI_URL}/predict/video"

    try:
        with video_path.open("rb") as file:
            response = requests.post(
                url,
                files={"file": (video_path.name, file, "video/mp4")},
                timeout=settings.FASTAPI_TIMEOUT,
            )
    except FileNotFoundError as error:
        raise PredictionServiceError("분석할 영상 파일을 찾을 수 없습니다.") from error
    except requests.Timeout as error:
        raise PredictionServiceError(
            f"AI 서버 응답 시간이 {settings.FASTAPI_TIMEOUT}초를 넘었습니다. 더 짧은 영상으로 다시 시도하세요."
        ) from error
    except requests.ConnectionError as error:
        raise PredictionServiceError("AI 서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.") from error

    if response.status_code == 200:
        return response.json()

    # FastAPI가 알려 준 실패 사유(detail)를 그대로 사용자에게 전달
    try:
        detail = response.json().get("detail", "")
    except ValueError:
        detail = ""
    if not isinstance(detail, str):
        detail = str(detail)
    raise PredictionServiceError(detail or f"AI 서버 오류가 발생했습니다(HTTP {response.status_code}).")
