/* ==========================================================================
   Zomato Analytics v2 — frontend logic
   Every number comes from a real Flask endpoint backed by DuckDB. Insights
   are read-only from a precomputed table — no live Groq calls on this page.
   ========================================================================== */

const CHART_COLORS = {
  accent: "#e23744",
  accentSoft: "rgba(226,55,68,0.18)",
  teal: "#0d9488",
  tealSoft: "rgba(13,148,136,0.18)",
  positive: "#16a34a",
  negative: "#dc2626",
  neutral: "#d97706",
  grid: "rgba(255,255,255,0.08)",
  text: "#c7cbd9",
};

const charts = {};
const state = {
  analytics: { city: "", period: "monthly", page: 1, pageSize: 10, breakdownScope: "city" },
  insights: { page: 1, pageSize: 10, search: "", selectedId: null },
  explorer: { type: "restaurants", page: 1, pageSize: 10 },
};

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
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) throw new Error(data.error || `Request failed: ${path}`);
  return data;
}
function showToast(message, trace) {
  let stack = document.querySelector(".toast-stack");
  if (!stack) { stack = document.createElement("div"); stack.className = "toast-stack"; document.body.appendChild(stack); }
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

function renderTable(tableEl, rows, columns) {
  tableEl.innerHTML = "";
  if (!rows || rows.length === 0) { tableEl.innerHTML = `<tr><td class="table-empty">No data to show.</td></tr>`; return; }
  const cols = columns || Object.keys(rows[0]);
  const thead = document.createElement("thead");
  thead.innerHTML = `<tr>${cols.map(c => `<th>${escapeHtml(prettifyHeader(c))}</th>`).join("")}</tr>`;
  const tbody = document.createElement("tbody");
  tbody.innerHTML = rows.map(row => `<tr>${cols.map(c => {
    const v = row[c];
    const isNum = typeof v === "number";
    return `<td class="${isNum ? "cell-num" : ""}">${escapeHtml(isNum ? formatMaybeCurrency(c, v) : v)}</td>`;
  }).join("")}</tr>`).join("");
  tableEl.appendChild(thead);
  tableEl.appendChild(tbody);
}
function prettifyHeader(c) { return c.replace(/_/g, " ").replace(/\b\w/g, ch => ch.toUpperCase()); }
function formatMaybeCurrency(colName, v) {
  const lc = colName.toLowerCase();
  if (lc.includes("revenue") || lc.includes("value") || lc.includes("price") || lc.includes("cost")) return fmtDecimal(v, 2);
  return v;
}

function renderPager(containerId, page, totalPages, onChange) {
  const el = document.getElementById(containerId);
  el.innerHTML = `
    <button id="${containerId}-prev" ${page <= 1 ? "disabled" : ""}>← Previous</button>
    <span class="pager-label">Page ${page} of ${totalPages}</span>
    <button id="${containerId}-next" ${page >= totalPages ? "disabled" : ""}>Next →</button>
  `;
  document.getElementById(`${containerId}-prev`).addEventListener("click", () => onChange(page - 1));
  document.getElementById(`${containerId}-next`).addEventListener("click", () => onChange(page + 1));
}

function baseChartOptions(extra = {}) {
  return Object.assign({
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false, labels: { color: CHART_COLORS.text, font: { family: "Inter" } } },
      tooltip: { backgroundColor: "#1a1a1f", titleColor: "#fff", bodyColor: "#d8d8dc", padding: 10, cornerRadius: 8 },
    },
    scales: {
      x: { ticks: { color: CHART_COLORS.text, font: { size: 11.5, weight: 500 } }, grid: { color: "transparent" } },
      y: { ticks: { color: CHART_COLORS.text, font: { size: 11.5, weight: 500 } }, grid: { color: CHART_COLORS.grid } },
    },
  }, extra);
}
function destroyChart(key) { if (charts[key]) { charts[key].destroy(); delete charts[key]; } }

