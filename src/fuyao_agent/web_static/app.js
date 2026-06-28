const state = {
  tools: [],
  workflows: [],
  cachedReportPayload: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload.data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderInlineMarkdown(value) {
  return String(value ?? "")
    .split(/(`[^`]*`)/g)
    .map((segment) => {
      if (segment.startsWith("`") && segment.endsWith("`")) {
        return `<code>${escapeHtml(segment.slice(1, -1))}</code>`;
      }
      return escapeHtml(segment)
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/__([^_]+)__/g, "<strong>$1</strong>");
    })
    .join("");
}

function toast(message) {
  const node = document.createElement("div");
  node.className = "toast";
  node.textContent = message;
  document.body.appendChild(node);
  window.setTimeout(() => node.remove(), 3600);
}

function setLoading(button, loading, label) {
  if (!button) return;
  if (!button.dataset.label) button.dataset.label = button.textContent;
  button.disabled = loading;
  button.textContent = loading ? label : button.dataset.label;
}

function setStatus(payload) {
  const dot = $("#statusDot");
  const title = $("#statusTitle");
  const detail = $("#statusDetail");
  dot.classList.remove("ready", "error");
  if (payload.ready) {
    dot.classList.add("ready");
    title.textContent = "配置就绪";
    detail.textContent = `${payload.modelscope_model} · ${payload.enabled_mcp_servers.join(", ")}`;
  } else {
    dot.classList.add("error");
    title.textContent = "配置缺失";
    detail.textContent = payload.error || "检查 .env";
  }
}

async function refreshStatus() {
  try {
    const payload = await api("/api/status");
    setStatus(payload);
  } catch (error) {
    setStatus({ ready: false, error: error.message });
  }
}

async function loadWorkflows() {
  const select = $("#workflowSelect");
  try {
    state.workflows = await api("/api/workflows");
    for (const workflow of state.workflows) {
      const option = document.createElement("option");
      option.value = workflow.name;
      option.textContent = `${workflow.title} · ${workflow.name}`;
      select.appendChild(option);
    }
  } catch (error) {
    toast(error.message);
  }
}

function currentAskPayload() {
  return {
    question: $("#questionInput").value.trim(),
    workflow: $("#workflowSelect").value,
    no_memory: $("#noMemory").checked,
    max_tool_rounds: Number($("#maxToolRounds").value || 8),
    pending_limit: Number($("#pendingLimit").value || 20),
  };
}

async function askAgent(event) {
  event.preventDefault();
  await submitAsk(currentAskPayload());
}

async function submitAsk(payload) {
  const button = $("#askButton");
  const runState = $("#runState");
  const answerPanel = $("#answerPanel");

  if (!payload.question && !payload.workflow) {
    toast("请输入问题或选择工作流");
    return;
  }

  const isForceRefresh = Boolean(payload.force_refresh);
  setLoading(button, true, isForceRefresh ? "重答中" : "运行中");
  runState.textContent = isForceRefresh ? "绕过缓存，重新运行中" : "模型和工具运行中";
  answerPanel.innerHTML = `<p class="empty-state">正在等待结果。</p>`;
  renderObservations([]);

  try {
    localStorage.setItem("fuyao:lastQuestion", payload.question);
    const result = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    answerPanel.innerHTML = `${renderCacheBanner(result.report_cache)}${renderMarkdown(result.answer)}`;
    bindCacheActions(result.report_cache, payload);
    renderObservations(result.observations || []);
    $("#metricTools").textContent = String(result.observation_count || 0);
    $("#metricMemory").textContent = result.report_cache?.hit
      ? `缓存 run ${result.report_cache.matched_run_id}`
      : result.memory_write
        ? `run ${result.memory_write.run_id}`
        : "未写入";
    runState.textContent = result.report_cache?.hit ? "缓存命中" : "完成";
    if (result.memory_warning) toast(result.memory_warning);
    if (result.memory_write?.validation_errors?.length) {
      const count = result.memory_write.validation_errors.length;
      toast(`记忆写入有 ${count} 条结构化记录被拒绝，请检查 MEMORY_JSON`);
    }
    if (result.memory_write) refreshMemory(false);
  } catch (error) {
    answerPanel.innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
    runState.textContent = "失败";
  } finally {
    setLoading(button, false);
  }
}

function renderCacheBanner(cache) {
  if (!cache?.hit) return "";
  const similarity = `${Math.round(Number(cache.similarity || 0) * 100)}%`;
  const ttlText = formatDuration(cache.ttl_seconds);
  const ageText = formatDuration(cache.age_seconds);
  return `
    <div class="cache-banner">
      <div>
        <strong>读取缓存报告</strong>
        <span>匹配 run ${escapeHtml(cache.matched_run_id)} · 相似度 ${escapeHtml(similarity)} · 已缓存 ${escapeHtml(ageText)} / ${escapeHtml(ttlText)}</span>
      </div>
      <button class="ghost-button" type="button" id="forceRefreshFromCache">重新作答</button>
    </div>
  `;
}

function bindCacheActions(cache, payload) {
  const button = $("#forceRefreshFromCache");
  if (!button || !cache?.hit) {
    state.cachedReportPayload = null;
    return;
  }
  state.cachedReportPayload = { ...payload, force_refresh: true };
  button.addEventListener("click", () => {
    submitAsk({ ...state.cachedReportPayload });
  });
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "暂无";
  if (value < 60) return `${Math.round(value)} 秒`;
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes} 分钟`;
  return `${(minutes / 60).toFixed(1)} 小时`;
}

function renderMarkdown(text) {
  const lines = String(text || "").split(/\r?\n/);
  const html = [];
  let inCode = false;
  let code = [];
  let paragraph = [];
  let listType = null;
  let listItems = [];
  let tableRows = [];

  function flushParagraph() {
    if (paragraph.length) {
      html.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
      paragraph = [];
    }
  }

  function flushList() {
    if (!listType) return;
    const tag = listType === "ordered" ? "ol" : "ul";
    html.push(`<${tag}>${listItems.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</${tag}>`);
    listType = null;
    listItems = [];
  }

  function flushTable() {
    if (!tableRows.length) return;
    if (tableRows.length < 2 || !isTableDivider(tableRows[1])) {
      paragraph.push(...tableRows.map((row) => row.join(" | ")));
      tableRows = [];
      return;
    }

    const headers = tableRows[0];
    const bodyRows = tableRows.slice(2);
    const headerHtml = headers
      .map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`)
      .join("");
    const bodyHtml = bodyRows
      .map((row) => {
        const cells = headers.map((_, index) => row[index] || "");
        return `<tr>${cells.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`;
      })
      .join("");
    html.push(`<div class="markdown-table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`);
    tableRows = [];
  }

  function flushBlocks() {
    flushTable();
    flushParagraph();
    flushList();
  }

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(`<pre>${escapeHtml(code.join("\n"))}</pre>`);
        code = [];
        inCode = false;
      } else {
        flushBlocks();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }
    if (!line.trim()) {
      flushBlocks();
      continue;
    }

    if (looksLikeTableRow(line)) {
      flushParagraph();
      flushList();
      tableRows.push(parseTableRow(line));
      continue;
    }
    flushTable();

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      const level = Math.min(4, Math.max(2, heading[1].length + 1));
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
    if (unordered) {
      flushParagraph();
      if (listType !== "unordered") flushList();
      listType = "unordered";
      listItems.push(unordered[1]);
      continue;
    }

    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      if (listType !== "ordered") flushList();
      listType = "ordered";
      listItems.push(ordered[1]);
      continue;
    }

    const quote = line.match(/^>\s?(.+)$/);
    if (quote) {
      flushParagraph();
      flushList();
      html.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      continue;
    }

    if (/^\s*-{3,}\s*$/.test(line)) {
      flushBlocks();
      html.push("<hr>");
      continue;
    }

    flushList();
    paragraph.push(line);
  }
  flushBlocks();
  if (inCode) html.push(`<pre>${escapeHtml(code.join("\n"))}</pre>`);
  return html.join("") || `<p class="empty-state">无输出</p>`;
}

