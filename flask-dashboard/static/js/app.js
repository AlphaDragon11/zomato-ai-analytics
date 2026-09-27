/* ==========================================================================
   Zomato Analytics — frontend logic
   Every function in this file calls a real Flask endpoint backed by DuckDB.
   Nothing here is mocked; empty states render when a query legitimately
   returns nothing (e.g. sentiment table not yet populated).
   ========================================================================== */

const CHART_COLORS = {
  accent: "#f0a828",
  accentSoft: "rgba(240,168,40,0.25)",
  teal: "#14b8a6",
  tealSoft: "rgba(20,184,166,0.25)",
  positive: "#34d399",
  negative: "#f87171",
  neutral: "#eab308",
  grid: "rgba(255,255,255,0.06)",
  text: "#8d97ab",
};

const charts = {}; // keep references so we can destroy before re-render

/* ---------------------------------------------------------------------- */
/* Utilities                                                               */
/* ---------------------------------------------------------------------- */
function fmtNumber(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
function fmtCurrency(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
}
function fmtDecimal(n, d = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toFixed(d);
}
function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function debounce(fn, wait = 300) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
}

async function api(path) {
  const res = await fetch(path);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) {
    const err = new Error(data.error || `Request failed: ${path}`);
    err.trace = data.trace;
    throw err;
  }
  return data;
}
async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `Request failed: ${path}`);
  return data;
}

function showToast(message, trace) {
  let stack = document.querySelector(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    document.body.appendChild(stack);
  }
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.style.maxWidth = "480px";
  toast.style.whiteSpace = "pre-wrap";
  toast.style.fontFamily = trace ? "monospace" : "inherit";
  toast.style.fontSize = trace ? "0.72rem" : "0.82rem";
  toast.textContent = trace ? (message + "\n\n" + trace) : message;
  stack.appendChild(toast);
  toast.addEventListener("click", () => toast.remove());
  setTimeout(() => toast.remove(), trace ? 20000 : 5000);
}

/* Render an array of row-objects into a <table id=...> */
function renderTable(tableEl, rows, columns) {
  tableEl.innerHTML = "";
  if (!rows || rows.length === 0) {
    tableEl.innerHTML = `<tr><td class="table-empty">No data to show.</td></tr>`;
    return;
  }
  const cols = columns || Object.keys(rows[0]);
  const thead = document.createElement("thead");
  thead.innerHTML = `<tr>${cols.map(c => `<th>${escapeHtml(prettifyHeader(c))}</th>`).join("")}</tr>`;
  const tbody = document.createElement("tbody");
  tbody.innerHTML = rows.map(row => {
    return `<tr>${cols.map(c => {
      const v = row[c];
      const isNum = typeof v === "number";
      return `<td class="${isNum ? "cell-num" : ""}">${escapeHtml(isNum ? formatMaybeCurrency(c, v) : v)}</td>`;
    }).join("")}</tr>`;
  }).join("");
  tableEl.appendChild(thead);
  tableEl.appendChild(tbody);
}
function prettifyHeader(c) {
  return c.replace(/_/g, " ").replace(/\b\w/g, ch => ch.toUpperCase());
}
function formatMaybeCurrency(colName, v) {
  const lc = colName.toLowerCase();
  if (lc.includes("revenue") || lc.includes("value") || lc.includes("price") || lc.includes("cost")) {
    return fmtDecimal(v, 2);
  }
  return v;
}

function baseChartOptions(extra = {}) {
  return Object.assign({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false, labels: { color: CHART_COLORS.text, font: { family: "Inter" } } },
      tooltip: {
        backgroundColor: "#1b2230",
        borderColor: "rgba(255,255,255,0.1)",
        borderWidth: 1,
        titleColor: "#edf0f5",
        bodyColor: "#c7ced9",
        padding: 10,
        cornerRadius: 8,
      },
    },
    scales: {
      x: { ticks: { color: CHART_COLORS.text, font: { size: 11 } }, grid: { color: "transparent" } },
      y: { ticks: { color: CHART_COLORS.text, font: { size: 11 } }, grid: { color: CHART_COLORS.grid } },
    },
  }, extra);
}
function destroyChart(key) {
  if (charts[key]) { charts[key].destroy(); delete charts[key]; }
}