/* ---------------------------------------------------------------------- */
/* Navigation                                                              */
/* ---------------------------------------------------------------------- */
const PAGE_META = {
  analytics: { title: "Analytics", sub: "Revenue, orders and performance — overall or by city" },
  sentiment: { title: "Sentiment", sub: "Aggregate review sentiment and per-restaurant insights" },
  chatbot:   { title: "AI Chatbot", sub: "Ask questions in plain English — the AI writes and runs the SQL" },
  explorer:  { title: "Data Explorer", sub: "Browse restaurants, orders, and reviews with live filters" },
};
const loadedPages = new Set();

function initNav() {
  document.querySelectorAll(".nav-item").forEach(btn => btn.addEventListener("click", () => goToPage(btn.dataset.page)));
}
function goToPage(page) {
  document.querySelectorAll(".nav-item").forEach(b => b.classList.toggle("is-active", b.dataset.page === page));
  document.querySelectorAll(".page").forEach(p => p.classList.toggle("is-active", p.dataset.page === page));
  document.getElementById("page-title").textContent = PAGE_META[page].title;
  document.getElementById("page-subtitle").textContent = PAGE_META[page].sub;
  if (!loadedPages.has(page)) {
    loadedPages.add(page);
    ({ analytics: initAnalytics, sentiment: initSentiment, chatbot: initChatbot, explorer: initExplorer })[page]();
  }
}
function initSubtabs(containerId) {
  const container = document.getElementById(containerId);
  container.querySelectorAll(".subtab").forEach(tab => {
    tab.addEventListener("click", () => {
      container.querySelectorAll(".subtab").forEach(t => t.classList.toggle("is-active", t === tab));
      container.parentElement.querySelectorAll(".subpage").forEach(p => p.classList.toggle("is-active", p.dataset.sub === tab.dataset.sub));
    });
  });
}

async function checkHealth() {
  const dbRow = document.getElementById("db-status");
  const aiRow = document.getElementById("ai-status");
  const insRow = document.getElementById("insights-status");
  try {
    const h = await api("/api/health");
    dbRow.innerHTML = h.db_connected ? `<span class="dot dot-ok"></span><span>Database connected</span>` : `<span class="dot dot-bad"></span><span>Database offline</span>`;
    aiRow.innerHTML = h.ai_connected ? `<span class="dot dot-ok"></span><span>AI engine ready</span>` : `<span class="dot dot-bad"></span><span>AI engine not configured</span>`;
    insRow.innerHTML = h.insights_available ? `<span class="dot dot-ok"></span><span>Restaurant insights ready</span>` : `<span class="dot dot-bad"></span><span>Insights not generated yet</span>`;
  } catch (e) {
    dbRow.innerHTML = `<span class="dot dot-bad"></span><span>Could not reach server</span>`;
  }
}

/* ---------------------------------------------------------------------- */
/* ANALYTICS (merged Overview + Analytics, city/area drill-down)          */
/* ---------------------------------------------------------------------- */
async function initAnalytics() {
  try {
    const cities = await api("/api/analytics/cities");
    const select = document.getElementById("an-city");
    cities.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.city;
      opt.textContent = `${c.city} (${fmtCurrency(c.revenue)})`;
      select.appendChild(opt);
    });
  } catch (e) { showToast("City list failed: " + e.message, e.trace); }

  document.getElementById("an-city").addEventListener("change", (e) => {
    state.analytics.city = e.target.value;
    state.analytics.page = 1;
    loadAnalytics();
  });
  document.getElementById("an-period").addEventListener("change", (e) => {
    state.analytics.period = e.target.value;
    loadAnalyticsTrend();
  });

  loadAnalytics();
}

async function loadAnalytics() {
  await Promise.all([
    loadAnalyticsKpis(),
    loadAnalyticsBreakdown(),
    loadAnalyticsTrend(),
    loadAnalyticsTable(),
    loadTopRestaurants(),
    loadCuisineShare(),
    loadRatingDistribution(),
  ]);
}

