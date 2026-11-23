/* ==================================================
   analyses.js: 분석 화면(index)과 상세 화면(detail)의 동작
   --------------------------------------------------
   [분석 화면 흐름]
   1. 'AI 분석 시작' → POST /api/analyses/ (FormData)
   2. 서버가 202와 작업 ID를 바로 돌려줌 (분석은 Celery Worker가 뒤에서 진행)
   3. 2초마다 GET /api/analyses/{id}/status/ 로 상태 확인 (폴링)
   4. 완료(SUCCESS/FAILURE)되면 GET /api/analyses/{id}/ 로 사람별 결과를 받아 표에 표시
   ================================================== */

// Django는 POST/PATCH/DELETE 요청에 CSRF 토큰을 요구합니다. (쿠키 csrftoken → 헤더 X-CSRFToken)
function getCookie(name) {
  const match = document.cookie.split("; ").find((row) => row.startsWith(name + "="));
  return match ? decodeURIComponent(match.split("=")[1]) : "";
}

async function api(url, options = {}) {
  const headers = { "X-CSRFToken": getCookie("csrftoken"), Accept: "application/json", ...(options.headers || {}) };
  const response = await fetch(url, { credentials: "same-origin", ...options, headers });
  let data = null;
  if (response.status !== 204) {
    try { data = await response.json(); } catch { data = null; }
  }
  return { ok: response.ok, status: response.status, data };
}

// DRF 오류 응답({필드: [메시지]})을 읽기 쉬운 문장으로
function formatErrors(data) {
  if (!data) return "요청을 처리하지 못했습니다.";
  if (typeof data.detail === "string") return data.detail;
  return Object.values(data).flat().join(" ");
}

const escapeHtml = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ==================================================
   분석 화면
   ================================================== */