function looksLikeTableRow(line) {
  return /^\s*\|.+\|\s*$/.test(line) || (line.includes("|") && line.split("|").length >= 2);
}

function parseTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableDivider(row) {
  return row.length > 1 && row.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function renderObservations(items) {
  const list = $("#observationList");
  $("#observationCount").textContent = String(items.length);
  if (!items.length) {
    list.innerHTML = `<p class="empty-state">暂无工具调用</p>`;
    return;
  }
  list.innerHTML = items.map((item, index) => {
    const args = JSON.stringify(item.arguments || {}, null, 2);
    return `
      <details class="observation-item">
        <summary>
          <span class="sequence">${String(index + 1).padStart(2, "0")}</span>
          <strong>${escapeHtml(item.tool_name || "未知工具")}</strong>
        </summary>
        <pre>${escapeHtml(args)}</pre>
        <pre>${escapeHtml(item.result || "")}</pre>
      </details>
    `;
  }).join("");
}

async function refreshTools() {
  const button = $("#refreshTools");
  const list = $("#toolList");
  setLoading(button, true, "刷新中");
  list.innerHTML = `<p class="empty-state">正在加载工具。</p>`;
  try {
    state.tools = await api("/api/tools");
    renderTools();
  } catch (error) {
    list.innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
  } finally {
    setLoading(button, false);
  }
}

function renderTools() {
  const list = $("#toolList");
  const needle = $("#toolFilter").value.trim().toLowerCase();
  const tools = state.tools.filter((tool) => {
    const text = `${tool.server} ${tool.name} ${tool.description}`.toLowerCase();
    return !needle || text.includes(needle);
  });
  if (!tools.length) {
    list.innerHTML = `<p class="empty-state">没有匹配的工具。</p>`;
    return;
  }
  list.innerHTML = tools.map((tool) => `
    <article class="tool-item">
      <div class="tool-server">${escapeHtml(tool.server)}</div>
      <div>
        <strong>${escapeHtml(tool.name)}</strong>
        <p>${escapeHtml(tool.description || "无描述")}</p>
      </div>
    </article>
  `).join("");
}

async function refreshMemory(showToast = true) {
  const button = $("#refreshMemory");
  if (showToast) setLoading(button, true, "刷新中");
  try {
    const [stats, pending, errors, audits, marketPerformance, stockPerformance] = await Promise.all([
      api("/api/memory/stats"),
      api("/api/memory/pending?limit=50"),
      api("/api/memory/errors?limit=20"),
      api("/api/memory/audits?limit=20"),
      api("/api/memory/performance?workflow=market-weather"),
      api("/api/memory/performance?workflow=stock-analysis"),
    ]);
    renderMemoryStats(stats);
    renderPerformance([
      { workflow: "market-weather", ...marketPerformance },
      { workflow: "stock-analysis", ...stockPerformance },
    ]);
    renderAudits(audits);
    renderPending(pending);
    renderValidationErrors(errors);
  } catch (error) {
    $("#memoryStats").innerHTML = `<div><strong>错误</strong><span>${escapeHtml(error.message)}</span></div>`;
    $("#performanceTable").innerHTML = `<tr><td colspan="8">${escapeHtml(error.message)}</td></tr>`;
    $("#auditTable").innerHTML = `<tr><td colspan="8">${escapeHtml(error.message)}</td></tr>`;
    $("#errorTable").innerHTML = `<tr><td colspan="4">${escapeHtml(error.message)}</td></tr>`;
  } finally {
    if (showToast) setLoading(button, false);
  }
}

function renderMemoryStats(stats) {
  const items = [
    ["预测总数", stats.prediction_total],
    ["有效预测", stats.valid_prediction_total],
    ["待复盘", stats.pending_total],
    ["复盘总数", stats.reviewed_total],
    ["平均得分", stats.average_score == null ? "暂无" : Number(stats.average_score).toFixed(2)],
    ["证据链", stats.predictions_with_evidence_total || 0],
    ["结构拒绝", stats.validation_error_total || 0],
    ["运行审计", stats.run_audit_total || 0],
  ];
  $("#memoryStats").innerHTML = items.map(([label, value]) => `
    <div><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>
  `).join("");
}

function renderPerformance(rows) {
  const body = $("#performanceTable");
  $("#performanceCount").textContent = String(rows.length);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8">暂无评分表现</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.workflow)}</td>
      <td>${escapeHtml(row.scored_total ?? 0)}</td>
      <td>${escapeHtml(row.hit_count ?? 0)}</td>
      <td>${escapeHtml(row.miss_count ?? 0)}</td>
      <td>${escapeHtml(row.unknown_count ?? 0)}</td>
      <td>${escapeHtml(formatRate(row.hit_rate))}</td>
      <td>${escapeHtml(formatScore(row.average_score))}</td>
      <td>${escapeHtml(formatTopMetric(row.by_metric))}</td>
    </tr>
  `).join("");
}

function renderAudits(rows) {
  const body = $("#auditTable");
  $("#auditCount").textContent = String(rows.length);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="8">暂无运行审计</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.run_id)}</td>
      <td>${escapeHtml(row.workflow || "暂无")}</td>
      <td>${escapeHtml(row.output_audit_status || "暂无")}</td>
      <td>${escapeHtml(row.memory_json_status || "暂无")}</td>
      <td>${escapeHtml(`${row.accepted_prediction_count || 0}/${row.prediction_count || 0}`)}</td>
      <td>${escapeHtml(formatList(row.missing_tools))}</td>
      <td>${escapeHtml(formatList(row.signal_families))}</td>
      <td>${escapeHtml(row.validation_error_count || 0)}</td>
    </tr>
  `).join("");
}

