"""
analyses 테스트 (실행: python manage.py test)
==================================================
FastAPI·Redis·카메라 없이도 돌아가도록 외부 호출은 mock(가짜 함수)으로 바꿔 끼웁니다.
과제 기능 테스트 표(T02, T04~T09)의 "코드로 확인 가능한 부분"을 자동화했습니다.
"""

import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .camera_capture import CameraCaptureError
from .models import AnalysisJob, PersonPrediction
from .services import PredictionServiceError
from .tasks import run_analysis

TEMP_MEDIA = tempfile.mkdtemp()

FAKE_RESULT = {
    "status": "ok",
    "message": "",
    "processing_ms": 4562,
    "video": {"frames": 126, "fps": 25.0, "width": 640, "height": 480, "truncated": False},
    "predictions": [
        {"track_id": 1, "action_label": "wave", "confidence": 0.6367, "is_uncertain": False,
         "valid_frames": 126, "probabilities": {"wave": 0.6367, "drink_return": 0.1067}},
        {"track_id": 2, "action_label": "sit_stand", "confidence": 0.31, "is_uncertain": True,
         "valid_frames": 80, "probabilities": {"sit_stand": 0.31}},
    ],
    "skipped_tracks": [],
}


def fake_video(name="wave.mp4", size=1024):
    return SimpleUploadedFile(name, b"\x00" * size, content_type="video/mp4")


