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
const latestDataSource = document.querySelector("[data-latest-data-source]");
const latestDataBody = document.querySelector("[data-latest-data-body]");
const streamState = document.querySelector("[data-stream-state]");
const clearLogButton = document.querySelector("[data-clear-log]");
const runButton = document.querySelector("button[form='scan-form']");
const clearDbBtn = document.getElementById("clear-db-btn");
const scanDuration = document.querySelector("[data-scan-duration]");
const marketViewer = document.querySelector("[data-market-viewer]");
const stockList = document.querySelector("[data-stock-list]");
const stockSearch = document.querySelector("[data-stock-search]");
const stockDetail = document.querySelector("[data-stock-detail]");
const marketStatus = document.querySelector("[data-market-status]");
const marketRefresh = document.querySelector("[data-market-refresh]");
const collapsiblePanels = document.querySelectorAll("[data-collapsible-panel]");
let eventSource = null;
let reconnectTimer = null;
let reconnectAttempts = 0;
let scanIsRunning = false;
const seenLogLines = new Set();
let marketStocks = [];
let selectedMarketCode = "";
let marketRequest = null;
let marketRefreshTimer = null;
let lastMarketRows = [];
let durationTimer = null;
let durationBaseSeconds = Number(scanDuration?.dataset.seconds || 0);
let durationTickStartedAt = 0;

function setPanelCollapsed(panel, collapsed, persist = true) {
  const button = panel.querySelector("[data-panel-toggle]");
  panel.classList.toggle("is-collapsed", collapsed);
  if (button) {
    button.setAttribute("aria-expanded", String(!collapsed));
    button.querySelector("span").textContent = collapsed ? "展开" : "收起";
    button.querySelector("i").textContent = collapsed ? "⌄" : "⌃";
  }
  if (persist) localStorage.setItem(`stock-helper-panel-${panel.dataset.collapsiblePanel}`, collapsed ? "collapsed" : "open");
  if (!collapsed && panel.matches("[data-market-viewer]")) {
    window.requestAnimationFrame(() => {
      const canvas = panel.querySelector("[data-kline-canvas]");
      if (canvas && lastMarketRows.length) drawKline(canvas, lastMarketRows);
    });
  }
}

collapsiblePanels.forEach((panel) => {
  const saved = localStorage.getItem(`stock-helper-panel-${panel.dataset.collapsiblePanel}`);
  const mobileDefault = window.matchMedia("(max-width: 768px)").matches && panel.dataset.mobileCollapsed === "true";
  setPanelCollapsed(panel, saved ? saved === "collapsed" : mobileDefault, false);
  panel.querySelector("[data-panel-toggle]")?.addEventListener("click", () => {
    setPanelCollapsed(panel, !panel.classList.contains("is-collapsed"));
  });
});

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
  if (!scanIsRunning && durationTimer) {
    window.clearInterval(durationTimer);
    durationTimer = null;
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
  renderLatestData(progress.latest_bar, progress.latest_source, progress.current_code, progress.current_name);
}

function renderSummary(summary) {
  if (!summary) return;
  if (candidateCount) candidateCount.textContent = summary.count ?? 0;
  if (topCandidate) {
    topCandidate.textContent = summary.top ? `${summary.top.code} ${summary.top.name}` : "暂无";
  }
  const scan = summary.scan;
  if (scan && scan.elapsed_seconds != null) {
    durationBaseSeconds = Number(scan.elapsed_seconds) || 0;
    if (scan.status === "running") startDurationTimer(durationBaseSeconds);
    else {
      stopDurationTimer();
      renderDuration(durationBaseSeconds);
    }
  } else if (!scanIsRunning) {
    stopDurationTimer();
    if (scanDuration) scanDuration.textContent = "暂无";
  }
}

function renderDuration(seconds) {
  if (!scanDuration) return;
  const value = Math.max(0, Number(seconds) || 0);
  if (value < 60) scanDuration.textContent = `${value.toFixed(1)} 秒`;
  else {
    const minutes = Math.floor(value / 60);
    const remain = Math.floor(value % 60);
    scanDuration.textContent = `${minutes} 分 ${String(remain).padStart(2, "0")} 秒`;
  }
}

