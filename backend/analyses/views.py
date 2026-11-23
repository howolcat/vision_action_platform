"""
analyses/views.py: DRF API
==================================================
POST   /api/analyses/               분석 등록 → 202 + 작업 ID·task ID 즉시 반환
GET    /api/analyses/               내 분석 목록
GET    /api/analyses/{id}/          분석 상세 (사람별 결과 포함)
PATCH  /api/analyses/{id}/          제목·메모만 수정
DELETE /api/analyses/{id}/          삭제 (처리 중이면 거절, 영상 파일도 정리)
GET    /api/analyses/{id}/status/   상태 조회 (브라우저 폴링용)
"""

import logging
import uuid

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import AnalysisJob
from .serializers import (
    AnalysisJobSerializer,
    AnalysisJobStatusSerializer,
    AnalysisJobUpdateSerializer,
)
from .tasks import run_analysis

logger = logging.getLogger(__name__)


class AnalysisJobViewSet(viewsets.ModelViewSet):
    serializer_class = AnalysisJobSerializer
    # PUT(전체 수정)은 열지 않습니다. 수정은 PATCH로 제목·메모만.
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        # ⭐ 핵심 권한 규칙: 항상 "내 작업"만 조회 대상
        #    → 다른 사람의 작업 ID로 요청하면 존재하지 않는 것처럼 404가 됩니다.
        #    (관리자의 전체 조회·관리는 Django Admin과 /dashboard/에서 합니다.)
        return (
            AnalysisJob.objects.filter(user=self.request.user)
            .prefetch_related("predictions")
        )

    def get_serializer_class(self):
        if self.action == "partial_update":
            return AnalysisJobUpdateSerializer
        if self.action == "job_status":
            return AnalysisJobStatusSerializer
        return AnalysisJobSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # task ID를 미리 만들어 두면, Celery에 보내기 전에 DB에 기록하고 응답에도 바로 넣을 수 있습니다.
        task_id = str(uuid.uuid4())
        job = serializer.save(user=request.user, celery_task_id=task_id)

        # ⭐ DB 저장(커밋)이 "끝난 뒤"에 작업을 등록합니다.
        #    먼저 등록하면 Worker가 아직 저장되지 않은 job을 찾다가 실패할 수 있습니다.
        transaction.on_commit(
            lambda: run_analysis.apply_async(args=[job.id], task_id=task_id)
        )
        logger.info("분석 작업 등록: job=%s task=%s type=%s", job.id, task_id, job.input_type)

        # 202 Accepted: "요청은 받았고, 처리는 나중에 끝납니다"
        return Response(AnalysisJobSerializer(job, context=self.get_serializer_context()).data,
                        status=status.HTTP_202_ACCEPTED)

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs)
        # 수정 결과는 전체 형식으로 돌려줍니다.
        job = self.get_object()
        response.data = AnalysisJobSerializer(job, context=self.get_serializer_context()).data
        return response

    def destroy(self, request, *args, **kwargs):
        job = self.get_object()

        # 삭제 정책 1: 처리 중인 작업은 지우지 않음 (Worker가 결과를 저장하려다 실패하는 것을 방지)
        if job.status in (AnalysisJob.Status.PENDING, AnalysisJob.Status.STARTED):
            return Response(
                {"detail": "처리 중인 작업은 삭제할 수 없습니다. 완료 또는 실패 후 삭제하세요."},
                status=status.HTTP_409_CONFLICT,
            )

        # 삭제 정책 2: 영상 파일도 정리, 사람별 예측은 CASCADE로 함께 삭제
        if job.video:
            job.video.delete(save=False)
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # 함수 이름을 status로 지으면 위에서 import한 rest_framework.status와 헷갈리므로
    # 이름은 job_status, URL만 .../status/ 로 지정합니다.
    @action(detail=True, methods=["get"], url_path="status")
    def job_status(self, request, pk=None):
        job = self.get_object()
        return Response(AnalysisJobStatusSerializer(job).data)
