"""
analyses/camera_preview.py: IP Webcam 실시간 미리보기 (MJPEG 중계 + 자동 재연결)
==================================================
브라우저 <img src="/cameras/phone1/preview/">  →  Django  →  휴대폰 IP Webcam(/video)

왜 브라우저가 카메라에 직접 접속하지 않고 Django를 거치나요?
    - 카메라 주소(내부 IP)를 HTML·브라우저에 노출하지 않기 위해 (화면에는 이름만)
    - 로그인한 사용자만, 서버에 등록된 카메라만 볼 수 있게 하기 위해

자동 재연결이 필요한 이유
    휴대폰을 돌리거나 앱이 잠깐 멈추면 카메라 스트림이 끊깁니다.
    그런데 크롬은 <img>의 MJPEG 스트림이 끊겨도 에러 이벤트를 주지 않고 마지막 화면에서 멈춥니다.
    그래서 Django가 카메라 연결을 직접 관리합니다.
        1) 카메라가 보내는 바이트에서 JPEG 사진을 한 장씩 꺼냄 (FFD8 ~ FFD9 표시)
        2) 우리가 정한 boundary로 다시 포장해서 브라우저에 보냄
        3) 카메라 연결이 끊기면 "다시 연결 중" 화면을 보내면서 1초마다 재접속
       → 브라우저 입장에서는 하나의 영상이 끊기지 않고 계속 이어집니다.

미리보기와 분석용 수집(camera_capture.py)은 카메라에 각각 따로 접속합니다.
IP Webcam 앱은 여러 접속을 동시에 받을 수 있어서, 미리보기를 켠 채로 분석해도 됩니다.
"""

import logging
import time

import cv2
import numpy as np
import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, StreamingHttpResponse

logger = logging.getLogger(__name__)

BOUNDARY = "frame"
PREVIEW_MAX_SECONDS = 300      # 연결 하나를 최대 5분 유지 (브라우저가 그 전에 다시 연결)
NO_SIGNAL_GIVE_UP_SECONDS = 30 # 카메라가 30초 넘게 안 돌아오면 중계 종료 (서버 자원 정리)
PREVIEW_MAX_FPS = 15           # 미리보기는 초당 15장이면 충분 (네트워크 절약)
CHUNK_SIZE = 16 * 1024
MAX_BUFFER = 5 * 1024 * 1024   # 이상한 데이터가 와도 메모리가 계속 늘지 않게

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"


def _open_session() -> requests.Session:
    # trust_env=False: 프록시 환경변수를 무시하고 같은 Wi-Fi의 휴대폰에 직접 연결
    session = requests.Session()
    session.trust_env = False
    return session


def _connect(session, url):
    """(연결 제한 3초, 다음 데이터를 기다리는 제한 10초)"""
    response = session.get(url, stream=True, timeout=(3, 10))
    response.raise_for_status()
    return response


def _iter_jpegs(upstream):
    """카메라가 보내는 바이트 흐름에서 JPEG 사진을 한 장씩 꺼냅니다."""
    buffer = b""
    for chunk in upstream.iter_content(chunk_size=CHUNK_SIZE):
        buffer += chunk
        while True:
            start = buffer.find(JPEG_START)
            if start < 0:
                buffer = buffer[-1:]           # 0xFF 한 바이트가 다음 조각과 이어질 수 있어 남겨 둠
                break
            end = buffer.find(JPEG_END, start + 2)
            if end < 0:
                buffer = buffer[start:]        # 사진이 아직 덜 왔으면 다음 조각을 기다림
                if len(buffer) > MAX_BUFFER:
                    buffer = b""
                break
            yield buffer[start:end + 2]
            buffer = buffer[end + 2:]


def _part(jpeg: bytes) -> bytes:
    """JPEG 한 장을 multipart 조각으로 포장"""
    return (
        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n".encode()
        + jpeg
        + b"\r\n"
    )


_placeholder_cache = None


def _reconnecting_image() -> bytes:
    """카메라 연결이 끊겼을 때 보여 줄 화면 (한 번만 만들어 재사용)"""
    global _placeholder_cache
    if _placeholder_cache is None:
        image = np.full((360, 480, 3), (39, 24, 17), np.uint8)   # 어두운 남색 (BGR)
        cv2.putText(image, "Camera reconnecting...", (70, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
        cv2.putText(image, "Check the IP Webcam app", (95, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 170, 190), 1)
        _placeholder_cache = cv2.imencode(".jpg", image)[1].tobytes()
    return _placeholder_cache


@login_required
def camera_preview(request, name):
    source_url = settings.IP_WEBCAM_SOURCES.get(name)
    if not source_url:
        raise Http404("등록되지 않은 카메라입니다.")
    if not source_url.startswith(("http://", "https://")):
        return HttpResponse("미리보기는 http 주소 카메라만 지원합니다.", status=400, content_type="text/plain; charset=utf-8")

    session = _open_session()
    try:
        first_upstream = _connect(session, source_url)
    except requests.RequestException as error:
        # 처음부터 연결이 안 되면 502 → 브라우저(analyses.js)가 안내 문구를 띄우고 잠시 뒤 다시 시도
        session.close()
        logger.warning("미리보기 연결 실패: camera=%s (%s)", name, error.__class__.__name__)
        return HttpResponse("카메라에 연결할 수 없습니다.", status=502, content_type="text/plain; charset=utf-8")

    def relay():
        started = time.monotonic()
        upstream = first_upstream
        last_sent = 0.0
        lost_since = None
        min_interval = 1.0 / PREVIEW_MAX_FPS
        try:
            while time.monotonic() - started < PREVIEW_MAX_SECONDS:
                if upstream is not None:
                    try:
                        for jpeg in _iter_jpegs(upstream):
                            lost_since = None
                            now = time.monotonic()
                            if now - last_sent >= min_interval:   # 초당 15장으로 줄여서 전송
                                last_sent = now
                                yield _part(jpeg)
                            if now - started >= PREVIEW_MAX_SECONDS:
                                return
                    except requests.RequestException:
                        pass
                    finally:
                        upstream.close()
                        upstream = None

                # 여기까지 왔다면 카메라 스트림이 끊긴 것 → 안내 화면을 보내고 1초 뒤 재접속
                if lost_since is None:
                    lost_since = time.monotonic()
                    logger.info("미리보기 스트림 끊김, 재연결 시도: camera=%s", name)
                if time.monotonic() - lost_since > NO_SIGNAL_GIVE_UP_SECONDS:
                    return
                yield _part(_reconnecting_image())
                time.sleep(1)
                try:
                    upstream = _connect(session, source_url)
                except requests.RequestException:
                    upstream = None
        finally:
            # 브라우저가 탭을 닫거나 다른 탭으로 바꿔도 여기서 카메라 연결을 정리합니다.
            if upstream is not None:
                upstream.close()
            session.close()

    response = StreamingHttpResponse(relay(), content_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response
