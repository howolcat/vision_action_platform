"""
pose_pipeline.py
==================================================
영상 파일 → (YOLO Pose + ByteTrack) → 사람(track_id)별 38차원 Feature 목록

학습 데이터를 만들 때(26번·34번 스크립트)와 "같은 조건"을 지키는 것이 핵심입니다.
    - 모든 Frame을 읽습니다. (건너뛰지 않음)
    - imgsz, conf는 configs/project.json 값을 그대로 씁니다. (640, 0.25)
    - 관절 Confidence로 관절을 걸러 내지 않습니다.
      (학습 CSV도 Confidence와 관계없이 좌표를 그대로 기록했습니다.)
    - Frame 번호(frame_id)는 0부터 셉니다. 30 Step 리샘플링의 시간축으로 쓰입니다.

학습 때와 다른 점 1가지:
    학습 때는 Frame마다 "Box Confidence가 가장 높은 1명"만 썼지만,
    서비스는 여러 사람을 구분해야 하므로 model.track()으로 사람마다 track_id를 붙입니다.
    Feature 계산 자체(정규화·각도)는 사람 한 명 단위라서 학습 때와 똑같습니다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

from src.common import resolve_device
from src.multi_person_utils import feature_from_keypoints

# 너무 긴 영상이 서버를 오래 붙잡지 않도록 처리할 최대 Frame 수
# (30fps 기준 1,800 Frame = 60초)
MAX_FRAMES = int(os.getenv("MAX_FRAMES", "1800"))
TRACKER = "bytetrack.yaml"  # 8일차 47번 스크립트의 기본 추적기


class VideoReadError(ValueError):
    """영상을 열 수 없거나 Frame을 하나도 읽지 못했을 때"""


@dataclass
class TrackFeatures:
    """한 사람(track_id)의 Frame별 Feature 기록"""

    frame_ids: list[int] = field(default_factory=list)
    features: list[np.ndarray] = field(default_factory=list)  # 각각 shape (38,)


@dataclass
class VideoPoseResult:
    frame_count: int                     # 실제로 읽은 Frame 수
    fps: float
    width: int
    height: int
    truncated: bool                      # MAX_FRAMES에서 잘렸는지
    tracks: dict[int, TrackFeatures]     # {track_id: TrackFeatures}


def extract_track_features(
    video_path: str | Path,
    pose_weights: Path,
    pose_config: dict,
) -> VideoPoseResult:
    """영상 한 건을 처음부터 끝까지 읽어 사람별 Feature를 모읍니다."""

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise VideoReadError("영상을 열 수 없습니다. 파일 형식이나 손상 여부를 확인하세요.")

    # ⚠️ YOLO 모델은 "요청마다 새로" 만듭니다.
    #    model.track(persist=True)는 추적기 상태를 모델 객체 안에 저장하기 때문에,
    #    하나의 모델을 여러 영상이 같이 쓰면 이전 영상의 track_id가 다음 영상으로 이어지거나
    #    동시에 들어온 요청끼리 추적 결과가 섞일 수 있습니다.
    #    yolo11n-pose.pt는 6MB라 새로 불러와도 부담이 작습니다.
    model = YOLO(str(pose_weights))
    device = resolve_device(pose_config.get("device", "auto"))

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

    tracks: dict[int, TrackFeatures] = {}
    frame_id = 0
    truncated = False

    try:
        while True:
            success, frame = capture.read()
            if not success:
                break  # 영상 끝

            if frame_id >= MAX_FRAMES:
                truncated = True
                break

            # 사람 검출 + 관절 추정 + 사람마다 track_id 부여
            result = model.track(
                source=frame,
                persist=True,           # 이 영상 안에서는 추적 상태를 이어 갑니다.
                tracker=TRACKER,
                imgsz=int(pose_config["image_size"]),
                conf=float(pose_config["person_confidence"]),
                device=device,
                verbose=False,
            )[0]

            boxes = result.boxes
            keypoints = result.keypoints

            # 사람이 없거나, 아직 track_id가 배정되지 않은 Frame은 건너뜁니다.
            if (
                boxes is not None
                and len(boxes) > 0
                and boxes.id is not None
                and keypoints is not None
                and keypoints.xy is not None
            ):
                track_ids = boxes.id.detach().cpu().numpy().astype(int)
                all_xy = keypoints.xy.detach().cpu().numpy()  # (사람 수, 17, 2) 픽셀 좌표

                for track_id, xy in zip(track_ids, all_xy):
                    # 3일차 정규화(골반 중심·어깨 폭) + 관절 각도 4개 → 38개
                    # 어깨 폭을 못 구하는 등 계산이 불가능하면 None
                    feature = feature_from_keypoints(xy)
                    if feature is None:
                        continue

                    track = tracks.setdefault(int(track_id), TrackFeatures())
                    track.frame_ids.append(frame_id)
                    track.features.append(feature)

            frame_id += 1
    finally:
        # 에러가 나도 영상 파일 핸들은 꼭 닫습니다.
        capture.release()

    if frame_id == 0:
        raise VideoReadError("영상에서 Frame을 하나도 읽지 못했습니다.")

    return VideoPoseResult(
        frame_count=frame_id,
        fps=fps,
        width=width,
        height=height,
        truncated=truncated,
        tracks=tracks,
    )