function renderPending(rows) {
  const body = $("#pendingTable");
  $("#pendingCount").textContent = String(rows.length);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7">暂无待复盘预测</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.id)}</td>
      <td>${escapeHtml(row.target || row.target_id || "未知")}</td>
      <td>${escapeHtml(row.metric || "暂无")}</td>
      <td>${escapeHtml(row.expected_direction || "暂无")}</td>
      <td>${escapeHtml(row.confidence ?? "暂无")}</td>
      <td>${escapeHtml(formatEvidence(row.evidence))}</td>
      <td>${escapeHtml(formatCondition(row.condition))}</td>
    </tr>
  `).join("");
}

function renderValidationErrors(rows) {
  const body = $("#errorTable");
  $("#errorCount").textContent = String(rows.length);
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="4">暂无结构拒绝记录</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.created_at || "暂无")}</td>
      <td>${escapeHtml(row.workflow || "暂无")}</td>
      <td>${escapeHtml(row.item_type || "暂无")}</td>
      <td>${escapeHtml((row.errors || []).join("; "))}</td>
    </tr>
  `).join("");
}

function formatEvidence(evidence) {
  if (!Array.isArray(evidence) || !evidence.length) return "暂无";
  return [...new Set(evidence.map((item) => item.tool_name).filter(Boolean))].join(", ");
}

