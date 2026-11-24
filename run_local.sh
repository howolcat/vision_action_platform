#!/usr/bin/env bash
# ==================================================
# 로컬 개발 실행 스크립트 (Docker 없이)
#   가상환경을 activate하지 않고, 각 가상환경의 python을 경로로 직접 실행합니다.
#     - FastAPI : 수업용 가상환경 (ultralytics·torch가 설치된 곳)
#     - Django  : backend/.venv
#   Ctrl+C 한 번으로 둘 다 종료됩니다.
#
# 사용법:  ./run_local.sh
# 가상환경 위치가 다르면:  AI_PY=/경로/bin/python WEB_PY=/경로/bin/python ./run_local.sh
# ==================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# 기본 위치
#   1순위: 레포 최상위 .venv 하나에 모두 설치한 경우 → FastAPI·Django가 같이 사용
#   2순위: 수업 폴더의 .venv(FastAPI) + backend/.venv(Django)
if [ -x "$ROOT/.venv/bin/python" ]; then
  AI_PY="${AI_PY:-$ROOT/.venv/bin/python}"
  WEB_PY="${WEB_PY:-$ROOT/.venv/bin/python}"
else
  AI_PY="${AI_PY:-$ROOT/../.venv/bin/python}"
  WEB_PY="${WEB_PY:-$ROOT/backend/.venv/bin/python}"
fi
FASTAPI_PORT="${FASTAPI_PORT:-8001}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
LOG_DIR="$ROOT/.logs"

# ---------- 1. 가상환경이 있는지 확인 ----------
for pair in "FastAPI용:$AI_PY" "Django용:$WEB_PY"; do
  name="${pair%%:*}"; py="${pair#*:}"
  if [ ! -x "$py" ]; then
    echo "❌ $name 파이썬을 찾을 수 없습니다: $py"
    echo "   가상환경 위치를 확인한 뒤 이렇게 실행하세요:"
    echo "   AI_PY=/경로/.venv/bin/python WEB_PY=/경로/.venv/bin/python ./run_local.sh"
    exit 1
  fi
done

# ---------- 2. .env에서 로컬에도 필요한 값만 가져오기 ----------
#   (POSTGRES_* 는 가져오지 않음 → 로컬은 SQLite 사용)
if [ -f "$ROOT/.env" ]; then
  for key in IP_WEBCAM_SOURCES WEBCAM_CAPTURE_SECONDS UNCERTAIN_THRESHOLD MAX_UPLOAD_MB; do
    line="$(grep -E "^${key}=" "$ROOT/.env" | tail -n 1 || true)"
    [ -n "$line" ] && export "$line"
  done
fi

# Redis·Celery 없이: 분석 작업을 요청 안에서 바로 실행
export CELERY_TASK_ALWAYS_EAGER=1
export FASTAPI_URL="http://127.0.0.1:${FASTAPI_PORT}"
# 프록시 설정이 있어도 로컬 주소는 직접 연결
export NO_PROXY="localhost,127.0.0.1${NO_PROXY:+,$NO_PROXY}"
export no_proxy="$NO_PROXY"

mkdir -p "$LOG_DIR"

# ---------- 3. 종료 처리: Ctrl+C 하면 FastAPI도 같이 끄기 ----------
FASTAPI_PID=""
cleanup() {
  if [ -n "$FASTAPI_PID" ] && kill -0 "$FASTAPI_PID" 2>/dev/null; then
    echo ""
    echo "🛑 FastAPI 종료"
    kill "$FASTAPI_PID" 2>/dev/null || true
    wait "$FASTAPI_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# ---------- 4. FastAPI 실행 (백그라운드) ----------
echo "🚀 FastAPI 시작 (port ${FASTAPI_PORT}) · 로그: .logs/fastapi.log"
(cd "$ROOT/ai_service" && exec "$AI_PY" -m uvicorn main:app --host 127.0.0.1 --port "$FASTAPI_PORT") \
  > "$LOG_DIR/fastapi.log" 2>&1 &
FASTAPI_PID=$!

# 모델 로드가 끝날 때까지 /health 확인 (최대 90초)
echo -n "   모델 준비 중"
for _ in $(seq 1 90); do
  if ! kill -0 "$FASTAPI_PID" 2>/dev/null; then
    echo ""
    echo "❌ FastAPI가 시작하자마자 종료됐습니다. 마지막 로그:"
    tail -n 20 "$LOG_DIR/fastapi.log"
    exit 1
  fi
  if "$WEB_PY" -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:${FASTAPI_PORT}/health', timeout=2)" 2>/dev/null; then
    echo " ✅"
    break
  fi
  echo -n "."
  sleep 1
done

if ! "$WEB_PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${FASTAPI_PORT}/health', timeout=2)" 2>/dev/null; then
  echo ""
  echo "⚠️  FastAPI /health가 준비되지 않았습니다 (모델 파일 확인). 로그: .logs/fastapi.log"
  tail -n 10 "$LOG_DIR/fastapi.log"
  exit 1
fi

# ---------- 5. Django 실행 (이 터미널에 로그 표시) ----------
cd "$ROOT/backend"
"$WEB_PY" manage.py migrate --noinput > /dev/null
echo "🌐 Django 시작 → http://localhost:${DJANGO_PORT}"
echo "   (관리자 계정이 없다면 다른 터미널에서: $WEB_PY backend/manage.py createsuperuser)"
echo "   종료: Ctrl+C"
echo ""
"$WEB_PY" manage.py runserver "127.0.0.1:${DJANGO_PORT}"