/* ---------------------------------------------------------------------- */
/* Navigation                                                              */
/* ---------------------------------------------------------------------- */
const PAGE_META = {
  overview:  { title: "Executive Overview",  sub: "High-level KPIs and business performance at a glance" },
  analytics: { title: "Advanced Analytics",  sub: "Deep-dive revenue analytics with time-based filters" },
  sentiment: { title: "Sentiment Hub",       sub: "AI-analyzed customer reviews and recurring topics" },
  chatbot:   { title: "AI Chatbot",          sub: "Ask questions in plain English — the AI writes and runs the SQL" },
  explorer:  { title: "Data Explorer",       sub: "Browse restaurants, orders, and reviews with live filters" },
};

const loadedPages = new Set();

function initNav() {
  document.querySelectorAll(".nav-item").forEach(btn => {
    btn.addEventListener("click", () => goToPage(btn.dataset.page));
  });
}
function goToPage(page) {
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("is-active", b.dataset.page === page));
  document.querySelectorAll(".page").forEach(p => p.classList.toggle("is-active", p.dataset.page === page));
  document.getElementById("page-title").textContent = PAGE_META[page].title;
  document.getElementById("page-subtitle").textContent = PAGE_META[page].sub;

  if (!loadedPages.has(page)) {
    loadedPages.add(page);
    ({
      overview: loadOverview,
      analytics: loadAnalytics,
      sentiment: loadSentiment,
      chatbot: initChatbot,
      explorer: loadExplorer,
    })[page]();
  }
}

function initSubtabs(containerId) {
  const container = document.getElementById(containerId);
  container.querySelectorAll(".subtab").forEach(tab => {
    tab.addEventListener("click", () => {
      container.querySelectorAll(".subtab").forEach(t => t.classList.toggle("is-active", t === tab));
      const parent = container.parentElement;
      parent.querySelectorAll(".subpage").forEach(p => p.classList.toggle("is-active", p.dataset.sub === tab.dataset.sub));
    });
  });
}

/* ---------------------------------------------------------------------- */
/* Health check                                                            */
/* ---------------------------------------------------------------------- */
async function checkHealth() {
  const dbRow = document.getElementById("db-status");
  const aiRow = document.getElementById("ai-status");
  try {
    const h = await api("/api/health");
    dbRow.innerHTML = h.db_connected
      ? `<span class="dot dot-ok"></span><span>Database connected</span>`
      : `<span class="dot dot-bad"></span><span>Database offline</span>`;
    aiRow.innerHTML = h.ai_connected
      ? `<span class="dot dot-ok"></span><span>AI engine ready</span>`
      : `<span class="dot dot-bad"></span><span>AI engine not configured</span>`;
  } catch (e) {
    dbRow.innerHTML = `<span class="dot dot-bad"></span><span>Could not reach server</span>`;
  }
}

