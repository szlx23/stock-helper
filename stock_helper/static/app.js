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
const streamState = document.querySelector("[data-stream-state]");
const clearLogButton = document.querySelector("[data-clear-log]");
const runButton = document.querySelector("button[form='scan-form']");
const clearDbBtn = document.getElementById("clear-db-btn");
let eventSource = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
let scanIsRunning = false;
const seenLogLines = new Set();

if (priceInput && priceOutput) {
  const syncPrice = () => {
    const value = Number.parseFloat(priceInput.value);
    priceOutput.value = Number.isFinite(value) ? value.toFixed(2) : "--";
  };
  priceInput.addEventListener("input", syncPrice);
  syncPrice();
}

// 侧边栏切换 & 抽屉效果
const allSidebarToggles = document.querySelectorAll("[data-sidebar-toggle]");
// 创建遮罩层
let overlay = document.querySelector(".sidebar-overlay");
if (!overlay) {
  overlay = document.createElement("div");
  overlay.className = "sidebar-overlay";
  document.body.appendChild(overlay);
}
function openSidebar() {
  if (!sidebar) return;
  sidebar.classList.add("is-open");
  overlay.classList.add("is-open");
}
function closeSidebar() {
  if (!sidebar) return;
  sidebar.classList.remove("is-open");
  overlay.classList.remove("is-open");
}
allSidebarToggles.forEach((btn) => {
  if (!sidebar) {
    btn.hidden = true;
    return;
  }
  btn.addEventListener("click", () => {
    if (window.innerWidth <= 768) {
      sidebar.classList.contains("is-open") ? closeSidebar() : openSidebar();
    } else {
      sidebar.classList.toggle("is-collapsed");
      btn.textContent = sidebar.classList.contains("is-collapsed") ? "›" : "‹";
    }
  });
});
overlay.addEventListener("click", closeSidebar);

function parseLog(line) {
  const match = String(line || "").match(/^\[([^\]]+)]\s*(.*)$/);
  const message = match ? match[2] : String(line || "");
  const lower = message.toLowerCase();
  if (/失败|错误|异常|不可用|中断|超时/.test(message) || lower.includes("error")) return { time: match?.[1] || "--:--:--", message, tone: "error", label: "错误" };
  if (/命中|最高分|结果已保存|扫描完成|任务结束/.test(message)) return { time: match?.[1] || "--:--:--", message, tone: "done", label: "完成" };
  if (/取消|跳过|重试|后备源|非实时|实时门槛|淘汰统计/.test(message)) return { time: match?.[1] || "--:--:--", message, tone: "warning", label: "提示" };
  if (/阶段|进度|启动|连接数据源|股票列表/.test(message)) return { time: match?.[1] || "--:--:--", message, tone: "phase", label: "进度" };
  return { time: match?.[1] || "--:--:--", message, tone: "normal", label: "信息" };
}

function appendLog(line, forcedTone = "") {
  if (!logBox) return;
  const rawLine = String(line || "");
  if (seenLogLines.has(rawLine)) return;
  seenLogLines.add(rawLine);
  const shouldFollow = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 48;
  const placeholder = logBox.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();
  const parsed = parseLog(rawLine);
  if (forcedTone) {
    parsed.tone = forcedTone;
    parsed.label = { error: "错误", done: "完成", warning: "提示", phase: "进度" }[forcedTone] || parsed.label;
  }
  const row = document.createElement("div");
  row.className = `log-line ${parsed.tone}`;
  const time = document.createElement("time");
  time.className = "log-time";
  time.textContent = parsed.time;
  const kind = document.createElement("span");
  kind.className = "log-kind";
  kind.textContent = parsed.label;
  const message = document.createElement("span");
  message.className = "log-message";
  message.textContent = parsed.message;
  row.append(time, kind, message);
  logBox.append(row);
  while (logBox.children.length > 500) logBox.firstElementChild.remove();
  if (shouldFollow) logBox.scrollTop = logBox.scrollHeight;
}

function clearLog() {
  if (logBox) logBox.innerHTML = "";
  seenLogLines.clear();
}

function setStreamState(state, text) {
  if (!streamState) return;
  streamState.className = `stream-state ${state ? `is-${state}` : ""}`.trim();
  streamState.lastChild.textContent = text;
}

function outcomeLabel(outcome) {
  return { idle: "待运行", running: "运行中", success: "已完成", failed: "运行失败", cancelled: "已取消" }[outcome] || "状态未知";
}

function applyTaskState(outcome) {
  scanIsRunning = outcome === "running";
  if (taskStatus) {
    taskStatus.textContent = outcomeLabel(outcome);
    taskStatus.dataset.outcome = outcome || "idle";
  }
  if (runLabel) runLabel.textContent = scanIsRunning ? "停止扫描" : "启动扫描";
  if (runButton) {
    runButton.dataset.mode = scanIsRunning ? "stop" : "start";
    if (!scanIsRunning) runButton.disabled = false;
  }
  if (clearDbBtn) {
    clearDbBtn.disabled = scanIsRunning;
    clearDbBtn.title = scanIsRunning ? "扫描运行中不能清空数据库" : "清空本地扫描数据";
  }
}

function connectEvents() {
  if (eventSource) eventSource.close();
  if (reconnectTimer) window.clearTimeout(reconnectTimer);
  eventSource = new EventSource("/scan-events");
  applyTaskState("running");
  setStreamState("live", "实时连接");
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
      applyTaskState(payload.outcome || "success");
      reconnectAttempts = 0;
      setStreamState(payload.outcome === "failed" ? "error" : "", outcomeLabel(payload.outcome));
      await refreshStatus();
    }
  };
  eventSource.onerror = () => {
    if (!scanIsRunning) return;
    eventSource.close();
    eventSource = null;
    reconnectAttempts += 1;
    const delay = Math.min(5000, 500 * (2 ** Math.min(reconnectAttempts - 1, 3)));
    setStreamState("error", "正在重连");
    if (reconnectAttempts === 1) appendLog("实时连接短暂中断，正在自动恢复。", "warning");
    reconnectTimer = window.setTimeout(connectEvents, delay);
  };
}