function formatCondition(condition) {
  if (!condition || typeof condition !== "object") return "暂无";
  if (condition.operator === "between") {
    return `${condition.metric} between ${condition.lower} and ${condition.upper}`;
  }
  return `${condition.metric} ${condition.operator} ${condition.threshold}`;
}

function formatRate(value) {
  if (value == null) return "暂无";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function formatScore(value) {
  if (value == null) return "暂无";
  return Number(value).toFixed(2);
}

function formatTopMetric(items) {
  if (!Array.isArray(items) || !items.length) return "暂无";
  const item = items[0];
  return `${item.key}: ${formatRate(item.hit_rate)}`;
}

function formatList(items) {
  if (!Array.isArray(items) || !items.length) return "暂无";
  return items.join(", ");
}

async function refreshKnowledge() {
  const button = $("#refreshKnowledge");
  setLoading(button, true, "刷新中");
  try {
    const payload = await api("/api/knowledge");
    $("#knowledgeText").textContent = payload.content || "知识库为空";
  } catch (error) {
    $("#knowledgeText").textContent = error.message;
  } finally {
    setLoading(button, false);
  }
}

async function runNeutrality() {
  const button = $("#runNeutrality");
  const text = $("#neutralityInput").value;
  setLoading(button, true, "检查中");
  try {
    const result = await api("/api/neutrality", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    renderNeutrality(result);
  } catch (error) {
    $("#neutralityResult").innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
  } finally {
    setLoading(button, false);
  }
}

function renderNeutrality(result) {
  const target = $("#neutralityResult");
  if (!result.findings || !result.findings.length) {
    target.innerHTML = `<p class="empty-state">未发现内置高风险主观措辞。</p>`;
    return;
  }
  target.innerHTML = result.findings.map((item) => `
    <div class="finding-item">
      <strong>${escapeHtml(item.term)}</strong>
      <span>${escapeHtml(item.count)} 次</span>
    </div>
  `).join("");
}

function activateView(name) {
  $$(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.viewTarget === name);
  });
  $$(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${name}`);
  });
  if (name === "tools" && !state.tools.length) refreshTools();
  if (name === "memory") refreshMemory();
  if (name === "knowledge" && $("#knowledgeText").textContent.includes("等待")) refreshKnowledge();
}

function bindEvents() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => activateView(button.dataset.viewTarget));
  });
  $("#askForm").addEventListener("submit", askAgent);
  $("#clearAnswer").addEventListener("click", () => {
    $("#answerPanel").innerHTML = `<p class="empty-state">运行结果会显示在这里。</p>`;
    renderObservations([]);
    $("#metricTools").textContent = "0";
    $("#metricMemory").textContent = "未写入";
    $("#runState").textContent = "待命";
  });
  $("#refreshStatus").addEventListener("click", refreshStatus);
  $("#refreshTools").addEventListener("click", refreshTools);
  $("#toolFilter").addEventListener("input", renderTools);
  $("#refreshMemory").addEventListener("click", () => refreshMemory(true));
  $("#refreshKnowledge").addEventListener("click", refreshKnowledge);
  $("#runNeutrality").addEventListener("click", runNeutrality);
  $$(".chip-button").forEach((button) => {
    button.addEventListener("click", () => {
      $("#questionInput").value = button.dataset.prompt || "";
      if (button.dataset.workflow) $("#workflowSelect").value = button.dataset.workflow;
    });
  });
}

async function boot() {
  bindEvents();
  $("#questionInput").value = localStorage.getItem("fuyao:lastQuestion") || "";
  await Promise.all([refreshStatus(), loadWorkflows(), refreshMemory(false)]);
}

boot();