/* ---------------------------------------------------------------------- */
/* OVERVIEW                                                                */
/* ---------------------------------------------------------------------- */
async function loadOverview() {
  try {
    const k = await api("/api/overview/kpis");
    document.getElementById("kpi-revenue").textContent = fmtCurrency(k.total_revenue);
    document.getElementById("kpi-orders").textContent = fmtNumber(k.total_orders);
    document.getElementById("kpi-restaurants").textContent = fmtNumber(k.total_restaurants);
    document.getElementById("kpi-rating").textContent = k.avg_rating !== null ? `${k.avg_rating}/5.0` : "—";
  } catch (e) { showToast("Could not load KPIs: " + e.message); }

  try {
    const rows = await api("/api/overview/top-cities");
    destroyChart("topCities");
    charts.topCities = new Chart(document.getElementById("chart-top-cities"), {
      type: "bar",
      data: {
        labels: rows.map(r => r.city),
        datasets: [{ data: rows.map(r => r.total_revenue), backgroundColor: CHART_COLORS.accent, borderRadius: 6, maxBarThickness: 34 }],
      },
      options: baseChartOptions(),
    });
  } catch (e) { showToast("Top cities chart failed: " + e.message); }

  try {
    const rows = await api("/api/overview/top-restaurants");
    destroyChart("topRestaurants");
    charts.topRestaurants = new Chart(document.getElementById("chart-top-restaurants"), {
      type: "bar",
      data: {
        labels: rows.map(r => r.restaurant_name),
        datasets: [{ data: rows.map(r => r.total_revenue), backgroundColor: CHART_COLORS.teal, borderRadius: 6 }],
      },
      options: baseChartOptions({ indexAxis: "y" }),
    });
  } catch (e) { showToast("Top restaurants chart failed: " + e.message); }

  try {
    const rows = await api("/api/overview/revenue-by-city");
    destroyChart("revenueShare");
    const palette = ["#f0a828", "#e2941f", "#d38017", "#c46c10", "#b5580a", "#a64705", "#973800", "#882c00", "#792100", "#6a1700"];
    charts.revenueShare = new Chart(document.getElementById("chart-revenue-share"), {
      type: "doughnut",
      data: { labels: rows.map(r => r.city), datasets: [{ data: rows.map(r => r.rev), backgroundColor: palette, borderColor: "#161c28", borderWidth: 2 }] },
      options: baseChartOptions({ cutout: "62%", plugins: { legend: { display: true, position: "right", labels: { color: CHART_COLORS.text, boxWidth: 10, font: { size: 10.5 } } } } }),
    });
  } catch (e) { showToast("Revenue share chart failed: " + e.message); }

  try {
    const rows = await api("/api/overview/orders-vs-revenue");
    destroyChart("ordersRevenue");
    charts.ordersRevenue = new Chart(document.getElementById("chart-orders-revenue"), {
      type: "scatter",
      data: {
        datasets: [{
          label: "Cities",
          data: rows.map(r => ({ x: r.total_orders, y: r.total_revenue })),
          backgroundColor: CHART_COLORS.accentSoft,
          borderColor: CHART_COLORS.accent,
          pointRadius: 6,
          pointHoverRadius: 8,
        }],
      },
      options: baseChartOptions({
        plugins: {
          tooltip: {
            callbacks: { label: (ctx) => `${rows[ctx.dataIndex].city}: ${fmtNumber(ctx.raw.x)} orders, ${fmtCurrency(ctx.raw.y)}` },
          },
        },
        scales: {
          x: { title: { display: true, text: "Total Orders", color: CHART_COLORS.text }, ticks: { color: CHART_COLORS.text }, grid: { color: "transparent" } },
          y: { title: { display: true, text: "Total Revenue", color: CHART_COLORS.text }, ticks: { color: CHART_COLORS.text }, grid: { color: CHART_COLORS.grid } },
        },
      }),
    });
  } catch (e) { showToast("Orders vs revenue chart failed: " + e.message); }

  try {
    const rows = await api("/api/overview/table");
    renderTable(document.getElementById("table-overview"), rows);
  } catch (e) { showToast("Overview table failed: " + e.message); }
}

/* ---------------------------------------------------------------------- */
/* ANALYTICS                                                               */
/* ---------------------------------------------------------------------- */
function initAnalyticsControls() {
  const period = document.getElementById("f-period");
  const minRev = document.getElementById("f-min-revenue");
  const sort = document.getElementById("f-sort");
  const limit = document.getElementById("f-limit");
  const limitVal = document.getElementById("f-limit-value");

  limit.addEventListener("input", () => { limitVal.textContent = limit.value; });

  [period, minRev, sort].forEach(el => el.addEventListener("change", loadAnalytics));
  limit.addEventListener("change", loadAnalytics);

  minRev.parentElement.style.display = "flex";
  period.addEventListener("change", () => {
    const isAllTime = period.value === "all";
    minRev.parentElement.style.display = isAllTime ? "flex" : "none";
    sort.parentElement.style.display = isAllTime ? "flex" : "none";
  });
}

