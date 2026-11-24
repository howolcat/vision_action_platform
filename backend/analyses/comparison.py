"""
analyses/comparison.py: 입력 방식 비교 (과제 문서 9-1)
==================================================
"비교 화면에서 확인할 항목: 입력 방식, 실제 수행한 동작, 예측 Label, Confidence, 처리 소요 시간"

영상 업로드와 IP Webcam 작업을 나눠서 건수·평균 Confidence·처리 시간·실제 동작 일치 수를 계산합니다.
화면(index)과 API(/api/analyses/comparison/)가 같은 함수를 씁니다.

⚠️ 두 입력은 촬영 거리·길이·사람·조명이 다를 수 있어서,
   여기 숫자로 "어느 입력이 더 정확하다"고 단정하면 안 됩니다. (화면에도 안내 문구 표시)
"""

from statistics import mean

from .models import AnalysisJob


def input_type_comparison(jobs) -> list[dict]:
    """jobs: AnalysisJob 목록 (predictions를 prefetch_related 해 두면 빠름)"""
    jobs = list(jobs)
    rows = []
    for value, label in AnalysisJob.InputType.choices:
        group = [job for job in jobs if job.input_type == value]
        done = [job for job in group if job.status == AnalysisJob.Status.SUCCESS]

        # 대표 예측 = 작업마다 Confidence가 가장 높은 사람의 예측
        tops = [job.top_prediction for job in done if job.top_prediction]

        # 사용자가 "실제로 한 동작"을 고른 작업만 일치 여부를 셀 수 있음
        with_expected = [job for job in done if job.expected_action and job.top_prediction]
        matched = [job for job in with_expected if job.top_prediction.action_label == job.expected_action]

        times = [job.processing_ms for job in done if job.processing_ms]

        rows.append({
            "input_type": value,
            "input_type_display": label,
            "total": len(group),
            "success": len(done),
            "failure": sum(job.status == AnalysisJob.Status.FAILURE for job in group),
            "avg_confidence": round(mean(p.confidence for p in tops), 3) if tops else None,
            "uncertain": sum(p.is_uncertain for p in tops),
            "predicted": len(tops),
            "avg_processing_ms": round(mean(times)) if times else None,
            "expected_count": len(with_expected),
            "matched": len(matched),
        })
    return rows
