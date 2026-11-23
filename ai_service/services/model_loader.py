"""
model_loader.py
==================================================
6일차 학습 산출물(Random Forest + Feature 순서 + 학습 정보)을
서버 시작 시 "한 번만" 불러오고, 학습 조건과 맞는지 검사합니다.

왜 서버 시작 때 한 번만 불러오나요?
    joblib.load()는 300개의 Tree를 디스크에서 읽어 메모리에 올리는 작업이라
    요청마다 반복하면 느립니다. 한 번 올려 둔 모델을 모든 요청이 재사용합니다.

왜 검사를 이렇게 많이 하나요?
    Random Forest는 숫자 1,140개만 받으면 "순서가 틀려도" 에러 없이 예측합니다.
    (이 모델은 numpy 배열로 학습해서 Feature 이름을 기억하지 않습니다.)
    그래서 잘못된 입력은 에러가 아니라 "엉뚱한 결과"로 나타납니다.
    서버가 뜨기 전에 미리 막는 것이 가장 안전합니다.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import joblib

from src.common import PROJECT_ROOT, load_config
from src.sequence_utils import FRAME_FEATURE_NAMES, sequence_feature_names

# 모델 폴더 위치: 환경변수 MODEL_DIR이 있으면 그 경로, 없으면 ai_service/models
# (Docker에서는 볼륨 경로를 환경변수로 바꿔 끼울 수 있습니다.)
DEFAULT_MODEL_DIR = PROJECT_ROOT / "models"

MODEL_FILE = "rf_action_baseline.joblib"
FEATURE_COLUMNS_FILE = "rf_feature_columns.json"
TRAINING_INFO_FILE = "rf_training_info.json"
CHECKSUM_FILE = "SHA256SUMS.txt"


class ModelLoadError(RuntimeError):
    """모델 파일이 없거나 학습 조건과 맞지 않을 때 발생시키는 에러"""


@dataclass(frozen=True)
class ModelBundle:
    """추론에 필요한 모든 것을 한 묶음으로 들고 다니는 상자입니다.

    frozen=True: 서버가 도는 동안 실수로 값을 바꾸지 못하게 잠급니다.
    """

    model: object                 # 학습 완료 RandomForestClassifier
    feature_columns: list[str]    # 1,140개 Feature 이름과 순서
    classes: list[str]            # model.classes_ (확률 배열의 칸 순서)
    sequence_steps: int           # 30
    min_valid_frames: int         # 10 (이보다 적으면 예측하지 않음)
    pose_weights: Path            # YOLO Pose 가중치(.pt) 경로
    pose_config: dict             # project.json (image_size, person_confidence, device)


def get_model_dir() -> Path:
    return Path(os.getenv("MODEL_DIR", str(DEFAULT_MODEL_DIR)))


def _sha256(path: Path) -> str:
    """파일 내용을 SHA256 지문(64자리 16진수)으로 바꿉니다."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        # 큰 파일도 메모리를 아끼도록 1MB씩 나눠 읽습니다.
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksums(model_dir: Path) -> None:
    """SHA256SUMS.txt와 실제 파일 지문을 비교합니다.

    joblib.load()는 파일 안의 파이썬 객체를 그대로 되살리기 때문에
    "내가 만든 신뢰 가능한 파일"인지 확인한 뒤에만 불러와야 합니다.
    (터미널의 sha256sum -c SHA256SUMS.txt 와 같은 검사)
    """
    checksum_path = model_dir / CHECKSUM_FILE
    if not checksum_path.exists():
        raise ModelLoadError(f"{CHECKSUM_FILE} 파일이 없습니다: {checksum_path}")

    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        # 형식: "<64자리 해시>  <파일명>"
        expected, filename = line.split(maxsplit=1)
        target = model_dir / filename.strip().lstrip("*")
        if not target.exists():
            raise ModelLoadError(f"모델 파일이 없습니다: {target}")
        if _sha256(target) != expected:
            raise ModelLoadError(f"체크섬이 다릅니다(파일 손상·교체 의심): {target.name}")


def load_model_bundle(model_dir: Path | None = None) -> ModelBundle:
    """모델 묶음을 불러오고, 학습 조건과 맞는지 하나씩 검사합니다."""
    model_dir = model_dir or get_model_dir()

    # ① 파일 지문 확인 → 통과해야만 joblib.load 실행
    verify_checksums(model_dir)

    # ② 세 파일 읽기
    model = joblib.load(model_dir / MODEL_FILE)
    feature_columns = json.loads((model_dir / FEATURE_COLUMNS_FILE).read_text(encoding="utf-8"))
    training_info = json.loads((model_dir / TRAINING_INFO_FILE).read_text(encoding="utf-8"))

    steps = int(training_info["sequence_steps"])
    classes = [str(c) for c in model.classes_]

    # ③ Feature 수 검사: 모델 입력 = JSON 이름 수 = 학습 정보 = 30 × 38
    expected = steps * len(FRAME_FEATURE_NAMES)
    counts = {
        "model.n_features_in_": int(model.n_features_in_),
        "rf_feature_columns.json": len(feature_columns),
        "training_info.feature_count": int(training_info["feature_count"]),
        f"{steps} steps × {len(FRAME_FEATURE_NAMES)}": expected,
    }
    if len(set(counts.values())) != 1:
        raise ModelLoadError(f"Feature 수가 서로 다릅니다: {counts}")

    # ④ Feature 순서 검사 (가장 중요!)
    #    sequence_utils가 실제로 만드는 순서와 학습 때 저장한 순서가 같아야 합니다.
    if sequence_feature_names(steps) != feature_columns:
        raise ModelLoadError(
            "src/sequence_utils.py가 만드는 Feature 순서가 "
            "rf_feature_columns.json과 다릅니다."
        )

    # ⑤ Class 검사: 모델이 기억하는 Class와 학습 정보가 같은지
    if classes != list(training_info["classes"]):
        raise ModelLoadError(
            f"Class가 다릅니다: model={classes}, info={training_info['classes']}"
        )

    # ⑥ YOLO Pose 가중치 경로 (project.json의 pose_model 파일명을 models/ 안에서 찾음)
    pose_config = load_config()
    pose_weights = model_dir / pose_config["pose_model"]
    if not pose_weights.exists():
        raise ModelLoadError(f"YOLO Pose 가중치가 없습니다: {pose_weights}")

    return ModelBundle(
        model=model,
        feature_columns=feature_columns,
        classes=classes,
        sequence_steps=steps,
        min_valid_frames=int(training_info.get("min_valid_frames", 10)),
        pose_weights=pose_weights,
        pose_config=pose_config,
    )