async function loadAnalyticsKpis() {
  const city = state.analytics.city;
  try {
    const k = await api(`/api/analytics/kpis?city=${encodeURIComponent(city)}`);
    document.getElementById("kpi-revenue").textContent = fmtCurrency(k.total_revenue);
    document.getElementById("kpi-orders").textContent = fmtNumber(k.total_orders);
    document.getElementById("kpi-aov").textContent = fmtCurrency(k.avg_order_value);
    document.getElementById("kpi-restaurants").textContent = fmtNumber(k.total_restaurants);
    document.getElementById("kpi-rating").textContent = k.avg_rating !== null ? `${k.avg_rating}/5.0` : "—";
    document.getElementById("kpi-scope-label").textContent = city ? `in ${city}` : "across all recorded orders";
  } catch (e) { showToast("KPIs failed: " + e.message, e.trace); }
}

async function loadAnalyticsBreakdown() {
  const city = state.analytics.city;
  try {
    const data = await api(`/api/analytics/breakdown?city=${encodeURIComponent(city)}`);
    const rows = data.rows;
    const isArea = data.scope === "area";

    document.getElementById("breakdown-title-1").textContent = isArea ? `Revenue by Area in ${city}` : "Revenue by City";
    document.getElementById("breakdown-title-2").textContent = isArea ? `Orders by Area in ${city}` : "Orders by City";
    document.getElementById("breakdown-hint").textContent = isArea ? "showing areas — change City filter to go back" : "click a bar to drill into that city";

    destroyChart("breakdownRevenue");
    charts.breakdownRevenue = new Chart(document.getElementById("chart-breakdown-revenue"), {
      type: "bar",
      data: { labels: rows.map(r => r.label), datasets: [{ data: rows.map(r => r.revenue), backgroundColor: CHART_COLORS.accent, borderRadius: 6, maxBarThickness: 34 }] },
      options: baseChartOptions({
        onClick: (evt, elements) => {
          if (isArea || !elements.length) return;
          const idx = elements[0].index;
          const clickedCity = rows[idx].label;
          document.getElementById("an-city").value = clickedCity;
          state.analytics.city = clickedCity;
          state.analytics.page = 1;
          loadAnalytics();
        },
      }),
    });

    destroyChart("breakdownOrders");
    charts.breakdownOrders = new Chart(document.getElementById("chart-breakdown-orders"), {
      type: "bar",
      data: { labels: rows.map(r => r.label), datasets: [{ data: rows.map(r => r.orders), backgroundColor: CHART_COLORS.teal, borderRadius: 6 }] },
      options: baseChartOptions({ indexAxis: "y" }),
    });
  } catch (e) { showToast("Breakdown failed: " + e.message, e.trace); }
}

async function loadAnalyticsTrend() {
  const { city, period } = state.analytics;
  try {
    const data = await api(`/api/analytics/trend?period=${period}&city=${encodeURIComponent(city)}`);
    const rows = data.rows;

    destroyChart("trendRevenue");
    charts.trendRevenue = new Chart(document.getElementById("chart-trend-revenue"), {
      type: "line",
      data: { labels: rows.map(r => r.period_label), datasets: [{ data: rows.map(r => r.total_revenue), borderColor: CHART_COLORS.accent, backgroundColor: CHART_COLORS.accentSoft, fill: true, tension: 0.35, pointRadius: 3 }] },
      options: baseChartOptions(),
    });

    destroyChart("trendOrders");
    charts.trendOrders = new Chart(document.getElementById("chart-trend-orders"), {
      type: "bar",
      data: { labels: rows.map(r => r.period_label), datasets: [{ data: rows.map(r => r.total_orders), backgroundColor: CHART_COLORS.teal, borderRadius: 6 }] },
      options: baseChartOptions(),
    });

    destroyChart("trendAov");
    charts.trendAov = new Chart(document.getElementById("chart-trend-aov"), {
      type: "line",
      data: { labels: rows.map(r => r.period_label), datasets: [{ data: rows.map(r => r.avg_order_value), borderColor: CHART_COLORS.neutral, backgroundColor: "rgba(251,191,36,0.18)", fill: true, tension: 0.35, pointRadius: 3 }] },
      options: baseChartOptions(),
    });
  } catch (e) { showToast("Trend failed: " + e.message, e.trace); }
}