function startDurationTimer(baseSeconds = 0) {
  stopDurationTimer();
  durationBaseSeconds = Number(baseSeconds) || 0;
  durationTickStartedAt = performance.now();
  renderDuration(durationBaseSeconds);
  durationTimer = window.setInterval(() => {
    renderDuration(durationBaseSeconds + (performance.now() - durationTickStartedAt) / 1000);
  }, 200);
}

function stopDurationTimer() {
  if (durationTimer) window.clearInterval(durationTimer);
  durationTimer = null;
}

function renderLatestData(bar, source, code, name) {
  if (!bar || !code || !marketViewer) return;
  window.clearTimeout(marketRefreshTimer);
  marketRefreshTimer = window.setTimeout(() => loadMarketStocks(false), 700);
}

async function loadMarketStocks(autoSelect = true) {
  if (!stockList) return;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/api/market/stocks", { headers: { Accept: "application/json" }, signal: controller.signal });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    marketStocks = (Array.isArray(payload) ? payload : (payload.stocks || [])).map((stock) => ({
      ...stock,
      code: String(stock.code || ""),
      name: String(stock.name || stock.code || "未命名股票"),
    })).sort((a, b) => stockCodeNumber(a.code).localeCompare(stockCodeNumber(b.code), "zh-CN", { numeric: true }) || a.code.localeCompare(b.code));
    renderMarketStocks();
    if (marketStatus) marketStatus.textContent = marketStocks.length ? `本地已缓存 ${marketStocks.length} 只` : "暂无缓存数据";
    if (autoSelect && !selectedMarketCode && marketStocks.length) selectMarketStock(marketStocks[0].code);
  } catch (error) {
    const message = error.name === "AbortError" ? "读取超时，请刷新页面重试" : error.message;
    if (marketStatus) marketStatus.textContent = "股票列表读取失败";
    stockList.innerHTML = `<div class="viewer-error">${escapeHtml(message)}</div>`;
  } finally {
    window.clearTimeout(timeout);
  }
}

function renderMarketStocks() {
  if (!stockList) return;
  const query = (stockSearch?.value || "").trim().toLowerCase();
  const compactQuery = query.replace(/^(sh|sz|bj)[.]?/, "");
  const visible = marketStocks.filter((stock) => {
    const code = String(stock.code || "").toLowerCase();
    const number = stockCodeNumber(code);
    const name = String(stock.name || "").toLowerCase();
    return !query || code.includes(query) || number.includes(compactQuery) || name.includes(query);
  });
  if (!visible.length) {
    stockList.innerHTML = `<div class="viewer-empty">${marketStocks.length ? "没有匹配的股票" : "暂无已拉取股票"}</div>`;
    return;
  }
  stockList.innerHTML = visible.map((stock) => `
    <button class="stock-option ${stock.code === selectedMarketCode ? "is-selected" : ""}" type="button"
            role="option" aria-selected="${stock.code === selectedMarketCode}" data-stock-code="${escapeHtml(stock.code)}">
      <span><b>${escapeHtml(stock.code.split(".").pop())}</b><small>${escapeHtml(stock.name)}</small></span>
      <span class="stock-cache-meta"><b>${Number(stock.rows_count || 0)}</b><small>${escapeHtml(stock.latest_trade_date || "--")}</small></span>
    </button>
  `).join("");
}