function initIndexPage() {
  const form = document.getElementById("analysis-form");
  if (!form) return;

  const threshold = JSON.parse(document.getElementById("uncertain-threshold").textContent);
  const inputTypeField = form.querySelector('input[name="input_type"]');
  const fileInput = form.querySelector('input[name="video"]');
  const startButton = document.getElementById("start-button");
  const formErrors = document.getElementById("form-errors");
  const POLL_MS = 2000;
  const POLL_LIMIT_MS = 10 * 60 * 1000;
  let pollTimer = null;
  let elapsedTimer = null;

  // ---------- 탭 전환 (영상 업로드 / IP Webcam) ----------
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => {
        t.classList.toggle("active", t === tab);
        t.setAttribute("aria-selected", t === tab);
      });
      inputTypeField.value = tab.dataset.input;
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.dataset.panel !== tab.dataset.input;
      });
      formErrors.hidden = true;
    });
  });

  // ---------- 파일 선택·끌어다 놓기·미리보기 ----------
  const dropzone = document.getElementById("dropzone");
  const preview = document.getElementById("preview");
  const showFile = () => {
    const file = fileInput.files[0];
    document.getElementById("file-name").textContent = file ? `${file.name} (${(file.size / 1048576).toFixed(1)}MB)` : "";
    if (file) {
      preview.src = URL.createObjectURL(file);
      preview.hidden = false;
    } else {
      preview.hidden = true;
    }
  };
  fileInput.addEventListener("change", showFile);
  ["dragover", "dragenter"].forEach((type) =>
    dropzone.addEventListener(type, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((type) =>
    dropzone.addEventListener(type, () => dropzone.classList.remove("dragover")));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(); }
  });

  // 수집 시간 입력에 맞춰 안내 문구 숫자 변경
  const secondsInput = form.querySelector('input[name="capture_seconds"]');
  if (secondsInput) {
    secondsInput.addEventListener("input", () => {
      document.querySelectorAll("[data-seconds-text]").forEach((el) => (el.textContent = secondsInput.value));
    });
  }

  // ---------- 진행 상태 표시 ----------
  const stepper = document.getElementById("stepper");
  function renderStatus(status, inputType) {
    const order = { PENDING: 0, STARTED: 1, SUCCESS: 2, FAILURE: 2 };
    const current = order[status] ?? 0;
    stepper.querySelectorAll("li").forEach((li, index) => {
      li.className = "";
      if (index < current) li.classList.add("done");
      if (index === current) li.classList.add(status === "SUCCESS" ? "success" : status === "FAILURE" ? "failure" : "current");
      if (index === current && (status === "SUCCESS" || status === "FAILURE")) li.classList.add("done");
    });
    document.getElementById("done-label").textContent = status === "FAILURE" ? "실패" : "완료";

    const secondsText = secondsInput ? secondsInput.value : "";
    const hints = {
      PENDING: "작업이 대기열(Redis)에 등록됐어요. Celery Worker가 가져가기를 기다리는 중…",
      STARTED: inputType === "WEBCAM"
        ? `카메라에서 ${secondsText}초 동안 영상을 모은 뒤 AI가 분석합니다. 지금 카메라 앞에서 동작해 주세요!`
        : "AI 서버가 사람을 추적하고 동작을 분석하는 중…",
      SUCCESS: "분석이 끝났습니다.",
      FAILURE: "분석에 실패했습니다.",
    };
    document.getElementById("status-hint").textContent = hints[status] || "";
  }

  function renderPredictions(job) {
    const body = document.getElementById("result-body");
    body.innerHTML = "";
    job.predictions.forEach((p) => {
      const percent = Math.round(p.confidence * 100);
      const row = document.createElement("tr");
      row.innerHTML = `
        <td class="mono">${p.track_id}</td>
        <td><strong>${escapeHtml(p.action_display)}</strong> <span class="muted mono small">${escapeHtml(p.action_label)}</span>
            ${p.is_uncertain ? '<span class="badge badge-warn">판단 불확실</span>' : ""}</td>
        <td><div class="conf"><div class="conf-bar ${p.is_uncertain ? "uncertain" : ""}"><span style="width:${percent}%"></span></div>
            <span class="mono">${p.confidence.toFixed(2)}</span></div></td>
        <td>${p.valid_frames}</td>`;
      body.appendChild(row);
    });
    document.getElementById("result-wrap").hidden = job.predictions.length === 0;
    document.getElementById("detail-link").href = `/analyses/${job.id}/`;

    const message = document.getElementById("job-message");
    message.hidden = !job.result_message;
    message.textContent = job.result_message || "";
  }

  async function refreshRecent() {
    const { ok, data } = await api("/api/analyses/");
    if (!ok) return;
    const body = document.getElementById("recent-body");
    if (!data.length) return;
    body.innerHTML = data.slice(0, 10).map((job) => `
      <tr>
        <td class="mono">${job.id}</td>
        <td>${escapeHtml(job.title)}</td>
        <td>${escapeHtml(job.input_type_display)}</td>
        <td>${escapeHtml(job.expected_action_display || "-")}</td>
        <td><span class="badge status-${job.status}">${escapeHtml(job.status_display)}</span></td>
        <td>${job.predictions.length}</td>
        <td class="muted">${new Date(job.created_at).toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</td>
        <td><a href="/analyses/${job.id}/">상세</a></td>
      </tr>`).join("");
  }

  function stopTimers() {
    clearInterval(pollTimer);
    clearInterval(elapsedTimer);
    startButton.disabled = false;
    startButton.textContent = "AI 분석 시작";
  }

  // ---------- 상태 폴링 ----------
  function startPolling(job) {
    const startedAt = Date.now();
    const elapsedEl = document.getElementById("job-elapsed");
    elapsedTimer = setInterval(() => {
      elapsedEl.textContent = `${Math.floor((Date.now() - startedAt) / 1000)}초 경과`;
    }, 500);

    pollTimer = setInterval(async () => {
      if (Date.now() - startedAt > POLL_LIMIT_MS) {
        stopTimers();
        document.getElementById("status-hint").textContent = "응답이 오래 걸리고 있어요. 잠시 후 최근 이력에서 확인하세요.";
        return;
      }
      const { ok, data } = await api(`/api/analyses/${job.id}/status/`);
      if (!ok) return;   // 일시적인 네트워크 오류는 다음 주기에 다시 시도
      renderStatus(data.status, job.input_type);

      if (data.is_finished) {
        stopTimers();
        const errorBox = document.getElementById("job-error");
        errorBox.hidden = data.status !== "FAILURE";
        errorBox.textContent = data.error_message || "";
        const detail = await api(`/api/analyses/${job.id}/`);
        if (detail.ok) renderPredictions(detail.data);
        refreshRecent();
      }
    }, POLL_MS);
  }

  // ---------- 분석 시작 ----------
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    formErrors.hidden = true;

    const inputType = inputTypeField.value;
    const formData = new FormData(form);
    if (inputType === "UPLOAD") {
      if (!fileInput.files.length) {
        formErrors.textContent = "분석할 영상 파일을 선택하세요.";
        formErrors.hidden = false;
        return;
      }
      formData.delete("camera_name");
      formData.delete("capture_seconds");
    } else {
      formData.delete("video");
    }

    startButton.disabled = true;
    startButton.textContent = inputType === "UPLOAD" ? "업로드 중…" : "요청 중…";

    const { ok, data } = await api("/api/analyses/", { method: "POST", body: formData });
    if (!ok) {
      stopTimers();
      formErrors.textContent = formatErrors(data);
      formErrors.hidden = false;
      return;
    }

    // 202: 작업 ID를 받자마자 화면 전환 (분석이 끝날 때까지 기다리지 않음)
    startButton.textContent = "분석 진행 중…";
    document.getElementById("job-empty").hidden = true;
    document.getElementById("job-view").hidden = false;
    document.getElementById("job-id").textContent = `#${data.id}`;
    document.getElementById("job-title").textContent = data.title;
    document.getElementById("job-error").hidden = true;
    document.getElementById("job-message").hidden = true;
    document.getElementById("result-wrap").hidden = true;
    renderStatus(data.status, inputType);
    startPolling(data);
    refreshRecent();
  });
}

/* ==================================================
   상세 화면: 제목·메모 수정(PATCH), 삭제(DELETE)
   ================================================== */
function initDetailPage() {
  const form = document.getElementById("edit-form");
  if (!form) return;
  const jobId = form.dataset.jobId;
  const result = document.getElementById("edit-result");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    // form.title은 폼 자체의 title 속성과 겹치므로 form.elements로 입력칸을 꺼냅니다.
    const payload = { title: form.elements["title"].value, memo: form.elements["memo"].value };
    const { ok, data } = await api(`/api/analyses/${jobId}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    result.hidden = false;
    result.className = ok ? "alert alert-success" : "alert alert-error";
    result.textContent = ok ? "저장했습니다." : formatErrors(data);
  });

  document.getElementById("delete-button").addEventListener("click", async () => {
    if (!confirm("이 분석과 사람별 예측 결과, 영상 파일이 모두 삭제됩니다. 계속할까요?")) return;
    const { ok, data } = await api(`/api/analyses/${jobId}/`, { method: "DELETE" });
    if (ok) {
      window.location.href = "/";
    } else {
      result.hidden = false;
      result.className = "alert alert-error";
      result.textContent = formatErrors(data);
    }
  });
}

initIndexPage();
initDetailPage();