@override_settings(
    MEDIA_ROOT=TEMP_MEDIA,
    CELERY_TASK_ALWAYS_EAGER=True,
    IP_WEBCAM_SOURCES={"phone1": "http://192.0.2.10:8080/video"},
)
class AnalysisApiTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.user_a = User.objects.create_user("user_a", password="pw-A-2026!")
        self.user_b = User.objects.create_user("user_b", password="pw-B-2026!")
        self.client.force_login(self.user_a)
        self.list_url = reverse("analyses:analysis-list")

    def make_job(self, user, **kwargs):
        defaults = {"title": "테스트", "input_type": "UPLOAD", "status": "SUCCESS"}
        defaults.update(kwargs)
        return AnalysisJob.objects.create(user=user, **defaults)

    # ---------- T05 비동기: 작업 ID 즉시 반환 + 커밋 후 등록 ----------
    def test_create_returns_202_and_enqueues_after_commit(self):
        with mock.patch("analyses.views.run_analysis.apply_async") as apply_async:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                response = self.client.post(
                    self.list_url,
                    {"input_type": "UPLOAD", "video": fake_video(), "expected_action": "wave"},
                    format="multipart",
                )
        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "PENDING")
        self.assertTrue(data["celery_task_id"])
        self.assertTrue(data["title"])                      # 비워 두면 자동 제목
        self.assertEqual(len(callbacks), 1)                 # on_commit 콜백으로 등록
        apply_async.assert_called_once_with(args=[data["id"]], task_id=data["celery_task_id"])

    def test_upload_requires_video_and_allowed_extension(self):
        response = self.client.post(self.list_url, {"input_type": "UPLOAD"}, format="multipart")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            self.list_url, {"input_type": "UPLOAD", "video": fake_video("note.txt")}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("video", response.json())

    # ---------- IP Webcam: 등록된 카메라 이름만 허용 ----------
    def test_webcam_only_allows_registered_camera(self):
        response = self.client.post(
            self.list_url, {"input_type": "WEBCAM", "camera_name": "http://evil.example/stream"}
        )
        self.assertEqual(response.status_code, 400)
        with mock.patch("analyses.views.run_analysis.apply_async"):
            response = self.client.post(self.list_url, {"input_type": "WEBCAM", "camera_name": "phone1"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["capture_seconds"], 8)

    def test_webcam_capture_seconds_range(self):
        response = self.client.post(
            self.list_url, {"input_type": "WEBCAM", "camera_name": "phone1", "capture_seconds": 60}
        )
        self.assertEqual(response.status_code, 400)

    # ---------- T07 CRUD 권한 ----------
    def test_user_cannot_read_update_delete_other_users_job(self):
        job_b = self.make_job(self.user_b)
        detail = reverse("analyses:analysis-detail", args=[job_b.id])
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.patch(detail, {"title": "해킹"}).status_code, 404)
        self.assertEqual(self.client.delete(detail).status_code, 404)
        self.assertEqual(self.client.get(reverse("analyses:analysis-job-status", args=[job_b.id])).status_code, 404)
        # 목록에도 B의 작업은 보이지 않음
        self.make_job(self.user_a)
        ids = [item["id"] for item in self.client.get(self.list_url).json()]
        self.assertNotIn(job_b.id, ids)

    def test_anonymous_cannot_use_api(self):
        self.client.logout()
        self.assertIn(self.client.get(self.list_url).status_code, (401, 403))

    # ---------- Update: 제목·메모만 ----------
    def test_patch_changes_only_title_and_memo(self):
        job = self.make_job(self.user_a, status="FAILURE")
        PersonPrediction.objects.create(job=job, track_id=1, action_label="wave", confidence=0.5, valid_frames=50)
        response = self.client.patch(
            reverse("analyses:analysis-detail", args=[job.id]),
            {"title": "새 제목", "memo": "메모", "status": "SUCCESS", "action_label": "sit_stand"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertEqual((job.title, job.memo, job.status), ("새 제목", "메모", "FAILURE"))
        self.assertEqual(job.predictions.get().action_label, "wave")

    def test_put_is_not_allowed(self):
        job = self.make_job(self.user_a)
        response = self.client.put(reverse("analyses:analysis-detail", args=[job.id]), {"title": "x"})
        self.assertEqual(response.status_code, 405)

    # ---------- Delete 정책 ----------
    def test_delete_policy(self):
        running = self.make_job(self.user_a, status="STARTED")
        self.assertEqual(self.client.delete(reverse("analyses:analysis-detail", args=[running.id])).status_code, 409)
        done = self.make_job(self.user_a)
        PersonPrediction.objects.create(job=done, track_id=1, action_label="wave", confidence=0.5, valid_frames=50)
        self.assertEqual(self.client.delete(reverse("analyses:analysis-detail", args=[done.id])).status_code, 204)
        self.assertFalse(PersonPrediction.objects.filter(job_id=done.id).exists())   # CASCADE

    # ---------- T04, T06: Worker가 사람별 결과를 ORM으로 저장 ----------
    def test_task_saves_predictions_per_track(self):
        job = self.make_job(self.user_a, status="PENDING", video=fake_video())
        with mock.patch("analyses.tasks.request_prediction", return_value=FAKE_RESULT):
            run_analysis.apply(args=[job.id])
        job.refresh_from_db()
        self.assertEqual(job.status, "SUCCESS")
        self.assertEqual(job.processing_ms, 4562)
        self.assertEqual(job.video_frames, 126)
        self.assertEqual(list(job.predictions.values_list("track_id", "action_label")),
                         [(1, "wave"), (2, "sit_stand")])
        # 상세 API에서 사람별 결과 확인
        data = self.client.get(reverse("analyses:analysis-detail", args=[job.id])).json()
        self.assertEqual(len(data["predictions"]), 2)

    # ---------- T09 예외 처리 ----------
    def test_task_saves_failure_when_fastapi_rejects(self):
        job = self.make_job(self.user_a, status="PENDING", video=fake_video())
        with mock.patch("analyses.tasks.request_prediction",
                        side_effect=PredictionServiceError("영상을 열 수 없습니다.")):
            run_analysis.apply(args=[job.id])
        job.refresh_from_db()
        self.assertEqual(job.status, "FAILURE")
        self.assertEqual(job.error_message, "영상을 열 수 없습니다.")
        status_data = self.client.get(reverse("analyses:analysis-job-status", args=[job.id])).json()
        self.assertTrue(status_data["is_finished"])

    def test_task_saves_failure_when_camera_disconnects(self):
        job = self.make_job(self.user_a, status="PENDING", input_type="WEBCAM", camera_name="phone1",
                            capture_seconds=8)
        with mock.patch("analyses.tasks.capture_clip",
                        side_effect=CameraCaptureError("카메라에 연결할 수 없습니다.")):
            run_analysis.apply(args=[job.id])
        job.refresh_from_db()
        self.assertEqual((job.status, job.error_message), ("FAILURE", "카메라에 연결할 수 없습니다."))

    def test_task_with_no_person_is_success_with_message(self):
        job = self.make_job(self.user_a, status="PENDING", video=fake_video())
        empty = dict(FAKE_RESULT, predictions=[], message="영상에서 사람을 찾지 못했습니다.")
        with mock.patch("analyses.tasks.request_prediction", return_value=empty):
            run_analysis.apply(args=[job.id])
        job.refresh_from_db()
        self.assertEqual(job.status, "SUCCESS")
        self.assertEqual(job.result_message, "영상에서 사람을 찾지 못했습니다.")
        self.assertEqual(job.predictions.count(), 0)


@override_settings(MEDIA_ROOT=TEMP_MEDIA)
class PageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("normal", password="pw-N-2026!")
        self.staff = User.objects.create_user("manager", password="pw-M-2026!", is_staff=True)

    def test_index_page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("analyses:index"))
        self.assertContains(response, "AI 분석 시작")

    def test_detail_page_only_for_owner(self):
        job = AnalysisJob.objects.create(user=self.staff, title="관리자 작업", input_type="UPLOAD")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("analyses:detail", args=[job.id])).status_code, 404)

    # ---------- T08 관리자 ----------
    def test_dashboard_only_for_staff(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("analyses:dashboard")).status_code, 302)
        self.client.force_login(self.staff)
        job = AnalysisJob.objects.create(user=self.user, title="a", input_type="UPLOAD", status="SUCCESS")
        PersonPrediction.objects.create(job=job, track_id=1, action_label="wave", confidence=0.6, valid_frames=30)
        response = self.client.get(reverse("analyses:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["action_chart"]["values"][-1], 1)     # wave 1건
        self.assertEqual(response.context["status_chart"]["values"][2], 1)      # SUCCESS 1건

    def test_admin_lists_jobs_for_staff(self):
        superuser = User.objects.create_superuser("root", password="pw-R-2026!")
        AnalysisJob.objects.create(user=self.user, title="관리 확인", input_type="UPLOAD")
        self.client.force_login(superuser)
        self.assertContains(self.client.get("/admin/analyses/analysisjob/"), "관리 확인")
        self.assertEqual(self.client.get("/admin/analyses/personprediction/").status_code, 200)
