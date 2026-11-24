"""
analyses/video_playback.py: 브라우저 재생용 영상 사본 만들기
==================================================
문제
    IP Webcam 수집 영상은 빠르게 저장하려고 'mp4v' 코덱(MPEG-4 Part 2)으로 씁니다.
    OpenCV·FastAPI는 이 형식을 잘 읽지만, 크롬의 <video>는 재생하지 못합니다. (0:00에서 멈춤)

해결
    분석이 끝난 뒤, 크롬이 재생할 수 있는 WebM(VP8) 사본을 만듭니다.
    - 촬영 중에는 빠른 mp4v로 저장 → 프레임을 놓치지 않음
    - 분석은 원본으로 → 결과에 영향 없음
    - 재생용 사본은 가로 480px로 줄여서 → 1~3초 안에 만들어짐
    - 실제 수집 속도(프레임 수 ÷ 수집 시간)로 저장 → 8초 영상이 8초로 재생
"""

import logging
from pathlib import Path

import cv2

logger = logging.getLogger(__name__)

PLAYBACK_MAX_WIDTH = 480


def make_playback_copy(source: Path, fps: float | None = None, max_width: int = PLAYBACK_MAX_WIDTH) -> Path | None:
    """source 옆에 같은 이름의 .webm을 만들고 경로를 돌려줍니다. 실패하면 None."""
    source = Path(source)
    target = source.with_suffix(".webm")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        return None

    if not fps or fps <= 0:
        fps = capture.get(cv2.CAP_PROP_FPS) or 15.0

    writer = None
    frames = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if writer is None:
                height, width = frame.shape[:2]
                scale = min(1.0, max_width / width)
                # VP8은 가로·세로가 짝수여야 안전
                size = (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2)
                writer = cv2.VideoWriter(str(target), cv2.VideoWriter_fourcc(*"VP80"), float(fps), size)
                if not writer.isOpened():
                    logger.warning("WebM(VP8) 인코더를 열 수 없습니다. 원본을 그대로 둡니다.")
                    return None
            if frame.shape[1] != size[0]:
                frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
            writer.write(frame)
            frames += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()

    if frames == 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return None
    return target
