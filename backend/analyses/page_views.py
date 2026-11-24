"""
analyses/page_views.py: HTML 화면
==================================================
/                   분석 화면 (입력 방식 선택, 분석 시작, 상태, 결과, 최근 이력)
/analyses/<id>/     분석 상세 (본인 것만)
/dashboard/         관리자 전용 대시보드 (Django ORM 집계 → Chart.js)

대시보드 데이터는 Django ORM으로 PostgreSQL을 집계해서 만듭니다.
FastAPI는 DB에 접근하지 않습니다.
"""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count
from django.shortcuts import get_object_or_404, render

from .comparison import input_type_comparison
from .models import ActionLabel, AnalysisJob, PersonPrediction


@login_required
def index(request):
    # 내 작업 전체 (사람별 예측까지 한 번에 불러와서 대표 예측 계산 시 DB를 반복 조회하지 않게)
    my_jobs = list(
        AnalysisJob.objects.filter(user=request.user)
        .prefetch_related("predictions")
        .order_by("-created_at")
    )
    context = {
        "recent_jobs": my_jobs[:10],
        # 과제 9-1 비교 화면: 입력 방식별 건수·평균 Confidence·처리 시간·실제 동작 일치
        "comparison": input_type_comparison(my_jobs),
        "action_choices": ActionLabel.choices,
        # 카메라 "이름"만 화면에 전달 (주소는 서버 밖으로 나가지 않음)
        "camera_names": list(settings.IP_WEBCAM_SOURCES.keys()),
        "capture_seconds": settings.WEBCAM_CAPTURE_SECONDS,
        "capture_min": settings.WEBCAM_MIN_SECONDS,
        "capture_max": settings.WEBCAM_MAX_SECONDS,
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "allowed_extensions": ",".join(settings.ALLOWED_VIDEO_EXTENSIONS),
        "uncertain_threshold": settings.UNCERTAIN_THRESHOLD,
        # 로컬 즉시 실행 모드면 버튼을 누르는 순간 촬영 시작, Celery 모드면 "처리 중"이 될 때 시작
        "is_eager": settings.CELERY_TASK_ALWAYS_EAGER,
    }
    return render(request, "analyses/index.html", context)


@login_required
def job_detail(request, pk):
    # 본인 작업만 (다른 사람 작업 번호면 404)
    job = get_object_or_404(AnalysisJob.objects.prefetch_related("predictions"), pk=pk, user=request.user)

    # 사람마다 6개 동작 확률을 큰 순서로 정리 → 화면에서 막대로 표시
    labels = dict(ActionLabel.choices)
    people = []
    for prediction in job.predictions.all():
        probs = sorted(prediction.probabilities.items(), key=lambda item: item[1], reverse=True)
        people.append(
            (prediction, [(label, labels.get(label, label), value, round(value * 100, 1)) for label, value in probs])
        )
    return render(request, "analyses/detail.html", {"job": job, "people": people})


@staff_member_required   # is_staff가 아니면 Admin 로그인 화면으로 이동
def dashboard(request):
    # ① 6개 Action별 예측 건수 (예측이 0건인 동작도 0으로 표시)
    action_counts = dict(
        PersonPrediction.objects.values_list("action_label").annotate(n=Count("id")).values_list("action_label", "n")
    )
    action_chart = {
        "labels": [label for _, label in ActionLabel.choices],
        "values": [action_counts.get(value, 0) for value, _ in ActionLabel.choices],
    }

    # ② 작업 상태별 건수
    status_counts = dict(
        AnalysisJob.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n")
    )
    status_chart = {
        "labels": [label for _, label in AnalysisJob.Status.choices],
        "codes": [value for value, _ in AnalysisJob.Status.choices],
        "values": [status_counts.get(value, 0) for value, _ in AnalysisJob.Status.choices],
    }

    # 선택: 입력 방식별 건수와 평균 추론 시간
    input_stats = (
        AnalysisJob.objects.values("input_type")
        .annotate(n=Count("id"), avg_ms=Avg("processing_ms"))
        .order_by("input_type")
    )
    input_labels = dict(AnalysisJob.InputType.choices)
    input_chart = {
        "labels": [input_labels.get(row["input_type"], row["input_type"]) for row in input_stats],
        "values": [row["n"] for row in input_stats],
        "avg_ms": [round(row["avg_ms"] or 0) for row in input_stats],
    }

    summary = {
        "jobs": AnalysisJob.objects.count(),
        "users": AnalysisJob.objects.values("user").distinct().count(),
        "predictions": PersonPrediction.objects.count(),
        "uncertain": PersonPrediction.objects.filter(is_uncertain=True).count(),
        "avg_ms": round(AnalysisJob.objects.filter(status="SUCCESS").aggregate(v=Avg("processing_ms"))["v"] or 0),
    }

    recent_failures = AnalysisJob.objects.filter(status=AnalysisJob.Status.FAILURE).select_related("user")[:5]

    return render(
        request,
        "analyses/dashboard.html",
        {
            "action_chart": action_chart,
            "status_chart": status_chart,
            "input_chart": input_chart,
            "summary": summary,
            "recent_failures": recent_failures,
            "uncertain_threshold": settings.UNCERTAIN_THRESHOLD,
        },
    )