async function loadAnalytics() {
  const period = document.getElementById("f-period").value;
  const minRevenue = document.getElementById("f-min-revenue").value;
  const sort = document.getElementById("f-sort").value;
  const limit = document.getElementById("f-limit").value;

  const titles = {
    all: ["Revenue by City", "Orders vs Revenue"],
    monthly: ["Monthly Revenue Trend", "Monthly Orders Volume"],
    yearly: ["Yearly Revenue Trend", "Yearly Orders Volume"],
  };
  document.getElementById("a-chart1-title").textContent = titles[period][0];
  document.getElementById("a-chart2-title").textContent = titles[period][1];

  try {
    const data = await api(`/api/analytics?period=${period}&min_revenue=${minRevenue}&sort=${sort}&limit=${limit}`);
    const rows = data.rows;
    const s = data.summary;

    document.getElementById("a-kpi-revenue").textContent = fmtCurrency(s.total_revenue);
    document.getElementById("a-kpi-orders").textContent = fmtNumber(s.total_orders);
    document.getElementById("a-kpi-aov").textContent = fmtCurrency(s.avg_order_value);

    const labelKey = period === "all" ? "city" : "period_label";
    const isTrend = period !== "all";

    destroyChart("analytics1");
    charts.analytics1 = new Chart(document.getElementById("chart-analytics-1"), {
      type: isTrend ? "line" : "bar",
      data: {
        labels: rows.map(r => r[labelKey]),
        datasets: [{
          data: rows.map(r => r.total_revenue),
          borderColor: CHART_COLORS.accent,
          backgroundColor: isTrend ? CHART_COLORS.accentSoft : CHART_COLORS.accent,
          fill: isTrend, tension: 0.35, pointRadius: isTrend ? 3 : 0,
          borderRadius: isTrend ? 0 : 6,
        }],
      },
      options: baseChartOptions(),
    });

    destroyChart("analytics2");
    // Original Streamlit used a scatter (orders vs revenue) for "All Time"
    // and a bar (volume) for Monthly/Yearly — restoring that exact split.
    if (period === "all") {
      charts.analytics2 = new Chart(document.getElementById("chart-analytics-2"), {
        type: "scatter",
        data: {
          datasets: [{
            label: "Restaurants",
            data: rows.map(r => ({ x: r.total_orders, y: r.total_revenue })),
            backgroundColor: CHART_COLORS.tealSoft,
            borderColor: CHART_COLORS.teal,
            pointRadius: 6,
            pointHoverRadius: 8,
          }],
        },
        options: baseChartOptions({
          plugins: {
            tooltip: {
              callbacks: { label: (ctx) => `${rows[ctx.dataIndex].restaurant_name}: ${fmtNumber(ctx.raw.x)} orders, ${fmtCurrency(ctx.raw.y)}` },
            },
          },
          scales: {
            x: { title: { display: true, text: "Total Orders", color: CHART_COLORS.text }, ticks: { color: CHART_COLORS.text }, grid: { color: "transparent" } },
            y: { title: { display: true, text: "Total Revenue", color: CHART_COLORS.text }, ticks: { color: CHART_COLORS.text }, grid: { color: CHART_COLORS.grid } },
          },
        }),
      });
    } else {
      charts.analytics2 = new Chart(document.getElementById("chart-analytics-2"), {
        type: "bar",
        data: { labels: rows.map(r => r[labelKey]), datasets: [{ data: rows.map(r => r.total_orders), backgroundColor: CHART_COLORS.teal, borderRadius: 6 }] },
        options: baseChartOptions(),
      });
    }

    renderTable(document.getElementById("table-analytics"), rows);
  } catch (e) { showToast("Analytics failed: " + e.message); }
}

