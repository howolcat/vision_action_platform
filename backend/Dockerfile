# ==================================================
# Django + Celery Worker 공용 이미지
#   django 서비스: python manage.py runserver 0.0.0.0:8000
#   celery 서비스: celery -A config worker -l info
# (compose.yaml의 command가 어떤 프로그램을 실행할지 정합니다)
# ==================================================
FROM python:3.13-slim

# 로그가 바로 출력되게, .pyc 파일은 만들지 않게
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# OpenCV(headless)가 사용하는 시스템 라이브러리
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements를 먼저 복사 → 코드만 바뀌었을 때 패키지 설치 단계를 캐시로 재사용
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 업로드 영상 저장 위치 (compose에서 django·celery가 같은 볼륨을 연결)
RUN mkdir -p /app/media

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
