"""
action_inference.py
==================================================
사람(track_id)별 Feature 목록 → 30 Step Sequence → Random Forest 예측

학습 때(34번 스크립트)와 같은 규칙:
    - 클립 "전체"를 하나의 샘플로 봅니다.
    - 유효 Frame이 min_valid_frames(10)개 미만이면 예측하지 않습니다.
    - frame_id를 시간축으로 삼아 30 Step으로 리샘플링합니다.
    - (30, 38)을 한 줄로 펴서 (1, 1,140)을 만듭니다.
"""

from __future__ import annotations

import os

import numpy as np

from services.model_loader import ModelBundle
from services.pose_pipeline import VideoPoseResult
from src.sequence_utils import flatten_sequence, resample_sequence

# 이 값보다 Confidence가 낮으면 화면에 "판단 불확실"로 표시합니다.
# 6개 Class를 무작위로 찍으면 약 0.17이므로, 그보다 충분히 높은 0.4를 기본값으로 둡니다.
# 시연하면서 조정할 수 있게 환경변수로 뺐습니다.
UNCERTAIN_THRESHOLD = float(os.getenv("UNCERTAIN_THRESHOLD", "0.4"))


def build_model_input(track_frame_ids, track_features, steps: int) -> np.ndarray:
    """한 사람의 Frame별 Feature → 모델 입력 (1, steps × 38)"""
    matrix = np.stack(track_features).astype(np.float32)        # (T, 38)
    frame_ids = np.asarray(track_frame_ids, dtype=np.float32)   # (T,)

    sequence = resample_sequence(matrix, frame_ids, target_steps=steps)  # (30, 38)

    # 행 우선(row-major)으로 펴면 t00의 38개 → t01의 38개 → … 순서가 됩니다.
    # 이것이 rf_feature_columns.json의 순서와 같다는 것은 model_loader에서 검사했습니다.
    return flatten_sequence(sequence).reshape(1, -1)            # (1, 1140)


def predict_tracks(bundle: ModelBundle, pose_result: VideoPoseResult) -> tuple[list[dict], list[dict]]:
    """사람별 예측 결과와, Frame이 부족해 건너뛴 사람 목록을 반환합니다."""
    predictions: list[dict] = []
    skipped: list[dict] = []

    for track_id in sorted(pose_result.tracks):
        track = pose_result.tracks[track_id]
        valid_frames = len(track.features)

        # 잠깐 스쳐 간 사람·추적이 끊겨 새로 생긴 ID 등은 정보가 부족해 예측하지 않습니다.
        if valid_frames < bundle.min_valid_frames:
            skipped.append({"track_id": track_id, "valid_frames": valid_frames})
            continue

        x = build_model_input(track.frame_ids, track.features, bundle.sequence_steps)

        if x.shape[1] != bundle.model.n_features_in_:
            raise RuntimeError(f"입력 Feature 수 불일치: {x.shape[1]} != {bundle.model.n_features_in_}")

        probabilities = bundle.model.predict_proba(x)[0]

        # ⚠️ 확률 배열의 칸 순서는 model.classes_(알파벳 순)입니다. A001~A006 순서가 아닙니다.
        best_index = int(np.argmax(probabilities))
        confidence = float(probabilities[best_index])

        predictions.append(
            {
                "track_id": track_id,
                "action_label": bundle.classes[best_index],
                "confidence": round(confidence, 4),
                "is_uncertain": confidence < UNCERTAIN_THRESHOLD,
                "valid_frames": valid_frames,
                "first_frame": int(track.frame_ids[0]),
                "last_frame": int(track.frame_ids[-1]),
                "probabilities": {
                    label: round(float(p), 4)
                    for label, p in zip(bundle.classes, probabilities)
                },
            }
        )

    return predictions, skipped