/* ---------------------------------------------------------------------- */
/* SENTIMENT                                                               */
/* ---------------------------------------------------------------------- */
async function loadSentiment() {
  initSubtabs("sentiment-subtabs");

  try {
    const s = await api("/api/sentiment/summary");
    if (!s.enriched) {
      document.getElementById("sentiment-empty").style.display = "block";
      document.getElementById("sentiment-grid").style.display = "none";
      document.getElementById("sent-raw-count").textContent = fmtNumber(s.raw_count);
    } else {
      document.getElementById("sentiment-empty").style.display = "none";
      document.getElementById("sentiment-grid").style.display = "grid";
      document.getElementById("sent-positive").textContent = fmtNumber(s.counts.Positive);
      document.getElementById("sent-negative").textContent = fmtNumber(s.counts.Negative);
      document.getElementById("sent-neutral").textContent = fmtNumber(s.counts.Neutral);

      destroyChart("sentiment");
      charts.sentiment = new Chart(document.getElementById("chart-sentiment"), {
        type: "doughnut",
        data: {
          labels: ["Positive", "Negative", "Neutral"],
          datasets: [{ data: [s.counts.Positive, s.counts.Negative, s.counts.Neutral], backgroundColor: [CHART_COLORS.positive, CHART_COLORS.negative, CHART_COLORS.neutral], borderColor: "#161c28", borderWidth: 2 }],
        },
        options: baseChartOptions({ cutout: "62%", plugins: { legend: { display: true, position: "bottom", labels: { color: CHART_COLORS.text } } } }),
      });
    }
  } catch (e) { showToast("Sentiment summary failed: " + e.message); }

  try {
    const rows = await api("/api/sentiment/reviews");
    const list = document.getElementById("review-list");
    list.innerHTML = rows.length ? rows.map(r => {
      const cls = (r.sentiment || "").toLowerCase();
      return `<div class="review-card">
        <div class="review-top">
          <span class="review-summary">${escapeHtml(r.summary || "Review")}</span>
          <span class="badge badge-${cls}">${escapeHtml(r.sentiment || "—")}</span>
        </div>
        <div class="review-text">${escapeHtml(r.original_text)}</div>
      </div>`;
    }).join("") : `<p class="empty-sub">No reviews to show.</p>`;
  } catch (e) { /* enriched table may not exist yet — silent, empty state already shown above */ }

  try {
    const rows = await api("/api/sentiment/topics");
    destroyChart("topics");
    charts.topics = new Chart(document.getElementById("chart-topics"), {
      type: "bar",
      data: { labels: rows.map(r => r.topic), datasets: [{ data: rows.map(r => r.count), backgroundColor: CHART_COLORS.accent, borderRadius: 6 }] },
      options: baseChartOptions({ indexAxis: "y" }),
    });
  } catch (e) { /* silent */ }
}

