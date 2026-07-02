const priceInput = document.querySelector("[data-live-price]");
const priceOutput = document.querySelector("[data-price-output]");
const scanForm = document.querySelector("[data-scan-form]");
const logBox = document.querySelector("[data-log-box]");
const runLabel = document.querySelector("[data-run-label]");
const sidebar = document.querySelector("[data-sidebar]");
const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
const resultsList = document.querySelector("[data-results-list]");
const candidateCount = document.querySelector("[data-candidate-count]");
const topCandidate = document.querySelector("[data-top-candidate]");
const taskStatus = document.querySelector("[data-task-status]");
const progressTitle = document.querySelector("[data-progress-title]");
const progressFill = document.querySelector("[data-progress-fill]");
const currentStock = document.querySelector("[data-current-stock]");
const hitCount = document.querySelector("[data-hit-count]");
let eventSource = null;

if (priceInput && priceOutput) {
  const syncPrice = () => {
    const value = Number.parseFloat(priceInput.value);
    priceOutput.value = Number.isFinite(value) ? value.toFixed(2) : "--";
  };
  priceInput.addEventListener("input", syncPrice);
  syncPrice();
}

if (sidebar && sidebarToggle) {
  sidebarToggle.addEventListener("click", () => {
    sidebar.classList.toggle("is-collapsed");
    sidebarToggle.textContent = sidebar.classList.contains("is-collapsed") ? "›" : "‹";
  });
}

function appendLog(line, tone = "normal") {
  if (!logBox) return;
  const placeholder = logBox.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();
  const row = document.createElement("div");
  row.className = `log-line ${tone}`;
  row.textContent = line;
  logBox.append(row);
  logBox.scrollTop = logBox.scrollHeight;
}

function clearLog() {
  if (logBox) logBox.innerHTML = "";
}

function connectEvents() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource("/scan-events");
  if (taskStatus) taskStatus.textContent = "运行中";
  eventSource.onmessage = async (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "hits") {
      appendHits(payload.candidates || []);
      return;
    }
    if (payload.type === "progress") {
      renderProgress(payload.progress);
      return;
    }
    appendLog(payload.line, payload.done ? "done" : "normal");
    if (payload.done) {
      eventSource.close();
      eventSource = null;
      if (runLabel) runLabel.textContent = "启动扫描";
      if (taskStatus) taskStatus.textContent = "已完成";
      await refreshStatus();
    }
  };
  eventSource.onerror = () => {
    appendLog("日志连接中断，可重新点击启动扫描。", "error");
    eventSource.close();
    eventSource = null;
    if (runLabel) runLabel.textContent = "启动扫描";
    if (taskStatus) taskStatus.textContent = "连接中断";
  };
}

async function refreshStatus() {
  const response = await fetch("/scan-status", { headers: { Accept: "application/json" } });
  if (!response.ok) return;
  const payload = await response.json();
  renderSummary(payload.summary);
  renderResults(payload.candidates || []);
  renderProgress(payload.progress);
  if (payload.logs && payload.logs.length && logBox && !logBox.querySelector(".log-line")) {
    clearLog();
    payload.logs.forEach((line) => appendLog(line));
  }
}

function renderProgress(progress) {
  if (!progress) return;
  const completed = Number(progress.completed || 0);
  const total = Number(progress.total || 0);
  const hits = Number(progress.hits || 0);
  const phase = progress.phase === "data" ? "拉取数据" : progress.phase === "analysis" ? "并行分析" : "等待";
  const percent = total > 0 ? Math.min(100, Math.max(0, (completed / total) * 100)) : 0;
  if (progressTitle) progressTitle.textContent = `${phase} ${completed}/${total}`;
  if (progressFill) progressFill.style.width = `${percent}%`;
  if (hitCount) hitCount.textContent = `命中 ${hits}`;
  if (currentStock) {
    const code = progress.current_code || "";
    const name = progress.current_name || "";
    currentStock.textContent = code ? `当前${phase}：${code} ${name}` : "当前：等待启动";
  }
}

