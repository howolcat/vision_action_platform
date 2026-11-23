# Django가 시작될 때 Celery 앱도 함께 불러옵니다.
# 이렇게 해야 views.py에서 run_analysis.apply_async()를 호출할 때 같은 Celery 설정(Redis 주소)을 씁니다.
from .celery import app as celery_app

__all__ = ("celery_app",)