async function loadTopRestaurants() {
  const city = state.analytics.city;
  try {
    const rows = await api(`/api/analytics/top-restaurants?city=${encodeURIComponent(city)}&limit=10`);
    destroyChart("topRestaurants");
    charts.topRestaurants = new Chart(document.getElementById("chart-top-restaurants"), {
      type: "bar",
      data: { labels: rows.map(r => r.restaurant_name), datasets: [{ data: rows.map(r => r.total_revenue), backgroundColor: CHART_COLORS.accent, borderRadius: 6 }] },
      options: baseChartOptions({ indexAxis: "y" }),
    });
  } catch (e) { showToast("Top restaurants failed: " + e.message, e.trace); }
}

async function loadCuisineShare() {
  const city = state.analytics.city;
  try {
    const rows = await api(`/api/analytics/cuisine?city=${encodeURIComponent(city)}`);
    const palette = ["#ff4b5c", "#ff8a6a", "#ffb067", "#ffd166", "#a3e635", "#2dd4c4", "#38bdf8", "#818cf8", "#c084fc", "#f472b6"];
    destroyChart("cuisine");
    charts.cuisine = new Chart(document.getElementById("chart-cuisine"), {
      type: "doughnut",
      data: { labels: rows.map(r => r.cuisine), datasets: [{ data: rows.map(r => r.revenue), backgroundColor: palette, borderColor: "#242a36", borderWidth: 2 }] },
      options: baseChartOptions({ cutout: "58%", plugins: { legend: { display: true, position: "right", labels: { color: CHART_COLORS.text, boxWidth: 10, font: { size: 10.5 } } } } }),
    });
  } catch (e) { showToast("Cuisine share failed: " + e.message, e.trace); }
}

async function loadRatingDistribution() {
  const city = state.analytics.city;
  try {
    const rows = await api(`/api/analytics/rating-distribution?city=${encodeURIComponent(city)}`);
    destroyChart("ratingDist");
    charts.ratingDist = new Chart(document.getElementById("chart-rating-dist"), {
      type: "bar",
      data: { labels: rows.map(r => r.bucket), datasets: [{ data: rows.map(r => r.restaurant_count), backgroundColor: CHART_COLORS.teal, borderRadius: 6 }] },
      options: baseChartOptions(),
    });
  } catch (e) { showToast("Rating distribution failed: " + e.message, e.trace); }
}

async function loadAnalyticsTable() {
  const { city, page, pageSize } = state.analytics;
  document.getElementById("table-title").textContent = city ? `Restaurant Performance in ${city}` : "Restaurant Performance (All Cities)";
  try {
    const data = await api(`/api/analytics/table?city=${encodeURIComponent(city)}&page=${page}&page_size=${pageSize}&sort=revenue`);
    renderTable(document.getElementById("table-analytics"), data.rows);
    renderPager("pager-analytics", data.page, data.total_pages, (newPage) => { state.analytics.page = newPage; loadAnalyticsTable(); });
  } catch (e) { showToast("Table failed: " + e.message, e.trace); }
}

/* ---------------------------------------------------------------------- */
/* SENTIMENT (overview + precomputed restaurant insights)                 */
/* ---------------------------------------------------------------------- */
function initSentiment() {
  initSubtabs("sentiment-subtabs");
  loadSentimentOverview();

  const search = document.getElementById("ins-search");
  search.addEventListener("input", debounce(() => { state.insights.search = search.value; state.insights.page = 1; loadInsightsList(); }, 300));
  loadInsightsList();
}

async function loadSentimentOverview() {
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
        data: { labels: ["Positive", "Negative", "Neutral"], datasets: [{ data: [s.counts.Positive, s.counts.Negative, s.counts.Neutral], backgroundColor: [CHART_COLORS.positive, CHART_COLORS.negative, CHART_COLORS.neutral], borderColor: "#262b38", borderWidth: 2 }] },
        options: baseChartOptions({ cutout: "62%", plugins: { legend: { display: true, position: "bottom", labels: { color: CHART_COLORS.text } } } }),
      });
    }
  } catch (e) { showToast("Sentiment overview failed: " + e.message, e.trace); }

  try {
    const rows = await api("/api/sentiment/topics");
    destroyChart("topics");
    charts.topics = new Chart(document.getElementById("chart-topics"), {
      type: "bar",
      data: { labels: rows.map(r => r.topic), datasets: [{ data: rows.map(r => r.count), backgroundColor: CHART_COLORS.accent, borderRadius: 6 }] },
      options: baseChartOptions({ indexAxis: "y" }),
    });
  } catch (e) { /* silent — topics depend on the optional enrich_reviews.py step */ }
}

