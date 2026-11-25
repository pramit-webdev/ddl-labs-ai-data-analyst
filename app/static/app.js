/* Atlas BI — dashboard interactions, charts and grounded analyst chat */

(function () {
  "use strict";

  const state = {
    meta: null,
    overview: null,
    quality: null,
    period: "last_12_months",
    chartInstances: {},
    askBusy: false,
    charts: {},
  };

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));

  // --------------------------------------------------------------------- utils
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function fmtINR(value) {
    const n = Number(value || 0);
    const sign = n < 0 ? "-" : "";
    const [whole, fraction] = Math.abs(n).toFixed(2).split(".");
    let grouped;
    if (whole.length <= 3) grouped = whole;
    else {
      const tail = whole.slice(-3);
      let head = whole.slice(0, -3);
      const parts = [];
      while (head.length > 2) { parts.unshift(head.slice(-2)); head = head.slice(0, -2); }
      if (head) parts.unshift(head);
      grouped = parts.concat(tail).join(",");
    }
    return `${sign}₹${grouped}.${fraction}`;
  }

  function fmtCompactINR(value) {
    const n = Number(value || 0);
    const sign = n < 0 ? "-" : "";
    const a = Math.abs(n);
    if (a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
    if (a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
    if (a >= 1e3) return `${sign}₹${(a / 1e3).toFixed(1)}K`;
    return `${sign}₹${a.toFixed(0)}`;
  }

  function fmtNum(value) {
    return Number(value || 0).toLocaleString("en-IN");
  }

  function fmtPct(value, digits) {
    const d = digits == null ? 1 : digits;
    return `${Number(value || 0).toFixed(d)}%`;
  }

  function deltaChip(delta, suffix) {
    if (delta == null) return `<span class="chip chip-neutral">No comparison</span>`;
    const up = delta >= 0;
    const cls = up ? "chip-up" : "chip-down";
    const arrow = up ? "▲" : "▼";
    const text = suffix === "pct"
      ? `${arrow} ${Math.abs(delta).toFixed(1)}%`
      : `${arrow} ${fmtCompactINR(Math.abs(delta))}`;
    return `<span class="chip ${cls}">${text}</span>`;
  }

  function inlineMd(text) {
    return String(text || "")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }

  function renderMarkdown(text) {
    const lines = escapeHtml(text || "").split("\n");
    let html = "";
    let list = null;
    const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) { closeList(); continue; }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        closeList();
        const level = Math.min(3, Math.max(1, heading[1].length));
        html += `<h${level}>${inlineMd(heading[2])}</h${level}>`;
        continue;
      }
      const bullet = line.match(/^[-*]\s+(.*)$/);
      if (bullet) {
        if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
        html += `<li>${inlineMd(bullet[1])}</li>`;
        continue;
      }
      const numbered = line.match(/^\d+[.)]\s+(.*)$/);
      if (numbered) {
        if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
        html += `<li>${inlineMd(numbered[1])}</li>`;
        continue;
      }
      closeList();
      html += `<p>${inlineMd(line)}</p>`;
    }
    closeList();
    return html;
  }

  async function api(path, options) {
    const response = await fetch(path, Object.assign({ headers: { Accept: "application/json" } }, options || {}));
    let data;
    try { data = await response.json(); }
    catch (_) { throw new Error(`Request failed (${response.status}).`); }
    if (!response.ok) {
      const error = data && data.error ? data.error : {};
      const err = new Error(error.message || `Request failed (${response.status}).`);
      err.code = error.code;
      err.details = error.details;
      err.status = response.status;
      throw err;
    }
    return data;
  }

  let toastTimer;
  function toast(message, isError) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("error", Boolean(isError));
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 4200);
  }

  function showLoading(el, message) {
    if (el) el.innerHTML = `<p class="muted">${escapeHtml(message || "Loading…")}</p>`;
  }

  // --------------------------------------------------------------------- charts
  const PALETTE = ["#0f766e", "#2563eb", "#e08a0c", "#be123c", "#7c3aed", "#0891b2", "#65a30d", "#ea580c"];

  function formatChartValue(value, valueFormat) {
    if (valueFormat === "number") return fmtNum(Math.round(value));
    if (valueFormat === "percent") return fmtPct(value);
    return fmtINR(value);
  }

  function renderChart(canvasId, spec) {
    if (!spec || typeof Chart === "undefined") return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (state.chartInstances[canvasId]) {
      state.chartInstances[canvasId].destroy();
      delete state.chartInstances[canvasId];
    }
    const isDoughnut = spec.type === "doughnut";
    const isLine = spec.type === "line";
    const horizontal = Boolean(spec.meta && spec.meta.horizontal) && !isDoughnut;
    const datasets = (spec.datasets || []).map((ds, index) => {
      const color = ds.color || (ds.colors && ds.colors[0]) || PALETTE[index % PALETTE.length];
      const colors = ds.colors && ds.colors.length ? ds.colors : (spec.labels || []).map((_, i) => PALETTE[i % PALETTE.length]);
      return {
        label: ds.label || "Value",
        data: ds.data || [],
        borderColor: isDoughnut ? colors : color,
        backgroundColor: isDoughnut ? colors : (isLine ? color + "22" : color),
        borderWidth: isDoughnut ? 1 : 2,
        pointBackgroundColor: color,
        pointRadius: isLine ? 3 : 0,
        pointHoverRadius: isLine ? 5 : 0,
        fill: Boolean(isLine && ds.fill),
        tension: isLine ? 0.35 : 0,
        borderRadius: !isLine && !isDoughnut ? 5 : 0,
        maxBarThickness: 34,
      };
    });

    const valueAxis = {
      beginAtZero: true,
      grid: { color: "#eef2f0" },
      border: { display: false },
      ticks: {
        color: "#6b8079",
        font: { size: 10 },
        callback: (value) => (spec.valueFormat === "number" ? fmtNum(value) : fmtCompactINR(value)),
      },
    };
    const labelAxis = {
      grid: { display: false },
      border: { display: false },
      ticks: { color: "#6b8079", font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 12 },
    };

    const config = {
      type: isDoughnut ? "doughnut" : (isLine ? "line" : "bar"),
      data: { labels: spec.labels || [], datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        font: { family: "Plus Jakarta Sans, Inter, system-ui, sans-serif", size: 12 },
        indexAxis: horizontal ? "y" : "x",
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: {
            display: !isLine || datasets.length > 1,
            position: isDoughnut ? "bottom" : "top",
            labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8, color: "#29423b", font: { size: 11 }, padding: 14 },
          },
          tooltip: {
            backgroundColor: "#10231f",
            padding: 10,
            displayColors: !isDoughnut,
            callbacks: {
              label: (ctx) => {
                const raw = typeof ctx.parsed === "number" ? ctx.parsed : (ctx.parsed && (ctx.parsed.y != null ? ctx.parsed.y : ctx.parsed.x));
                return ` ${ctx.dataset.label}: ${formatChartValue(raw || 0, spec.valueFormat)}`;
              },
            },
          },
        },
        scales: isDoughnut ? {} : (horizontal ? { x: valueAxis, y: labelAxis } : { x: labelAxis, y: valueAxis }),
      },
    };
    state.chartInstances[canvasId] = new Chart(canvas, config);
  }

  // --------------------------------------------------------------------- meta
  async function loadMeta() {
    state.meta = await api("/api/meta");
    const llm = state.meta.llm || {};
    const statusText = llm.configured
      ? `${llm.provider} · ${llm.model || "LLM"}`
      : "Verified offline analyst";
    $("#llm-status").textContent = llm.configured ? "LLM connected" : "Offline analyst";
    $("#status-dot").classList.toggle("ok", true);
    $("#as-of-status").textContent = `Data as of ${state.meta.business.as_of}`;
    $("#hero-llm").textContent = `Analyst: ${statusText}`;
    $("#hero-badge").textContent = `Data as of ${state.meta.business.as_of}`;
    $("#ask-mode-chip").textContent = llm.configured ? `LLM · ${llm.provider}` : "Verified offline";

    const select = $("#period-select");
    select.innerHTML = (state.meta.periods || []).map((p) =>
      `<option value="${escapeHtml(p.key)}">${escapeHtml(p.label)}</option>`
    ).join("");
    select.value = state.period;

    const suggestions = state.meta.suggested_questions || [];
    $("#suggestions").innerHTML = suggestions.slice(0, 6).map((q) =>
      `<button class="suggestion" type="button" data-question="${escapeHtml(q)}">${escapeHtml(q)}</button>`
    ).join("");
    $$("#suggestions .suggestion").forEach((btn) => {
      btn.addEventListener("click", () => sendQuestion(btn.dataset.question));
    });
  }

  // --------------------------------------------------------------------- overview
  const KPI_ICONS = {
    revenue: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17 9 11l4 4 8-8"/><path d="M14 7h7v7"/></svg>',
    orders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/></svg>',
    aov: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8H9.5a2.5 2.5 0 0 0 0 5H14a2.5 2.5 0 0 1 0 5H8"/><path d="M12 6v12"/></svg>',
    customers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
  };
  function renderKPIs(kpis) {
    const defs = [
      { key: "revenue", label: "Net revenue", format: (v) => fmtINR(v) },
      { key: "orders", label: "Completed orders", format: (v) => fmtNum(v) },
      { key: "aov", label: "Average order value", format: (v) => fmtINR(v) },
      { key: "customers", label: "Purchasing customers", format: (v) => fmtNum(v) },
    ];
    $("#kpi-grid").innerHTML = defs.map((def) => {
      const item = kpis.kpis[def.key] || {};
      return `<article class="kpi-card">
        <div class="kpi-head">
          <div class="kpi-label">${def.label}</div>
          <div class="kpi-icon">${KPI_ICONS[def.key]}</div>
        </div>
        <div class="kpi-value">${def.format(item.value)}</div>
        <div class="kpi-foot">
          ${deltaChip(item.delta_pct, "pct")}
          ${item.previous != null ? `<span class="kpi-previous">vs ${def.format(item.previous)}</span>` : `<span class="kpi-previous">no comparison</span>`}
        </div>
      </article>`;
    }).join("");
  }

  function productRows(rows) {
    if (!rows || !rows.length) return `<tr><td colspan="5" class="empty-cell">No sales in this period.</td></tr>`;
    return rows.map((r) => `<tr>
      <td class="rank">${r.rank}</td>
      <td><strong>${escapeHtml(r.name)}</strong></td>
      <td>${escapeHtml(r.category)}</td>
      <td class="num">${fmtINR(r.revenue)}</td>
      <td class="num">${fmtNum(r.units)}</td>
    </tr>`).join("");
  }

  function stateRows(rows) {
    if (!rows || !rows.length) return `<tr><td colspan="4" class="empty-cell">No state-level sales.</td></tr>`;
    return rows.map((r) => `<tr>
      <td class="rank">${r.rank}</td>
      <td><strong>${escapeHtml(r.state)}</strong></td>
      <td class="num">${fmtINR(r.revenue)}</td>
      <td class="num">${fmtNum(r.orders)}</td>
    </tr>`).join("");
  }

  function renderCustomerHealth(c) {
    const items = [
      { value: fmtNum(c.purchasing_customers), label: "Customers who purchased", pct: (c.purchasing_customers / (c.total_customers || 1)) * 100, color: "var(--teal)" },
      { value: `${fmtPct(c.repeat_rate_pct)}`, label: "Repeat buyers", pct: c.repeat_rate_pct, color: "#2563eb" },
      { value: fmtNum(c.inactive_customers), label: `Inactive ${c.inactive_days}+ days`, pct: c.inactive_rate_pct, color: "var(--rose)" },
      { value: fmtNum(c.never_purchased), label: "Never purchased", pct: (c.never_purchased / (c.total_customers || 1)) * 100, color: "var(--saffron)" },
    ];
    $("#customer-health").innerHTML = items.map((it) => `<div class="health-item">
      <div class="health-value">${it.value}</div>
      <div class="health-label">${it.label}</div>
      <div class="health-bar"><span style="width:${Math.min(100, Math.max(0, it.pct))}%;background:${it.color}"></span></div>
    </div>`).join("");
  }

  function renderInsights(insights) {
    $("#insight-grid").innerHTML = (insights || []).map((insight) => `<article class="insight-card">
      <div class="insight-title"><span class="severity ${escapeHtml(insight.severity)}"></span>${escapeHtml(insight.title)}</div>
      <p>${escapeHtml(insight.body)}</p>
    </article>`).join("");
  }

  function renderDecline(data) {
    if (!data) return;
    const change = data.change || {};
    const lines = [];
    lines.push(`<p><strong>${fmtMonthLabel(data.previous_month)} → ${fmtMonthLabel(data.month)}:</strong> revenue ${fmtINR(data.current.revenue)} vs ${fmtINR(data.previous.revenue)} (<strong>${(change.revenue_delta_pct || 0).toFixed(1)}%</strong>).</p>`);
    lines.push("<ul>");
    (data.summary_lines || []).slice(1, 6).forEach((line) => lines.push(`<li>${escapeHtml(line)}</li>`));
    lines.push("</ul>");
    if (data.context_notes && data.context_notes.length) {
      lines.push(`<p class="muted">${escapeHtml(data.context_notes[0])}</p>`);
    }
    $("#decline-summary").innerHTML = lines.join("");
    $("#decline-sub").textContent = `${data.previous_month} → ${data.month} root-cause analysis`;
  }

  function fmtMonthLabel(ym) {
    if (!ym) return "";
    const [year, month] = ym.split("-").map(Number);
    const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return `${names[month - 1]} ${year}`;
  }

  async function loadOverview() {
    showLoading($("#insight-text"), "Loading overview…");
    try {
      const data = await api(`/api/overview?period=${encodeURIComponent(state.period)}`);
      state.overview = data;
      const p = data.period || {};
      const k = data.kpis || {};
      const rev = (k.kpis || {}).revenue || {};
      $("#overview-subtitle").textContent =
        `${p.label || "Selected period"} · ${fmtINR(rev.value || 0)} net revenue across ${fmtNum((k.kpis || {}).orders ? k.kpis.orders.value : 0)} completed orders.`;
      $("#revenue-pulse-sub").textContent = p.comparison_label
        ? `Compared with ${p.comparison_label}.`
        : "Completed orders · shipping excluded.";
      $("#monthly-range").textContent = data.monthly_revenue_chart.subtitle || "Last 12 months";
      $("#insight-text").textContent = data.headline || "The latest numbers are on the dashboard.";

      renderKPIs(k);
      renderInsights(data.insights);
      renderCustomerHealth(data.customer_metrics || {});
      document.querySelector("#top-products-table tbody").innerHTML = productRows(data.top_products);
      document.querySelector("#top-states-table tbody").innerHTML = stateRows(data.top_states);

      renderChart("chart-monthly", data.monthly_revenue_chart);
      renderChart("chart-category", data.category_chart);
      renderChart("chart-payment", data.payment_chart);
      renderChart("chart-tier", data.city_tier_chart);
      renderChart("chart-customers", data.customer_trend_chart);

      if (!data.decline) {
        data.decline = await api("/api/analysis/revenue-decline").catch(() => null);
      }
      renderDecline(data.decline);
    } catch (error) {
      toast(error.message, true);
      $("#insight-text").textContent = "The overview could not be loaded. Please refresh.";
    }
  }

  // --------------------------------------------------------------------- quality
  async function loadQuality() {
    try {
      state.quality = await api("/api/quality");
      const q = state.quality;
      const rawTotal = Object.values(q.raw_counts || {}).reduce((a, b) => a + Number(b || 0), 0);
      const cleanTotal = Object.values(q.clean_counts || {}).reduce((a, b) => a + Number(b || 0), 0);
      const passed = (q.checks || []).filter((c) => c.status === "pass").length;
      const cards = [
        { label: "Raw rows ingested", value: fmtNum(rawTotal) },
        { label: "Clean rows loaded", value: fmtNum(cleanTotal) },
        { label: "Issues repaired", value: fmtNum(q.total_issues) },
        { label: "Checks passed", value: `${passed}/${(q.checks || []).length}` },
      ];
      $("#quality-kpis").innerHTML = cards.map((c) => `<article class="kpi-card">
        <div class="kpi-label">${c.label}</div><div class="kpi-value">${c.value}</div>
      </article>`).join("");
      $("#quality-badge").textContent = `As of ${q.as_of || "—"}`;

      document.querySelector("#counts-table tbody").innerHTML = Object.keys(q.raw_counts || {}).map((key) => {
        const raw = Number(q.raw_counts[key] || 0);
        const clean = Number((q.clean_counts || {})[key] || 0);
        const retained = raw ? (clean / raw) * 100 : 0;
        return `<tr><td><strong>${escapeHtml(key.replace(/_/g, " "))}</strong></td>
          <td class="num">${fmtNum(raw)}</td><td class="num">${fmtNum(clean)}</td>
          <td class="num">${retained.toFixed(1)}%</td></tr>`;
      }).join("");

      $("#checks-list").innerHTML = (q.checks || []).map((c) => `<div class="check-item ${c.status}">
        <span class="check-icon">${c.status === "pass" ? "✓" : "!"}</span>
        <div><strong>${escapeHtml(c.name.replace(/_/g, " "))}</strong><p>${escapeHtml(c.detail)}</p></div>
      </div>`).join("");

      document.querySelector("#issues-table tbody").innerHTML = (q.issues || []).map((i) => `<tr>
        <td><strong>${escapeHtml(i.code.replace(/_/g, " "))}</strong></td>
        <td>${escapeHtml(i.table)}</td><td>${escapeHtml(i.action)}</td>
        <td class="num">${fmtNum(i.count)}</td>
        <td><span class="chip ${i.severity === "error" ? "chip-down" : "chip-neutral"}">${escapeHtml(i.severity)}</span></td>
      </tr>`).join("");

      $("#revenue-definition").textContent = q.revenue_definition || "—";
      $("#currency-definition").textContent = q.currency_definition || "—";
    } catch (error) {
      toast(error.message, true);
    }
  }

  // --------------------------------------------------------------------- chat
  function createMessage(role, meta) {
    const wrap = document.createElement("div");
    wrap.className = `message ${role}`;
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "You" : "A";
    const body = document.createElement("div");
    body.className = "message-body";
    const metaEl = document.createElement("div");
    metaEl.className = "message-meta";
    metaEl.textContent = meta || (role === "user" ? "You" : "Atlas · business analyst");
    const content = document.createElement("div");
    content.className = "message-content";
    body.appendChild(metaEl);
    body.appendChild(content);
    if (role === "user") { wrap.appendChild(avatar); wrap.appendChild(body); }
    else { wrap.appendChild(avatar); wrap.appendChild(body); }
    $("#chat").appendChild(wrap);
    $("#chat").scrollTop = $("#chat").scrollHeight;
    return { wrap, body, content };
  }

  function renderAssistantMessage(contentEl, result) {
    const extras = [];
    if (result.mode === "llm") extras.push(`<span class="chip chip-soft">LLM · ${escapeHtml(result.provider || "")}</span>`);
    else extras.push(`<span class="chip chip-neutral">${result.mode === "fallback" ? "Verified fallback" : "Offline analyst"}</span>`);
    if (result.grounding && result.grounding.status === "verified") {
      extras.push(`<span class="chip chip-up">Grounded · ${result.grounding.checked_numbers} numbers</span>`);
    } else if (result.grounding) {
      extras.push(`<span class="chip chip-down">Grounding check failed</span>`);
    }
    if (result.latency_ms != null) extras.push(`<span class="chip chip-neutral">${result.latency_ms} ms</span>`);

    let traceHtml = "";
    if (result.tool_trace && result.tool_trace.length) {
      traceHtml = `<details class="trace"><summary>How Atlas got this answer · ${result.tool_trace.length} tool call(s)</summary>
        <div class="trace-items">${result.tool_trace.map((t) => `<div class="trace-item">
          <span class="${t.ok ? "trace-ok" : "trace-bad"}">${t.ok ? "●" : "○"}</span>
          <code>${escapeHtml(t.name)}</code>
          <span>${escapeHtml(t.summary || "")}</span>
          <span class="muted">${t.duration_ms || 0} ms</span>
        </div>`).join("")}</div></details>`;
    }

    let chartHtml = "";
    let chartId = null;
    if (result.chart) {
      chartId = `chat-chart-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
      chartHtml = `<div class="message-chart"><canvas id="${chartId}" role="img" aria-label="${escapeHtml(result.chart.title || "Generated chart")}"></canvas></div>`;
      state.charts[chartId] = result.chart;
    }

    const noticeHtml = result.notice ? `<p class="muted" style="font-size:.76rem;margin-top:8px">${escapeHtml(result.notice)}</p>` : "";
    contentEl.innerHTML = renderMarkdown(result.answer || "") + noticeHtml + chartHtml + traceHtml +
      (extras.length ? `<div class="message-extra">${extras.join("")}</div>` : "");
    if (chartId) {
      requestAnimationFrame(() => renderChart(chartId, result.chart));
    }
  }

  async function sendQuestion(question) {
    const text = String(question || "").trim();
    if (!text || state.askBusy) return;
    state.askBusy = true;
    $("#ask-submit").disabled = true;
    const input = $("#ask-input");
    if (input && input.value.trim() === text) input.value = "";
    createMessage("user", "You");
    $("#chat").lastElementChild.querySelector(".message-content").textContent = text;
    const pending = createMessage("assistant", "Atlas · thinking…");
    pending.content.innerHTML = `<div class="typing"><span></span><span></span><span></span></div>`;

    try {
      const result = await api("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ question: text }),
      });
      pending.body.querySelector(".message-meta").textContent = "Atlas · business analyst";
      renderAssistantMessage(pending.content, result);
    } catch (error) {
      pending.body.querySelector(".message-meta").textContent = "Atlas · error";
      pending.content.innerHTML = `<p>${escapeHtml(error.message)}</p>` +
        (error.status === 429 ? `<p class="muted">Please wait a moment before asking another question.</p>` : "");
      toast(error.message, true);
    } finally {
      state.askBusy = false;
      $("#ask-submit").disabled = false;
      $("#chat").scrollTop = $("#chat").scrollHeight;
      if (input) input.focus();
    }
  }

  function initAsk() {
    const form = $("#ask-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const input = $("#ask-input");
      sendQuestion(input.value);
    });
    $("#ask-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        form.requestSubmit();
      }
    });
  }

  // --------------------------------------------------------------------- nav
  function setPage(page) {
    const valid = ["overview", "ask", "quality", "about"];
    const target = valid.includes(page) ? page : "overview";
    $$(".page").forEach((el) => el.classList.toggle("active", el.id === `page-${target}`));
    $$(".nav-link").forEach((el) => el.classList.toggle("active", el.dataset.page === target));
    const titles = { overview: "Overview", ask: "Ask Atlas", quality: "Data quality", about: "About" };
    $("#topbar-title").textContent = titles[target];
    $("#sidebar").classList.remove("open");
    $("#sidebar-scrim").hidden = true;
    if (target === "quality") loadQuality();
    if (target === "ask") { setTimeout(() => $("#ask-input").focus(), 60); }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function initNav() {
    const fromHash = () => (location.hash || "#overview").slice(1);
    window.addEventListener("hashchange", () => setPage(fromHash()));
    setPage(fromHash());
    $("#menu-toggle").addEventListener("click", () => {
      const open = $("#sidebar").classList.toggle("open");
      $("#sidebar-scrim").hidden = !open;
      $("#menu-toggle").setAttribute("aria-expanded", String(open));
    });
    $("#sidebar-scrim").addEventListener("click", () => {
      $("#sidebar").classList.remove("open");
      $("#sidebar-scrim").hidden = true;
      $("#menu-toggle").setAttribute("aria-expanded", "false");
    });
    $("#ask-shortcut").addEventListener("click", () => { location.hash = "#ask"; });
    $("#insight-ask").addEventListener("click", () => { location.hash = "#ask"; setTimeout(() => sendQuestion("Why did revenue decline last month?"), 80); });
    $("#decline-ask").addEventListener("click", () => { location.hash = "#ask"; setTimeout(() => sendQuestion("Why did revenue decline last month?"), 80); });
    $("#refresh-button").addEventListener("click", () => {
      loadOverview().then(() => toast("Overview refreshed."));
    });
    $("#period-select").addEventListener("change", (event) => {
      state.period = event.target.value;
      loadOverview();
    });
  }

  // --------------------------------------------------------------------- boot
  async function boot() {
    initNav();
    initAsk();
    try {
      await loadMeta();
      await loadOverview();
    } catch (error) {
      toast(`Could not start the dashboard: ${error.message}`, true);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