async function selectMarketStock(code, forceRefresh = false, revealOnMobile = false) {
  selectedMarketCode = String(code || "");
  renderMarketStocks();
  if (marketRefresh) marketRefresh.disabled = true;
  if (marketStatus) marketStatus.textContent = forceRefresh ? "正在增量刷新…" : "正在读取本地 K 线…";
  if (stockDetail) stockDetail.innerHTML = '<div class="viewer-loading tall"><i></i><span>读取本地日 K 数据</span></div>';
  if (marketRequest) marketRequest.abort();
  marketRequest = new AbortController();
  try {
    const url = forceRefresh ? "/api/market/daily-kline/refresh" : `/api/market/daily-kline?code=${encodeURIComponent(code)}&lookback_days=80`;
    const options = forceRefresh
      ? { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ code, lookback_days: 80 }), signal: marketRequest.signal }
      : { headers: { Accept: "application/json" }, signal: marketRequest.signal };
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (selectedMarketCode !== code) return;
    renderMarketDetail(payload);
    if (revealOnMobile && window.matchMedia("(max-width: 768px)").matches) {
      stockDetail?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    const rowCount = (payload.rows || []).length;
    if (marketStatus) marketStatus.textContent = payload.refreshed ? `增量数据已更新 · ${rowCount} 日` : (rowCount ? `当前仅有 ${rowCount} 个交易日数据` : "暂无本地K线数据");
    await loadMarketStocks(false);
  } catch (error) {
    if (error.name === "AbortError") return;
    if (marketStatus) marketStatus.textContent = "日 K 数据加载失败";
    if (stockDetail) stockDetail.innerHTML = `<div class="viewer-error tall"><b>无法加载数据</b><span>${escapeHtml(error.message)}</span></div>`;
  } finally {
    if (selectedMarketCode === code && marketRefresh) marketRefresh.disabled = false;
  }
}

function renderMarketDetail(payload) {
  if (!stockDetail) return;
  const rows = payload.rows || [];
  lastMarketRows = rows;
  if (!rows.length) {
    stockDetail.innerHTML = '<div class="viewer-empty tall"><b>暂无本地K线数据</b><span>如需联网补充，可点击“刷新当前股票”</span></div>';
    return;
  }
  const latest = rows[rows.length - 1];
  stockDetail.innerHTML = `
    <div class="stock-detail-head">
      <div><b>${escapeHtml(payload.code)}</b><span>${escapeHtml(payload.name || "")}</span></div>
      <div class="detail-badges"><span>${escapeHtml(latest.trade_date)}</span><span>${escapeHtml(latest.source || "--")}</span><span>${rows.length} 日</span></div>
    </div>
    ${rows.length < 80 ? `<div class="local-data-note">当前仅有 <b>${rows.length}</b> 个交易日数据，以下展示全部本地记录。</div>` : ""}
    <div class="kline-legend"><span class="ma5">MA5</span><span class="ma10">MA10</span><span class="ma20">MA20</span><small>红涨 · 绿跌</small></div>
    <div class="kline-stage"><canvas data-kline-canvas role="img" aria-label="${escapeHtml(payload.code)} 最近 ${rows.length} 个交易日日 K 图"></canvas></div>
    <div class="kline-table-wrap">
      <table class="kline-table">
        <thead><tr><th>日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th><th>成交额</th><th>涨跌幅</th><th>换手率</th></tr></thead>
        <tbody>${rows.slice().reverse().map((row) => `<tr>
          <td>${escapeHtml(row.trade_date)}</td><td>${formatNumber(row.open)}</td><td>${formatNumber(row.high)}</td>
          <td>${formatNumber(row.low)}</td><td>${formatNumber(row.close)}</td><td>${formatCompact(row.volume)}</td>
          <td>${formatCompact(row.amount)}</td><td class="${Number(row.pct_chg) >= 0 ? "up" : "down"}">${formatPointPercent(row.pct_chg)}</td>
          <td>${formatPointPercent(row.turnover)}</td></tr>`).join("")}</tbody>
      </table>
    </div>
  `;
  window.requestAnimationFrame(() => drawKline(stockDetail.querySelector("[data-kline-canvas]"), rows));
}