async function loadInsightsList() {
  const { page, pageSize, search } = state.insights;
  try {
    const data = await api(`/api/insights/list?page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}`);
    const emptyEl = document.getElementById("insights-empty");
    const layoutEl = document.getElementById("insights-layout");

    if (data.total_count === 0 && !search) {
      emptyEl.style.display = "block";
      layoutEl.style.display = "none";
      return;
    }
    emptyEl.style.display = "none";
    layoutEl.style.display = "grid";

    const list = document.getElementById("insights-list");
    list.innerHTML = data.rows.map(r => `
      <div class="insight-row" data-id="${escapeHtml(r.restaurant_id)}">
        <div>
          <div class="insight-row-name">${escapeHtml(r.restaurant_name)}</div>
          <div class="insight-row-meta">${escapeHtml(r.city || "")}</div>
        </div>
        <span class="insight-row-count">${r.review_count} reviews</span>
      </div>
    `).join("");
    list.querySelectorAll(".insight-row").forEach(row => {
      row.addEventListener("click", () => {
        list.querySelectorAll(".insight-row").forEach(r => r.classList.remove("is-active"));
        row.classList.add("is-active");
        loadInsightDetail(row.dataset.id);
      });
    });
    renderPager("pager-insights", data.page, data.total_pages, (newPage) => { state.insights.page = newPage; loadInsightsList(); });
  } catch (e) { showToast("Insights list failed: " + e.message, e.trace); }
}

async function loadInsightDetail(restaurantId) {
  const detail = document.getElementById("insights-detail");
  detail.innerHTML = `<p class="empty-sub">Loading…</p>`;
  try {
    const data = await api(`/api/insights/${encodeURIComponent(restaurantId)}`);
    if (!data.found) { detail.innerHTML = `<p class="empty-sub">No insight found for this restaurant.</p>`; return; }
    const r = data.insight;
    const negThemes = (r.top_negative_themes || "").split(",").map(t => t.trim()).filter(Boolean);
    const posThemes = (r.top_positive_themes || "").split(",").map(t => t.trim()).filter(Boolean);

    detail.innerHTML = `
      <h2 class="insight-detail-name">${escapeHtml(r.restaurant_name)}</h2>
      <p class="insight-detail-city">${escapeHtml(r.city || "")} · based on ${r.review_count} reviews</p>
      <div class="insight-summary">${escapeHtml(r.summary || "No summary available.")}</div>
      <div class="insight-counts">
        <div class="insight-count-pill positive">Positive<span>${r.positive_count ?? 0}</span></div>
        <div class="insight-count-pill negative">Negative<span>${r.negative_count ?? 0}</span></div>
        <div class="insight-count-pill neutral">Neutral<span>${r.neutral_count ?? 0}</span></div>
      </div>
      <div class="insight-themes">
        <h4>Recurring complaints</h4>
        ${negThemes.length ? negThemes.map(t => `<span class="theme-chip negative">${escapeHtml(t)}</span>`).join("") : '<p class="empty-sub">None flagged.</p>'}
        <h4>Recurring praise</h4>
        ${posThemes.length ? posThemes.map(t => `<span class="theme-chip positive">${escapeHtml(t)}</span>`).join("") : '<p class="empty-sub">None flagged.</p>'}
      </div>
    `;
  } catch (e) { detail.innerHTML = `<p class="empty-sub">Failed to load: ${escapeHtml(e.message)}</p>`; }
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

  appendAssistantText("Hello! I'm your AI data analyst. Ask me anything about your restaurants, orders, or revenue — I'll write and run the SQL for you.");
  document.querySelectorAll("#chat-suggestions .chip").forEach(chip => chip.addEventListener("click", () => { input.value = chip.textContent; form.requestSubmit(); }));

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
      if (data.limitation) appendAssistantText("I can't answer that with the current schema. Try asking about revenue, orders, restaurants, or ratings.");
      else if (data.db_error) appendAssistantText("The generated query didn't run successfully against the database.", data.sql, null, data.db_error);
      else appendAssistantText(`Here are the results — found ${data.row_count} row${data.row_count === 1 ? "" : "s"}.`, data.sql, data.rows);
    } catch (err) { typingEl.remove(); appendAssistantText("Something went wrong reaching the server: " + err.message); }
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
    if (rows && rows.length) html += `<div class="msg-result-table"><table></table></div>`;
    html += `</div>`;
    div.innerHTML = html;
    messages.appendChild(div);
    if (rows && rows.length) renderTable(div.querySelector("table"), rows);
    messages.scrollTop = messages.scrollHeight;
  }
}

