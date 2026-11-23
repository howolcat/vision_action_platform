"""
analyses/camera_capture.py: IP Webcam에서 정해진 시간만 영상을 수집
==================================================
누가 카메라를 읽나요?     Celery Worker (브라우저가 아님)
언제 수집을 끝내나요?     연결 후 capture_seconds(기본 8초)가 지나면
결과는 어디에 저장하나요? MP4 파일로 저장 → 업로드 영상과 똑같이 FastAPI로 보냄

8초는 "입력 영상의 길이"입니다. 카메라 연결 시간과 AI 추론 시간은 따로 걸립니다.
"""

import time
from pathlib import Path

import cv2


class CameraCaptureError(Exception):
    """카메라 연결 실패·끊김 등. message는 사용자 화면에 보여 줄 문장입니다."""


MIN_FRAMES = 10   # 이보다 적게 받으면 분석 불가 (모델 min_valid_frames와 같은 기준)


def capture_clip(source_url: str, seconds: int, output_path: str | Path, connect_timeout_ms: int = 5000) -> dict:
    """source_url에서 seconds초 동안 Frame을 받아 output_path(MP4)에 저장합니다."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 연결·읽기 제한 시간을 걸어 카메라가 응답하지 않을 때 무한히 기다리지 않게 합니다.
    capture = cv2.VideoCapture(
        source_url,
        cv2.CAP_FFMPEG,
        [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, connect_timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, connect_timeout_ms,
        ],
    )
    if not capture.isOpened():
        raise CameraCaptureError("카메라에 연결할 수 없습니다. 휴대폰의 IP Webcam 서버가 켜져 있는지, 같은 네트워크인지 확인하세요.")

    writer = None
    frame_count = 0
    first_shape = None
    started = None

    try:
        while True:
            success, frame = capture.read()
            if not success:
                break   # 스트림 끊김 (또는 파일 끝)

            if started is None:
                started = time.monotonic()   # 첫 Frame을 받은 순간부터 시간을 잽니다.
                first_shape = frame.shape
                height, width = frame.shape[:2]
                # 수집 중에는 실제 FPS를 모르므로 우선 카메라가 알려 준 값으로 저장
                fps = capture.get(cv2.CAP_PROP_FPS)
                fps = fps if 1 <= fps <= 60 else 15.0
                writer = cv2.VideoWriter(str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

            if frame.shape != first_shape:
                continue   # 도중에 해상도가 바뀐 Frame은 건너뜀

            writer.write(frame)
            frame_count += 1

            if time.monotonic() - started >= seconds:
                break
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    elapsed = (time.monotonic() - started) if started else 0.0

    if frame_count < MIN_FRAMES:
        output_path.unlink(missing_ok=True)
        if frame_count == 0:
            raise CameraCaptureError("카메라에 연결됐지만 영상을 받지 못했습니다. 카메라 앱 화면이 켜져 있는지 확인하세요.")
        raise CameraCaptureError(f"카메라 연결이 중간에 끊겼습니다({frame_count}Frame 수집). 다시 시도하세요.")

    return {"frames": frame_count, "elapsed_seconds": round(elapsed, 2), "path": str(output_path)}
