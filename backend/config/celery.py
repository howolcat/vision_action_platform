"""
config/celery.py
==================================================
Celery 앱을 만들고 Django 설정과 연결합니다.

Celery Worker는 웹 서버와 "다른 프로세스(컨테이너)"에서 실행되지만,
같은 Django 프로젝트 코드를 불러오기 때문에 Django ORM으로 DB에 결과를 저장할 수 있습니다.

실행: (backend 폴더에서)  celery -A config worker -l info --concurrency=1
"""

import os

from celery import Celery

# Celery가 Django 설정 파일을 찾을 수 있게 지정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")

# settings.py에서 CELERY_ 로 시작하는 설정을 읽어 옵니다. (CELERY_BROKER_URL → broker_url)
app.config_from_object("django.conf:settings", namespace="CELERY")

# INSTALLED_APPS의 각 앱에서 tasks.py를 자동으로 찾아 등록합니다. (analyses/tasks.py)
app.autodiscover_tasks()
