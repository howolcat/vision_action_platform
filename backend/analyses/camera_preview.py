"""
analyses/camera_preview.py: IP Webcam 실시간 미리보기 (MJPEG 중계)
==================================================
브라우저 <img src="/cameras/phone1/preview/">  →  Django  →  휴대폰 IP Webcam(/video)

왜 브라우저가 카메라에 직접 접속하지 않고 Django를 거치나요?
    - 카메라 주소(내부 IP)를 HTML·브라우저에 노출하지 않기 위해 (화면에는 이름만)
    - 로그인한 사용자만, 서버에 등록된 카메라만 볼 수 있게 하기 위해

Django는 카메라가 보내는 MJPEG 바이트를 "그대로 전달"만 합니다.
(이미지를 풀었다가 다시 만들지 않아서 CPU를 거의 쓰지 않음)

미리보기와 분석용 수집(camera_capture.py)은 카메라에 각각 따로 접속합니다.
IP Webcam 앱은 여러 접속을 동시에 받을 수 있어서, 미리보기를 켠 채로 분석해도 됩니다.
"""

import logging
import time

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, StreamingHttpResponse

logger = logging.getLogger(__name__)

# 한 번 연결한 미리보기를 최대 몇 초까지 유지할지 (화면을 열어 둔 채 잊어도 서버 자원이 묶이지 않게)
# 브라우저(analyses.js)가 이보다 조금 짧은 주기로 다시 연결합니다.
PREVIEW_MAX_SECONDS = 300
CHUNK_SIZE = 16 * 1024


@login_required
def camera_preview(request, name):
    source_url = settings.IP_WEBCAM_SOURCES.get(name)
    if not source_url:
        raise Http404("등록되지 않은 카메라입니다.")
    if not source_url.startswith(("http://", "https://")):
        return HttpResponse("미리보기는 http 주소 카메라만 지원합니다.", status=400, content_type="text/plain; charset=utf-8")

    # trust_env=False: 프록시 환경변수를 무시하고 같은 Wi-Fi의 휴대폰에 직접 연결
    session = requests.Session()
    session.trust_env = False
    try:
        # (연결 제한 3초, 다음 데이터를 기다리는 제한 10초)
        upstream = session.get(source_url, stream=True, timeout=(3, 10))
        upstream.raise_for_status()
    except requests.RequestException as error:
        session.close()
        logger.warning("미리보기 연결 실패: camera=%s (%s)", name, error.__class__.__name__)
        return HttpResponse("카메라에 연결할 수 없습니다.", status=502, content_type="text/plain; charset=utf-8")

    def relay():
        started = time.monotonic()
        try:
            for chunk in upstream.iter_content(chunk_size=CHUNK_SIZE):
                yield chunk
                if time.monotonic() - started > PREVIEW_MAX_SECONDS:
                    break
        except requests.RequestException:
            pass   # 카메라 쪽 연결이 끊기면 조용히 종료 (브라우저가 다시 연결)
        finally:
            # 브라우저가 탭을 닫거나 다른 탭으로 바꿔도 여기서 카메라 연결을 정리합니다.
            upstream.close()
            session.close()

    # 카메라가 알려 준 형식(multipart/x-mixed-replace; boundary=...)을 그대로 전달해야
    # 브라우저가 <img> 하나로 연속 화면을 그릴 수 있습니다.
    content_type = upstream.headers.get("Content-Type", "multipart/x-mixed-replace")
    response = StreamingHttpResponse(relay(), content_type=content_type)
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response
