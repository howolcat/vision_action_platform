# ==================================================
# FastAPI AI 추론 서비스 이미지
# ==================================================
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # ultralytics가 실행 중에 업데이트 확인·자동 설치를 시도하지 않게
    YOLO_OFFLINE=1

# OpenCV(ultralytics가 설치하는 opencv-python)가 사용하는 시스템 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ⭐ torch를 CPU 전용 버전으로 먼저 설치
#    그냥 ultralytics를 설치하면 GPU(CUDA)용 torch가 함께 내려와 이미지가 수 GB 더 커집니다.
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

# 코드 복사 (models/ 폴더는 .dockerignore로 제외 → compose에서 볼륨으로 연결)
COPY . .

EXPOSE 8000
# main.py가 있는 /app에서 실행해야 `from src...` import가 동작합니다.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