function drawKline(canvas, rows) {
  if (!canvas || !rows.length) return;
  const rect = canvas.parentElement.getBoundingClientRect();
  const mobile = window.matchMedia("(max-width: 768px)").matches;
  const width = Math.max(300, Math.floor(rect.width));
  const height = mobile ? 276 : 330;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  canvas.width = width * dpr; canvas.height = height * dpr;
  canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d"); ctx.scale(dpr, dpr);
  const pad = { left: mobile ? 40 : 48, right: 10, top: 12, bottom: 24 };
  const priceBottom = mobile ? 190 : 235;
  const volumeTop = mobile ? 204 : 252;
  const volumeBottom = height - 20;
  const maxPrice = Math.max(...rows.map((r) => Number(r.high)));
  const minPrice = Math.min(...rows.map((r) => Number(r.low)));
  const priceRange = maxPrice - minPrice || 1;
  const maxVolume = Math.max(...rows.map((r) => Number(r.volume) || 0), 1);
  const plotWidth = width - pad.left - pad.right; const step = plotWidth / rows.length;
  const yPrice = (value) => pad.top + ((maxPrice - value) / priceRange) * (priceBottom - pad.top);
  ctx.font = "11px ui-monospace, monospace"; ctx.strokeStyle = "rgba(154,164,178,.16)"; ctx.fillStyle = "#8290a2"; ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = pad.top + ((priceBottom - pad.top) * i / 4); const value = maxPrice - priceRange * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke(); ctx.fillText(value.toFixed(2), 4, y + 4);
  }
  rows.forEach((row, index) => {
    const x = pad.left + step * (index + .5); const open = Number(row.open); const close = Number(row.close);
    const color = close >= open ? "#f06f7d" : "#45c69c"; const bodyWidth = Math.max(2, Math.min(8, step * .62));
    ctx.strokeStyle = color; ctx.fillStyle = color;
    ctx.beginPath(); ctx.moveTo(x, yPrice(Number(row.high))); ctx.lineTo(x, yPrice(Number(row.low))); ctx.stroke();
    const top = yPrice(Math.max(open, close)); const bodyHeight = Math.max(1, Math.abs(yPrice(open) - yPrice(close)));
    ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, bodyHeight);
    const volumeHeight = (Number(row.volume) / maxVolume) * (volumeBottom - volumeTop);
    ctx.globalAlpha = .55; ctx.fillRect(x - bodyWidth / 2, volumeBottom - volumeHeight, bodyWidth, volumeHeight); ctx.globalAlpha = 1;
  });
  [[5, "#f0c36a"], [10, "#72b7ff"], [20, "#c18cff"]].forEach(([period, color]) => {
    ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.beginPath(); let started = false;
    rows.forEach((_, index) => {
      if (index + 1 < period) return;
      const avg = rows.slice(index + 1 - period, index + 1).reduce((sum, row) => sum + Number(row.close), 0) / period;
      const x = pad.left + step * (index + .5); const y = yPrice(avg); started ? ctx.lineTo(x, y) : ctx.moveTo(x, y); started = true;
    });
    ctx.stroke();
  });
  ctx.fillStyle = "#8290a2"; ctx.fillText(rows[0].trade_date.slice(5), pad.left, height - 5); ctx.fillText(rows[rows.length - 1].trade_date.slice(5), width - 48, height - 5);
}

function formatCompact(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿`;
  if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万`;
  return number.toFixed(0);
}

function formatPointPercent(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(2)}%` : "--";
}

function stockCodeNumber(code) {
  const match = String(code || "").match(/(\d{6})$/);
  return match ? match[1] : String(code || "");
}

stockSearch?.addEventListener("input", renderMarketStocks);
stockList?.addEventListener("click", (event) => {
  const option = event.target.closest("[data-stock-code]");
  if (option) selectMarketStock(option.dataset.stockCode, false, true);
});
marketRefresh?.addEventListener("click", () => {
  if (selectedMarketCode) selectMarketStock(selectedMarketCode, true);
});
window.addEventListener("resize", () => {
  const canvas = stockDetail?.querySelector("[data-kline-canvas]");
  if (canvas && lastMarketRows.length) drawKline(canvas, lastMarketRows);
});

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
      startDurationTimer(0);
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
loadMarketStocks(true);

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
        selectedMarketCode = "";
        lastMarketRows = [];
        if (stockDetail) stockDetail.innerHTML = '<div class="viewer-empty tall"><b>选择一只股票</b><span>查看最近 80 个交易日的日 K、成交量与明细数据</span></div>';
        await loadMarketStocks(false);
      }
    } catch(e) {
      clearMsg.textContent = "请求失败";
    } finally {
      clearDbBtn.disabled = scanIsRunning;
    }
  });
}