/* ---------------------------------------------------------------------- */
/* DATA EXPLORER (unified)                                                 */
/* ---------------------------------------------------------------------- */
async function initExplorer() {
  try {
    const cities = await api("/api/explorer/cities");
    const select = document.getElementById("ex-city");
    cities.forEach(c => { const opt = document.createElement("option"); opt.value = c; opt.textContent = c; select.appendChild(opt); });
  } catch (e) { /* non-fatal — filter just won't have options */ }

  const typeSelect = document.getElementById("ex-type");
  const search = document.getElementById("ex-search");
  const city = document.getElementById("ex-city");
  const rating = document.getElementById("ex-rating");
  const ratingValue = document.getElementById("ex-rating-value");
  const minOrders = document.getElementById("ex-min-orders");
  const minRevenue = document.getElementById("ex-min-revenue");

  function updateFieldVisibility() {
    const type = typeSelect.value;
    document.getElementById("ex-rating-field").style.display = type === "restaurants" ? "flex" : "none";
    document.getElementById("ex-min-orders-field").style.display = type === "orders" ? "flex" : "none";
    document.getElementById("ex-min-revenue-field").style.display = type === "orders" ? "flex" : "none";
  }

  const reload = debounce(() => { state.explorer.page = 1; loadExplorer(); }, 300);

  typeSelect.addEventListener("change", () => { state.explorer.type = typeSelect.value; updateFieldVisibility(); reload(); });
  search.addEventListener("input", reload);
  city.addEventListener("change", reload);
  rating.addEventListener("input", () => { ratingValue.textContent = fmtDecimal(rating.value, 1); reload(); });
  minOrders.addEventListener("input", reload);
  minRevenue.addEventListener("input", reload);

  updateFieldVisibility();
  loadExplorer();
}

async function loadExplorer() {
  const type = document.getElementById("ex-type").value;
  const search = document.getElementById("ex-search").value;
  const city = document.getElementById("ex-city").value;
  const rating = document.getElementById("ex-rating").value;
  const minOrders = document.getElementById("ex-min-orders").value || 0;
  const minRevenue = document.getElementById("ex-min-revenue").value || 0;
  const { page, pageSize } = state.explorer;

  let url = `/api/explorer?type=${type}&page=${page}&page_size=${pageSize}&search=${encodeURIComponent(search)}&city=${encodeURIComponent(city)}`;
  if (type === "restaurants") url += `&min_rating=${rating}`;
  if (type === "orders") url += `&min_orders=${minOrders}&min_revenue=${minRevenue}`;

  try {
    const data = await api(url);
    document.getElementById("ex-count").textContent = fmtNumber(data.total_count);
    renderTable(document.getElementById("table-explorer"), data.rows);
    renderPager("pager-explorer", data.page, data.total_pages, (newPage) => { state.explorer.page = newPage; loadExplorer(); });
  } catch (e) { showToast("Explorer failed: " + e.message, e.trace); }
}

/* ---------------------------------------------------------------------- */
/* Clock + boot                                                            */
/* ---------------------------------------------------------------------- */
function tickClock() {
  const el = document.getElementById("clock");
  el.textContent = new Date().toLocaleString("en-IN", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

document.addEventListener("DOMContentLoaded", () => {
  initNav();
  checkHealth();
  tickClock();
  setInterval(tickClock, 30000);
  initAnalytics();
  loadedPages.add("analytics");
});