"""
Django 설정 (config/settings.py)
==================================================
원칙: 환경마다 달라지는 값(비밀번호, 주소, 카메라 정보)은 코드에 쓰지 않고 환경변수로 받습니다.
    - 로컬 개발: 환경변수가 없으면 기본값(SQLite, localhost)으로 동작
    - Docker: compose.yaml이 .env 값을 넣어 주면 PostgreSQL, Redis, fastapi 서비스로 동작
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: str = "0") -> bool:
    """환경변수 "1"/"true"/"yes"를 True로 읽습니다."""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """쉼표로 구분한 환경변수를 리스트로 읽습니다."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# ==================================================
# 보안 기본값
# ==================================================
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env_bool("DJANGO_DEBUG", "1")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Docker에서 http://localhost:8000 으로 접속할 때 CSRF 검사를 통과시키기 위한 출처 목록
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")


# ==================================================
# 앱
# ==================================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",   # 설치해서 쓰는 패키지 (직접 만든 앱 개수에 포함하지 않음)
    "accounts",         # 직접 만든 앱 1: 회원
    "analyses",         # 직접 만든 앱 2: 분석 요청·결과·대시보드·Celery 작업
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ==================================================
# DB: POSTGRES_DB가 있으면 PostgreSQL, 없으면 SQLite
# ==================================================
if os.getenv("POSTGRES_DB"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["POSTGRES_DB"],
            "USER": os.environ["POSTGRES_USER"],
            "PASSWORD": os.environ["POSTGRES_PASSWORD"],
            "HOST": os.getenv("POSTGRES_HOST", "postgres"),   # Compose 서비스 이름
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ==================================================
# 언어·시간
# ==================================================
LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True


# ==================================================
# 정적 파일(CSS·JS)과 미디어 파일(업로드 영상)
# ==================================================
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"   # collectstatic 결과 위치

MEDIA_URL = "media/"
# Docker에서는 django와 celery가 같은 볼륨을 이 경로에 연결해 같은 영상 파일을 봅니다.
MEDIA_ROOT = Path(os.getenv("DJANGO_MEDIA_ROOT", str(BASE_DIR / "media")))

# 업로드 최대 크기 (FastAPI의 MAX_UPLOAD_MB와 맞춤)
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
ALLOWED_VIDEO_EXTENSIONS = [".mp4", ".mov", ".avi", ".mkv"]


# ==================================================
# 로그인
# ==================================================
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "analyses:index"
LOGOUT_REDIRECT_URL = "login"


# ==================================================
# DRF: 기본적으로 로그인한 사용자만 API 사용 가능
# ==================================================
REST_FRAMEWORK = {
    # 브라우저 화면과 같은 로그인(세션 쿠키)을 API에서도 사용합니다.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}


# ==================================================
# Celery (Redis = 메시지 브로커 + 작업 상태 저장)
# ==================================================
# Celery는 "CELERY_"로 시작하는 설정을 읽습니다. (config/celery.py의 namespace="CELERY")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_TRACK_STARTED = True          # STARTED 상태도 기록
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "600"))  # 10분이 넘으면 강제 종료
CELERY_WORKER_PREFETCH_MULTIPLIER = 1     # 무거운 작업을 한 번에 1개씩만 가져가기
CELERY_TASK_ACKS_LATE = False             # 웹캠 수집이 두 번 실행되지 않게 (재전달 안 함)
# Redis 없이 로컬에서 흐름만 확인할 때: CELERY_TASK_ALWAYS_EAGER=1 이면 작업을 즉시(동기) 실행
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", "0")


# ==================================================
# FastAPI AI 추론 서버
# ==================================================
# 로컬: http://127.0.0.1:8001 / Docker: http://fastapi:8000 (localhost 아님! 서비스 이름 사용)
FASTAPI_URL = os.getenv("FASTAPI_URL", "http://127.0.0.1:8001").rstrip("/")
FASTAPI_TIMEOUT = int(os.getenv("FASTAPI_TIMEOUT", "300"))


# ==================================================
# IP Webcam
# ==================================================
# 사용자가 주소를 마음대로 입력하지 못하게, 서버에 등록된 카메라만 "이름"으로 고르게 합니다.
# 형식: 이름=주소,이름=주소   예) IP_WEBCAM_SOURCES=phone1=http://192.168.0.10:8080/video
def _parse_camera_sources(raw: str) -> dict[str, str]:
    sources = {}
    for item in raw.split(","):
        if "=" not in item:
            continue
        name, url = item.split("=", 1)
        name, url = name.strip(), url.strip()
        if name and url.startswith(("http://", "https://", "rtsp://")):
            sources[name] = url
    return sources


IP_WEBCAM_SOURCES = _parse_camera_sources(os.getenv("IP_WEBCAM_SOURCES", ""))
WEBCAM_CAPTURE_SECONDS = int(os.getenv("WEBCAM_CAPTURE_SECONDS", "8"))   # 기본 수집 시간
WEBCAM_MIN_SECONDS = 5
WEBCAM_MAX_SECONDS = 10
WEBCAM_CONNECT_TIMEOUT_MS = int(os.getenv("WEBCAM_CONNECT_TIMEOUT_MS", "5000"))

# FastAPI의 UNCERTAIN_THRESHOLD와 같은 값 (대시보드 설명용)
UNCERTAIN_THRESHOLD = float(os.getenv("UNCERTAIN_THRESHOLD", "0.4"))


# ==================================================
# 로그 (Celery Worker 로그에서 작업 흐름을 확인하기 위함)
# ==================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"analyses": {"handlers": ["console"], "level": "INFO"}},
}


# Email (Django 6.1 기본 설정, 이번 과제에서는 사용하지 않음)
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}