async function refreshStatus() {
  try {
    const response = await fetch("/scan-status", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    applyTaskState(payload.outcome || (payload.done ? "idle" : "running"));
    renderSummary(payload.summary);
    renderResults(payload.candidates || []);
    renderProgress(payload.progress);
    if (payload.logs && payload.logs.length && logBox && !logBox.querySelector(".log-line")) {
      clearLog();
      payload.logs.forEach((line) => appendLog(line));
    }
    if (scanIsRunning && !eventSource) connectEvents();
    if (!scanIsRunning && !eventSource) setStreamState("", outcomeLabel(payload.outcome));
  } catch (error) {
    setStreamState("error", "状态获取失败");
    appendLog(`状态同步失败：${error.message}`, "error");
  }
}

function renderProgress(progress) {
  if (!progress) return;
  const completed = Number(progress.completed || 0);
  const total = Number(progress.total || 0);
  const hits = Number(progress.hits || 0);
  const phase = progress.phase === "pipeline" ? "流水线处理" : progress.phase === "data" ? "拉取数据" : progress.phase === "analysis" ? "并行分析" : "等待";
  const percent = total > 0 ? Math.min(100, Math.max(0, (completed / total) * 100)) : 0;
  if (progressTitle) {
    if (progress.phase === "pipeline") {
      progressTitle.textContent = `拉取 ${Number(progress.fetched || 0)}/${total} · 分析 ${Number(progress.analyzed || 0)} · 非实时 ${Number(progress.realtime_skipped || 0)}`;
    } else {
      progressTitle.textContent = `${phase} ${completed}/${total}`;
    }
  }
  if (progressFill) progressFill.style.width = `${percent}%`;
  if (hitCount) hitCount.textContent = `命中 ${hits}`;
  if (currentStock) {
    const code = progress.current_code || "";
    const name = progress.current_name || "";
    const action = progress.current_action === "fetch" ? "拉取完成" : progress.current_action === "analysis" ? "分析完成" : phase;
    currentStock.textContent = code ? `${action}：${code} ${name}` : "当前：等待启动";
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
  const keyed = new Map(liveHits.map((item) => [`${item.code}:${item.trade_date}`, item]));
  newHits.forEach((item) => keyed.set(`${item.code}:${item.trade_date}`, item));
  liveHits = Array.from(keyed.values());
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
  const doScan = async () => {
    if (runButton?.disabled) return;
    const runPwd = document.getElementById("run-pwd");
    if (scanIsRunning) {
      if (runButton) runButton.disabled = true;
      if (runLabel) runLabel.textContent = "正在停止…";
      try {
        const response = await fetch("/cancel-scan", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ password: runPwd?.value || "" }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
        appendLog("已提交停止请求，当前股票处理完成后将安全退出。", "warning");
      } catch (error) {
        appendLog(`停止失败：${error.message}`, "error");
        if (runButton) runButton.disabled = false;
        if (runLabel) runLabel.textContent = "停止扫描";
      }
      return;
    }
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    clearLog();
    liveHits = [];
    renderLiveHits();
    renderSummary({ count: 0, top: null });
    appendLog("提交参数，准备启动扫描...");
    if (runButton) runButton.disabled = true;
    if (runLabel) runLabel.textContent = "正在启动…";
    if (taskStatus) taskStatus.textContent = "启动中";

    try {
      const formData = new FormData(scanForm);
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
      if (runButton) runButton.disabled = false;
      connectEvents();
    } catch (error) {
      appendLog(`启动失败：${error.message}`, "error");
      if (runLabel) runLabel.textContent = "启动扫描";
      if (taskStatus) taskStatus.textContent = "启动失败";
      if (runButton) runButton.disabled = false;
    }
  };

  scanForm.addEventListener("submit", (event) => {
    event.preventDefault();
    doScan();
  });

  // 外部按钮
  const extBtn = document.querySelector("button[form='scan-form']");
  if (extBtn) {
    extBtn.addEventListener("click", (event) => {
      event.preventDefault();
      doScan();
    });
  }
}

if (logBox) {
  const initialLines = Array.from(logBox.querySelectorAll(".log-line"), (row) => row.textContent);
  if (initialLines.length) {
    clearLog();
    initialLines.forEach((line) => appendLog(line));
  }
}

refreshStatus();

if (clearLogButton) clearLogButton.addEventListener("click", clearLog);

const clearPwd = document.getElementById("clear-pwd");
const clearMsg = document.getElementById("clear-msg");
if (clearDbBtn) {
  clearDbBtn.addEventListener("click", async () => {
    if (!clearPwd || !clearMsg) return;
    const pwd = clearPwd.value;
    if (!pwd) { clearMsg.textContent = "请输入密码"; return; }
    clearMsg.textContent = "处理中...";
    clearDbBtn.disabled = true;
    try {
      const r = await fetch("/clear-db", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({password: pwd}) });
      const j = await r.json();
      clearMsg.textContent = j.ok ? "已清空 \u2713" : (j.error || "失败");
      if (j.ok) {
        clearPwd.value = "";
        await refreshStatus();
      }
    } catch(e) {
      clearMsg.textContent = "请求失败";
    } finally {
      clearDbBtn.disabled = scanIsRunning;
    }
  });
}