/* ---------------------------------------------------------------------- */
/* AI CHATBOT                                                              */
/* ---------------------------------------------------------------------- */
let chatInitialized = false;
function initChatbot() {
  if (chatInitialized) return;
  chatInitialized = true;

  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const messages = document.getElementById("chat-messages");

  appendAssistantText(
    "Hello! I'm your AI data analyst. Ask me anything about your restaurants, orders, or revenue — I'll write and run the SQL for you."
  );

  document.querySelectorAll("#chat-suggestions .chip").forEach(chip => {
    chip.addEventListener("click", () => { input.value = chip.textContent; form.requestSubmit(); });
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    appendUserMessage(question);
    input.value = "";
    document.getElementById("chat-suggestions").style.display = "none";

    const typingEl = appendTyping();
    try {
      const data = await apiPost("/api/chat", { message: question });
      typingEl.remove();
      if (data.limitation) {
        appendAssistantText("I can't answer that with the current schema. Try asking about revenue, orders, restaurants, or ratings.");
      } else if (data.db_error) {
        appendAssistantText("The generated query didn't run successfully against the database.", data.sql, null, data.db_error);
      } else {
        appendAssistantText(`Here are the results — found ${data.row_count} row${data.row_count === 1 ? "" : "s"}.`, data.sql, data.rows);
      }
    } catch (err) {
      typingEl.remove();
      appendAssistantText("Something went wrong reaching the server: " + err.message);
    }
    messages.scrollTop = messages.scrollHeight;
  });

  function appendUserMessage(text) {
    const div = document.createElement("div");
    div.className = "msg msg-user";
    div.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
  }

  function appendTyping() {
    const div = document.createElement("div");
    div.className = "msg msg-assistant";
    div.innerHTML = `<div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div>`;
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function appendAssistantText(text, sql, rows, dbError) {
    const div = document.createElement("div");
    div.className = "msg msg-assistant";
    let html = `<div class="msg-bubble">${escapeHtml(text)}`;
    if (sql) html += `<div class="msg-sql">${escapeHtml(sql)}</div>`;
    if (dbError) html += `<div class="msg-sql" style="color:#f87171">${escapeHtml(dbError)}</div>`;
    if (rows && rows.length) {
      html += `<div class="msg-result-table"><table></table></div>`;
    }
    html += `</div>`;
    div.innerHTML = html;
    messages.appendChild(div);
    if (rows && rows.length) renderTable(div.querySelector("table"), rows);
    messages.scrollTop = messages.scrollHeight;
  }
}

/* ---------------------------------------------------------------------- */
/* DATA EXPLORER                                                           */
/* ---------------------------------------------------------------------- */
function loadExplorer() {
  initSubtabs("explorer-subtabs");
  loadRestaurantsTab();
  loadOrdersTab();
  loadReviewsTab();
}

function loadRestaurantsTab() {
  const search = document.getElementById("r-search");
  const rating = document.getElementById("r-rating");
  const ratingVal = document.getElementById("r-rating-value");

  const run = debounce(async () => {
    try {
      const data = await api(`/api/data/restaurants?search=${encodeURIComponent(search.value)}&min_rating=${rating.value}`);
      document.getElementById("r-count").textContent = fmtNumber(data.count);
      renderTable(document.getElementById("table-restaurants"), data.rows);
    } catch (e) { showToast("Restaurant search failed: " + e.message); }
  }, 300);

  search.addEventListener("input", run);
  rating.addEventListener("input", () => { ratingVal.textContent = fmtDecimal(rating.value, 1); run(); });
  run();
}

async function loadOrdersTab() {
  const citySelect = document.getElementById("o-cities");
  const minOrders = document.getElementById("o-min-orders");
  const minRevenue = document.getElementById("o-min-revenue");

  try {
    const cities = await api("/api/data/cities");
    citySelect.innerHTML = cities.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
  } catch (e) {
    showToast("City list failed to load: " + e.message, e.trace);
  }

  const run = debounce(async () => {
    const selected = Array.from(citySelect.selectedOptions).map(o => o.value).join(",");
    try {
      const data = await api(`/api/data/orders?cities=${encodeURIComponent(selected)}&min_orders=${minOrders.value || 0}&min_revenue=${minRevenue.value || 0}`);
      document.getElementById("o-count").textContent = fmtNumber(data.count);
      renderTable(document.getElementById("table-orders"), data.rows);
    } catch (e) { showToast("Orders filter failed: " + e.message, e.trace); }
  }, 300);

  citySelect.addEventListener("change", run);
  minOrders.addEventListener("input", run);
  minRevenue.addEventListener("input", run);
  run();
}

async function loadReviewsTab() {
  try {
    const data = await api("/api/data/reviews");
    document.getElementById("reviews-source-note").textContent =
      (data.source === "enriched" ? "✅ Showing AI-enriched reviews — " : "📄 Showing raw reviews (AI enrichment not available) — ")
      + fmtNumber(data.count) + " total";
    renderTable(document.getElementById("table-reviews"), data.rows);
  } catch (e) { showToast("Reviews failed: " + e.message); }
}

/* ---------------------------------------------------------------------- */
/* Clock + boot                                                            */
/* ---------------------------------------------------------------------- */
function tickClock() {
  const el = document.getElementById("clock");
  const now = new Date();
  el.textContent = now.toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

document.addEventListener("DOMContentLoaded", () => {
  initNav();
  initAnalyticsControls();
  checkHealth();
  tickClock();
  setInterval(tickClock, 30000);
  loadOverview();
  loadedPages.add("overview");
});