function renderSummary(summary) {
  if (!summary) return;
  if (candidateCount) candidateCount.textContent = summary.count ?? 0;
  if (topCandidate) {
    topCandidate.textContent = summary.top ? `${summary.top.code} ${summary.top.name}` : "暂无";
  }
}

let liveHits = [];

function appendHits(newHits) {
  liveHits = liveHits.concat(newHits);
  liveHits.sort((a, b) => b.score - a.score);
  renderLiveHits();
}

function renderLiveHits() {
  if (!resultsList) return;
  if (!liveHits.length) {
    resultsList.innerHTML = '<div class="empty">扫描中，命中后会实时展示……</div>';
    return;
  }
  resultsList.innerHTML = liveHits.map(candidateCard).join("");
}

function renderResults(candidates) {
  if (!resultsList) return;
  if (!candidates.length) {
    resultsList.innerHTML = '<div class="empty">暂无候选股。运行扫描后结果会显示在这里。</div>';
    return;
  }
  resultsList.innerHTML = candidates.map(candidateCard).join("");
}

function candidateCard(item) {
  const reasons = (item.reasons || []).map((text) => `<span class="chip good">${escapeHtml(text)}</span>`).join("");
  const risks = (item.risks || []).map((text) => `<span class="chip warn">${escapeHtml(text)}</span>`).join("");
  return `
    <article class="candidate-card">
      <div class="candidate-title">
        <div>
          <span class="market-code">${escapeHtml(item.trade_date)}</span>
          <strong>${escapeHtml(item.code)}</strong>
          <span>${escapeHtml(item.name)}</span>
        </div>
        <b><em>${item.score}</em> 分</b>
      </div>
      <div class="metrics">
        <span>收盘 <b>${formatNumber(item.close)}</b></span>
        <span>涨跌 <b>${formatPercent(item.pct_chg)}</b></span>
        <span>MA10 <b>${formatNumber(item.ma10)}</b></span>
        <span>距MA10 <b>${formatPercent(item.distance_ma10_pct)}</b></span>
        <span>量比 <b>${formatNumber(item.volume_ratio_5)}</b></span>
      </div>
      <div class="chips">${reasons}${risks}</div>
    </article>
  `;
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "--";
}

function formatPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(2)}%` : "--";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

if (scanForm) {
  scanForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    clearLog();
    appendLog("提交参数，准备启动扫描...");
    if (runLabel) runLabel.textContent = "重新运行";
    if (taskStatus) taskStatus.textContent = "启动中";

    try {
      const formData = new FormData(scanForm);
      const runPwd = document.getElementById("run-pwd");
      if (runPwd) formData.set("run_password", runPwd.value);
      const response = await fetch(scanForm.action, {
        method: "POST",
        body: formData,
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${response.status}`);
      }
      const payload = await response.json();
      if (!payload.ok) throw new Error(payload.error);
      appendLog(`任务 #${payload.task_id} 已接管当前扫描通道。`);
      connectEvents();
    } catch (error) {
      appendLog(`启动失败：${error.message}`, "error");
      if (runLabel) runLabel.textContent = "启动扫描";
      if (taskStatus) taskStatus.textContent = "启动失败";
    }
  });
}

refreshStatus();

const clearDbBtn = document.getElementById("clear-db-btn");
const clearPwd = document.getElementById("clear-pwd");
const clearMsg = document.getElementById("clear-msg");
if (clearDbBtn) {
  clearDbBtn.addEventListener("click", async () => {
    if (!clearPwd || !clearMsg) return;
    const pwd = clearPwd.value;
    if (!pwd) { clearMsg.textContent = "请输入密码"; return; }
    clearMsg.textContent = "处理中...";
    try {
      const r = await fetch("/clear-db", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({password: pwd}) });
      const j = await r.json();
      clearMsg.textContent = j.ok ? "已清空 \u2713" : (j.error || "失败");
      if (j.ok) clearPwd.value = "";
    } catch(e) {
      clearMsg.textContent = "请求失败";
    }
  });
}
