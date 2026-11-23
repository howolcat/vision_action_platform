/* ==================================================
   dashboard.js: Django가 넘겨준 집계 데이터로 Chart.js 차트 그리기
   --------------------------------------------------
   데이터는 page_views.dashboard()에서 Django ORM으로 집계해
   템플릿의 json_script 태그(<script type="application/json">)로 전달됩니다.
   ================================================== */
(function () {
  if (typeof Chart === "undefined") {
    document.querySelectorAll(".chart-box").forEach((box) => {
      box.innerHTML = '<p class="muted center">차트 라이브러리를 불러오지 못했습니다. (인터넷 연결 확인)</p>';
    });
    return;
  }

  const read = (id) => JSON.parse(document.getElementById(id).textContent);
  const actionData = read("action-data");
  const statusData = read("status-data");
  const inputData = read("input-data");

  Chart.defaults.font.family = getComputedStyle(document.documentElement).fontFamily;
  Chart.defaults.color = "#667085";

  const integerAxis = { beginAtZero: true, ticks: { precision: 0 }, grid: { color: "#eef0f3" } };

  // ① 6개 Action별 예측 건수 (막대)
  new Chart(document.getElementById("action-chart"), {
    type: "bar",
    data: {
      labels: actionData.labels,
      datasets: [{ label: "예측 건수", data: actionData.values, backgroundColor: "#2f5bea", borderRadius: 6, maxBarThickness: 46 }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: integerAxis, x: { grid: { display: false } } },
    },
  });

  // ② 작업 상태별 건수 (막대, 상태마다 의미 있는 색)
  const statusColors = { PENDING: "#98a2b3", STARTED: "#2f5bea", SUCCESS: "#138a52", FAILURE: "#c4322b" };
  new Chart(document.getElementById("status-chart"), {
    type: "bar",
    data: {
      labels: statusData.labels,
      datasets: [{
        label: "작업 수",
        data: statusData.values,
        backgroundColor: statusData.codes.map((code) => statusColors[code]),
        borderRadius: 6,
        maxBarThickness: 56,
      }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: integerAxis, x: { grid: { display: false } } },
    },
  });

  // (선택) 입력 방식별 작업 수 + 평균 추론 시간은 툴팁으로
  if (inputData.labels.length) {
    new Chart(document.getElementById("input-chart"), {
      type: "bar",
      data: {
        labels: inputData.labels,
        datasets: [{ label: "작업 수", data: inputData.values, backgroundColor: ["#2f5bea", "#6c8cff"], borderRadius: 6, maxBarThickness: 40 }],
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { afterLabel: (ctx) => `평균 추론 ${inputData.avg_ms[ctx.dataIndex]} ms` } },
        },
        scales: { x: integerAxis, y: { grid: { display: false } } },
      },
    });
  } else {
    document.getElementById("input-chart").parentElement.innerHTML = '<p class="muted center">아직 데이터가 없습니다.</p>';
  }
})();
