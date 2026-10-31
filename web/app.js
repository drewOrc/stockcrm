/* Stock CRM front-end. Vanilla JS, talks to /api. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const L = {
  stage: { watch: "觀察", hold: "持有", exited: "已出場" },
  tier: { core: "核心", satellite: "衛星", watch: "觀察", blacklist: "黑名單" },
  thesis: { intact: "成立", shaken: "動搖", broken: "失效" },
  kind: { buy: "買進", sell: "賣出", add: "加碼", trim: "減碼", news: "新聞", earnings: "財報", call: "法說", thought: "想法", review: "回顧", alert: "警示" },
  rkind: { price: "到價", earnings: "財報", revenue: "營收", review: "回顧", other: "其他" },
  conf: { high: "高", medium: "中", low: "低" },
  pkind: { interaction: "互動紀錄", edge: "供應鏈關係", thesis_status: "論點狀態", reminder: "提醒", driver: "驅動因子", driver_value: "驅動因子數值", checkpoint: "檢查點", checkpoint_resolve: "檢查點結果", metric: "標準指標" },
  effect: { supports: "支持", weakens: "削弱", confirms: "確認" },
  signal: { no_data: "無資料", on_track: "符合", ahead: "超前", behind: "落後", weakened: "被削弱" },
  cstatus: { pending: "待確認", confirmed: "已確認", missed: "未達成" },
  metric: { revenue_m: "月營收", revenue_q: "季營收", gross_margin: "毛利率", opex: "營業費用", eps: "EPS", shares: "股數" },
  relation: { customer: "客戶", partner: "夥伴", holding: "成分股" },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => v == null ? "?" : Math.round(v * 100) + "%";
const spct = (v) => v == null ? "" : (v > 0 ? "+" : "") + (v * 100).toFixed(0) + "%";
const num = (v) => v == null ? "–" : (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-US") : String(v));
const tpe = (ts) => ts ? new Date(ts.replace(" ", "T") + "Z").toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
const opts = (map, sel) => Object.entries(map).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${v}</option>`).join("");

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, { headers: { "Content-Type": "application/json" }, ...opts, body: opts.body ? JSON.stringify(opts.body) : undefined });
  if (!r.ok) { const t = await r.text(); toast("錯誤：" + t.slice(0, 200)); throw new Error(t); }
  return r.json();
}
function toast(msg) { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 2600); }

/* ---------------- tabs */
$$("nav button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
function showTab(name) {
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  ({ board: loadBoard, graph: loadGraph, morning: loadMorning, proposals: loadProposals })[name]();
}

/* ---------------- board */
async function loadBoard() {
  const rows = await api("/positions");
  const cols = { hold: [], watch: [], exited: [] };
  rows.forEach((p) => cols[p.stage].push(p));
  $("#board").innerHTML = ["hold", "watch", "exited"].map((s) => `
    <div class="col"><h3><span>${L.stage[s]}</span><span>${cols[s].length}${s === "hold" ? " · " + cols[s].reduce((a, p) => a + p.position_pct, 0).toFixed(1) + "%" : ""}</span></h3>
      ${cols[s].map(cardHtml).join("") || '<div class="empty">沒有卡片</div>'}
    </div>`).join("");
  $$(".card").forEach((c) => c.onclick = () => openCard(c.dataset.id));
}
function cardHtml(p) {
  const stale = p.stage === "hold" && p.days_since_update >= 30;
  return `<div class="card ${p.tier}" data-id="${p.id}">
    <div class="top"><span><span class="name">${esc(p.name)}</span> <span class="ticker">${esc(p.company_id)}</span></span>
      <span>${p.health_issues ? `<span class="badge hi" title="健康度警示">⚠ ${p.health_issues}</span> ` : ""}<span class="badge ${p.tier}">${L.tier[p.tier]}</span></span></div>
    ${p.latest_brief ? `<div class="brief-line" title="${esc(p.latest_brief.date)}">${esc(p.latest_brief.text)}</div>` : ""}
    <div class="meta">
      <span><i class="dot ${p.thesis_status}"></i>論點${L.thesis[p.thesis_status]}</span>
      ${p.stage === "hold" ? `<span>部位 ${p.position_pct}%${p.shares ? ` · ${p.shares.toLocaleString("en-US")} 股${p.avg_cost ? " @ " + p.avg_cost : ""}` : ""}</span>` : ""}
      <span class="${stale ? "stale" : ""}">${p.days_since_update == null ? "" : p.days_since_update + " 天未更新"}</span>
      <span>${p.interaction_count} 筆紀錄</span>
    </div></div>`;
}

/* ---------------- drawer / card detail */
const drawer = $("#drawer"), backdrop = $("#backdrop");
backdrop.onclick = closeDrawer;
function closeDrawer() { drawer.classList.remove("open"); backdrop.classList.remove("show"); }
async function openCard(ref) {
  let p;
  try { p = await api("/positions/" + encodeURIComponent(ref)); } catch { return; }
  renderCard(p);
  drawer.classList.add("open"); backdrop.classList.add("show");
}

function driversHtml(p) {
  const rows = p.drivers.map((d) => {
    const lc = d.latest_comparison;
    const last = d.series.length ? d.series[d.series.length - 1] : null;
    return `<tr>
      <td>${d.critical ? "★ " : ""}${esc(d.name)} <span class="kv">${esc(d.unit || "")}</span>${d.sensitivity ? `<div class="kv">${esc(d.sensitivity)}</div>` : ""}</td>
      <td class="num">${lc ? `${num(lc.actual)} / ${num(lc.model)}<div class="kv">${lc.period} ${spct(lc.gap_pct)}</div>` : (last ? `${last.actual != null ? num(last.actual) : "–"} / ${last.model != null ? num(last.model) : "–"}<div class="kv">${last.period}</div>` : "–")}</td>
      <td><span class="sig ${d.signal}">${L.signal[d.signal]}</span>${d.latest_effect ? `<div class="kv">最近證據：${L.effect[d.latest_effect]}</div>` : ""}</td>
      <td class="actions-cell"><button class="ghost tiny" data-dv="${d.id}" title="填數值">＋值</button> <button class="ghost tiny" data-ddel="${d.id}" title="刪除">×</button></td>
    </tr>
    <tr class="series-row" id="series-${d.id}" hidden><td colspan="4">
      <table class="inner"><tr><th>期間</th><th class="num">模型</th><th class="num">實際</th><th class="num">差距</th><th>來源</th></tr>
      ${d.series.map((s) => `<tr><td>${s.period}</td><td class="num">${s.model != null ? num(s.model) : "–"}</td><td class="num">${s.actual != null ? num(s.actual) : "–"}</td><td class="num">${spct(s.gap_pct)}</td><td class="kv">${esc(s.source || "")}</td></tr>`).join("") || '<tr><td colspan="5" class="kv">還沒有數值</td></tr>'}</table>
      <form class="row4 form-dv" data-id="${d.id}">
        <input name="period" placeholder="2026 / 2026Q2 / 2026-05" required><select name="kind"><option value="model">模型</option><option value="actual">實際</option></select>
        <input name="value" type="number" step="any" placeholder="數值" required><input name="source" placeholder="來源">
        <button class="ghost tiny" style="grid-column:1/-1">儲存數值</button></form>
      ${d.evidence.length ? `<div class="kv" style="margin-top:6px">證據：${d.evidence.map((e) => `${tpe(e.ts).slice(0, 10)} ${e.effect ? "［" + L.effect[e.effect] + "］" : ""}${esc(e.content)}`).join("<br>")}</div>` : ""}
    </td></tr>`;
  }).join("");
  return `<table class="drivers"><tr><th>驅動因子</th><th class="num">實際 / 模型</th><th>訊號</th><th></th></tr>${rows || '<tr><td colspan="4" class="kv">還沒有驅動因子。★ 是關鍵因子，被削弱或落後模型會讓論點動搖。</td></tr>'}</table>
    <form id="form-driver" class="row4" style="margin-top:8px">
      <input name="name" placeholder="名稱，例如 出貨台數" required><input name="unit" placeholder="單位">
      <input name="sensitivity" placeholder="敏感度，例如 每台 0.25 EPS"><label class="inline"><input type="checkbox" name="critical" checked> 關鍵</label>
      <button class="ghost tiny" style="grid-column:1/-1">＋ 新增驅動因子</button></form>`;
}

function checkpointsHtml(p) {
  const rows = p.checkpoints.map((c) => `
    <div class="cp ${c.status} ${c.overdue ? "overdue" : ""}">
      <div><span class="pill-s ${c.overdue ? "bad" : c.status}">${c.overdue ? "逾期" : L.cstatus[c.status]}</span> <b>${esc(c.title)}</b>
        <span class="kv">${c.expected_by}${c.status === "pending" ? (c.days_left >= 0 ? `，還有 ${c.days_left} 天` : `，逾期 ${-c.days_left} 天`) : ""}${c.driver_name ? " · " + esc(c.driver_name) : ""}</span>
        ${c.evidence_needed ? `<div class="kv">要看到：${esc(c.evidence_needed)}</div>` : ""}
        ${c.evidence ? `<div class="kv">證據：${esc(c.evidence)}</div>` : ""}</div>
      <div class="actions-cell">${c.status === "pending" ? `<button class="ghost tiny" data-cok="${c.id}">確認</button> <button class="ghost tiny" data-cmiss="${c.id}">未達</button>` : `<button class="ghost tiny" data-creopen="${c.id}">重開</button>`} <button class="ghost tiny" data-cdel="${c.id}">×</button></div>
    </div>`).join("");
  return `${rows || '<div class="kv">還沒有檢查點。「到某個日期前應該看到某個證據」，逾期沒證據就是警訊。</div>'}
    <form id="form-cp" class="row4" style="margin-top:8px">
      <input name="title" placeholder="標題，例如 首批出貨" required><input name="expected_by" type="date" required>
      <input name="evidence_needed" placeholder="要看到什麼證據"><select name="driver_id"><option value="">不綁驅動因子</option>${p.drivers.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select>
      <button class="ghost tiny" style="grid-column:1/-1">＋ 新增檢查點</button></form>`;
}

function metricsHtml(p) {
  const m = p.metrics;
  const rr = m.run_rate.map((r) => `<tr class="${r.flag ? "flag" : ""}"><td>${r.period} run-rate</td><td class="num">${num(r.model)}</td><td class="num">${num(r.projected)}<div class="kv">${r.months_available} 個月${r.final ? "，已完整" : "，推估"}</div></td><td class="num">${spct(r.gap_pct)}</td></tr>`).join("");
  const cmp = m.comparisons.filter((t) => t.metric !== "revenue_m").map((t) => `<tr class="${t.flag ? "flag" : ""}"><td>${t.label} ${t.period}</td><td class="num">${t.metric === "gross_margin" ? pct(t.model) : num(t.model)}</td><td class="num">${t.metric === "gross_margin" ? pct(t.actual) : num(t.actual)}</td><td class="num">${t.gap == null ? "" : (t.metric === "gross_margin" ? (t.gap * 100).toFixed(1) + " pp" : spct(t.gap))}</td></tr>`).join("");
  const monthly = m.monthly.length ? `<div class="kv" style="margin-top:6px">月營收：${m.monthly.map((x) => `${x.period} ${num(x.value)}`).join("、")}${m.monthly_yoy.length ? "<br>年增率：" + m.monthly_yoy.map((x) => `${x.period} ${spct(x.yoy)}`).join("、") : ""}</div>` : "";
  return `<table><tr><th>指標</th><th class="num">模型</th><th class="num">實際</th><th class="num">差距</th></tr>${rr}${cmp}${!rr && !cmp ? '<tr><td colspan="4" class="kv">還沒有可對照的指標。填入模型季營收與實際月營收，就會算 run-rate。</td></tr>' : ""}</table>${monthly}
    <form id="form-metric" class="row4" style="margin-top:8px">
      <select name="metric">${opts(L.metric)}</select><input name="period" placeholder="2026Q2 / 2026-05" required>
      <select name="kind"><option value="model">模型</option><option value="actual">實際</option></select><input name="value" type="number" step="any" placeholder="數值（千元 / 0..1 / 千股）" required>
      <button class="ghost tiny" style="grid-column:1/-1">儲存指標</button></form>`;
}

const srcLinks = (sources) => (sources || []).length ? " · 來源：" + sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a>${s.date ? " (" + esc(s.date) + ")" : ""}`).join("、") : "";

function renderCard(p) {
  const c = p.concentration;
  const concBadge = c.largest_customer ? `<span class="badge ${{ high: "hi", medium: "mid", low: "lo" }[c.flag]}">最大客戶 ${esc(c.largest_customer_name)} ${pct(c.largest_customer_share)}</span>` : '<span class="kv">尚無客戶資料</span>';
  const rel = (list, key, nameKey, dir) => list.length ? list.map((e) => `
      <div class="rel"><span>${e.relation === "partner" ? "夥伴" : e.relation === "holding" ? (dir === "客戶" ? "成分股" : "被持有於") : dir} <a data-open="${e[key]}">${esc(e[nameKey])}</a> <span class="kv">${e[key]}</span></span>
        <span class="kv">${pct(e.revenue_share)} · ${e.lag_days ?? "?"} 天 · 信心${L.conf[e.confidence]}</span></div>`).join("") : '<div class="kv">無</div>';
  const linkOpts = `<option value="">不關聯</option>${p.drivers.map((d) => `<option value="d:${d.id}">驅動：${esc(d.name)}</option>`).join("")}${p.checkpoints.filter((k) => k.status === "pending").map((k) => `<option value="c:${k.id}">檢查點：${esc(k.title)}</option>`).join("")}`;
  const health = p.health.issues.length ? `<div class="warn-box"><b>健康度警示</b><ul class="issues">${p.health.issues.map((i) => `<li class="${i.severity}">${esc(i.message)}</li>`).join("")}</ul>${p.health.suggest_shaken ? '<div class="kv">有高嚴重度問題而論點仍標成立。建議改為動搖，或處理上面的項目。</div>' : ""}</div>` : '<div class="ok-box">健康度正常：沒有逾期檢查點、落後的驅動因子或偏離的指標。</div>';

  $("#drawer-content").innerHTML = `
    <button class="close" id="drawer-close">×</button>
    <h2>${esc(p.company.name)} <span class="ticker">${esc(p.company_id)}</span> <span class="badge ${p.tier}">${L.tier[p.tier]}</span></h2>
    <div class="kv">${esc(p.company.sector || "")} · 想法來源：${esc(p.idea_source || "未填")} · ${p.days_since_update ?? "?"} 天未更新${p.tags?.length ? " · " + p.tags.map(esc).join(", ") : ""}</div>
    <section>${health}</section>

    <section><h4>基本介紹 <span class="kv">公司在做什麼</span></h4>
      ${p.briefs.profile ? `<div class="brief">${md(p.briefs.profile.content)}<div class="kv">${p.briefs.profile.date} · ${esc(p.briefs.profile.created_by)}${srcLinks(p.briefs.profile.sources)}</div></div>` : '<div class="kv">尚無簡介。在 Claude Code 說「幫我寫 ' + esc(p.company.name) + ' 的公司簡介」，或在下面直接填。</div>'}
      <form id="form-profile" style="margin-top:6px"><textarea name="content" rows="2" placeholder="兩三句：賣什麼、賣給誰、怎麼賺錢" required></textarea>
        <div style="margin-top:4px;display:flex;justify-content:flex-end"><button class="ghost tiny">儲存簡介</button></div></form></section>

    <section><h4>最近狀況 <span class="kv">近期重點，agent 每天提案、你核准</span></h4>
      ${p.briefs.highlights.length ? p.briefs.highlights.map((b) => `<div class="brief"><div class="kv"><b>${b.date}</b> · ${esc(b.created_by)}</div>${md(b.content)}${srcLinks(b.sources) ? `<div class="kv">${srcLinks(b.sources)}</div>` : ""}</div>`).join("") : '<div class="kv">尚無近期重點。早晨排程會自動提案。</div>'}</section>

    <section><div class="row3">
      <label>階段<select id="f-stage">${opts(L.stage, p.stage)}</select></label>
      <label>分層<select id="f-tier">${opts(L.tier, p.tier)}</select></label>
      <label>部位 %<input id="f-pct" type="number" step="0.5" min="0" value="${p.position_pct}"></label>
    </div>
    <div id="tier-msg"></div>
    <div class="row3">
      <label>目標價<input id="f-target" type="number" step="any" value="${p.target_price ?? ""}"></label>
      <label>停損價<input id="f-stop" type="number" step="any" value="${p.stop_price ?? ""}"></label>
      <label>目標價依據<input id="f-basis" placeholder="PE 30 × 2027E EPS" value="${esc(p.target_basis || "")}"></label>
    </div>
    <div class="row3">
      <label>持有股數<input id="f-shares" type="number" step="1" min="0" value="${p.shares ?? ""}"></label>
      <label>平均成本<input id="f-cost" type="number" step="any" value="${p.avg_cost ?? ""}"></label>
      <label>成本金額<input disabled value="${p.shares && p.avg_cost ? Math.round(p.shares * p.avg_cost).toLocaleString("en-US") : ""}"></label>
    </div>
    <label>估值模型檔案<input id="f-model" placeholder="試算表路徑或連結" value="${esc(p.model_url || "")}"></label></section>

    <section><h4>投資論點 <span class="seg" id="thesis-seg">${Object.entries(L.thesis).map(([k, v]) => `<button data-s="${k}" class="${k === p.thesis_status ? "on" : ""}">${v}</button>`).join("")}</span></h4>
      <textarea id="f-thesis" rows="3">${esc(p.thesis || "")}</textarea></section>
    <section><h4>失效條件</h4><textarea id="f-inval" rows="2">${esc(p.invalidation || "")}</textarea>
      <div style="margin-top:8px;display:flex;gap:8px;justify-content:flex-end"><button class="primary" id="btn-save">儲存卡片</button></div></section>

    <section><h4>驅動因子 <span class="kv">論點的量化假設，模型 vs 實際</span>
      ${p.drivers.length === 0 && p.checkpoints.length === 0 ? `<select id="sel-template" class="tiny-select"><option value="">套用模板…</option></select>` : ""}</h4>
      ${driversHtml(p)}</section>

    <section><h4>檢查點 <span class="kv">到某日前該看到的證據</span></h4>${checkpointsHtml(p)}</section>

    <section><h4>標準指標 <span class="kv">月營收、季報六個數，模型 vs 實際</span></h4>${metricsHtml(p)}</section>

    <section><h4>供應鏈關係</h4>
      <div style="margin-bottom:6px">${concBadge}</div>
      ${rel(p.customers, "customer_id", "customer_name", "客戶")}
      ${rel(p.suppliers, "supplier_id", "supplier_name", "供應商")}
      <div style="margin-top:8px"><button class="ghost tiny" id="btn-exposure">看組合對 ${esc(p.company.name)} 的間接曝險</button><div id="exposure"></div></div>
    </section>

    <section><h4>提醒</h4>
      ${p.reminders.map((r) => `<div class="rel"><span>${r.due_date} · ${L.rkind[r.kind]} · ${esc(r.content)}</span><button class="ghost tiny" data-done="${r.id}">完成</button></div>`).join("") || '<div class="kv">無</div>'}
      <form id="form-rem" class="row3" style="margin-top:8px">
        <input name="due_date" type="date" required><select name="kind">${opts(L.rkind)}</select>
        <input name="content" placeholder="提醒內容" required><button class="ghost tiny" style="grid-column:1/-1">＋ 新增提醒</button>
      </form></section>

    <section><h4>互動紀錄（${p.interactions.length}）</h4>
      <form id="form-int">
        <div class="row3">
          <select name="kind">${opts(L.kind)}</select>
          <input name="price" type="number" step="any" placeholder="當時價格">
          <input name="behavior_tags" placeholder="行為標籤：chasing, fomo">
        </div>
        <div class="row" style="margin-top:8px">
          <select name="link">${linkOpts}</select>
          <select name="effect"><option value="">對它的影響…</option>${opts(L.effect)}</select>
        </div>
        <textarea name="content" rows="2" placeholder="發生了什麼？為什麼？" required style="margin-top:8px"></textarea>
        <div style="margin-top:6px;display:flex;justify-content:flex-end"><button class="primary">記一筆</button></div>
      </form>
      <ul class="timeline" style="margin-top:10px">${p.interactions.map((i) => `
        <li class="${i.kind}"><div class="when">${tpe(i.ts)}${i.price != null ? " · " + i.price : ""}<span class="by">${esc(i.created_by)}</span></div>
          <span class="kind">${L.kind[i.kind]}</span>${i.effect ? `<span class="pill-s ${i.effect}">${L.effect[i.effect]}</span> ` : ""}${md(i.content)}
          ${i.behavior_tags?.length ? `<div class="kv">標籤：${i.behavior_tags.map(esc).join(", ")}</div>` : ""}
          ${i.source_url ? `<div class="kv"><a href="${esc(i.source_url)}" target="_blank" rel="noopener">來源</a></div>` : ""}</li>`).join("")}</ul>
    </section>`;

  const reload = () => { openCard(p.id); loadBoard(); };
  $("#drawer-close").onclick = closeDrawer;
  $$("[data-open]").forEach((a) => a.onclick = () => openCard(a.dataset.open));
  $("#f-pct").oninput = async (e) => {
    const r = await api(`/positions/${p.id}/tier_check?new_pct=${e.target.value || 0}`);
    $("#tier-msg").innerHTML = `<div class="${r.exceeds_limit ? "warn-box" : "ok-box"}">${esc(r.message)}</div>`;
  };
  $$("#thesis-seg button").forEach((b) => b.onclick = async () => {
    await api(`/positions/${p.id}/thesis_status`, { method: "POST", body: { status: b.dataset.s } });
    toast("論點狀態已更新"); reload();
  });
  $("#btn-save").onclick = async () => {
    await api("/positions", { method: "POST", body: {
      company_id: p.company_id, stage: $("#f-stage").value, tier: $("#f-tier").value,
      position_pct: +$("#f-pct").value, thesis: $("#f-thesis").value, invalidation: $("#f-inval").value,
      target_price: $("#f-target").value === "" ? null : +$("#f-target").value,
      stop_price: $("#f-stop").value === "" ? null : +$("#f-stop").value,
      target_basis: $("#f-basis").value, model_url: $("#f-model").value,
      shares: $("#f-shares").value === "" ? null : +$("#f-shares").value,
      avg_cost: $("#f-cost").value === "" ? null : +$("#f-cost").value } });
    toast("已儲存"); reload();
  };
  $("#form-profile").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    await api(`/companies/${encodeURIComponent(p.company_id)}/briefs`, { method: "POST", body: { kind: "profile", content: f.get("content") } });
    toast("簡介已儲存"); reload();
  };
  $("#btn-exposure").onclick = async () => {
    const ex = await api("/exposure/" + encodeURIComponent(p.company_id));
    $("#exposure").innerHTML = ex.exposures.length ? `<table><tr><th>持股</th><th class="num">部位%</th><th class="num">曝險%</th><th>路徑</th></tr>
      ${ex.exposures.map((e) => `<tr><td>${esc(e.name)}</td><td class="num">${e.position_pct}</td><td class="num">${e.exposure_pct}</td><td class="kv">${e.paths.map((pp) => pp.path.join(" → ")).join("<br>")}</td></tr>`).join("")}
      <tr><td colspan="2"><b>間接合計</b></td><td class="num"><b>${ex.indirect_total_pct}</b></td><td></td></tr></table>` : '<div class="kv" style="margin-top:6px">沒有持股以這家公司為客戶。</div>';
  };
  $$("[data-done]").forEach((b) => b.onclick = async () => { await api(`/reminders/${b.dataset.done}/done`, { method: "POST" }); openCard(p.id); });
  $("#form-rem").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    await api("/reminders", { method: "POST", body: { due_date: f.get("due_date"), kind: f.get("kind"), content: f.get("content"), position_id: p.id } });
    openCard(p.id);
  };
  $("#form-int").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    const link = f.get("link"), body = {
      kind: f.get("kind"), content: f.get("content"), price: f.get("price") ? +f.get("price") : null,
      behavior_tags: f.get("behavior_tags").split(",").map((s) => s.trim()).filter(Boolean),
      effect: f.get("effect") || null };
    if (link.startsWith("d:")) body.driver_id = +link.slice(2);
    if (link.startsWith("c:")) body.checkpoint_id = +link.slice(2);
    if (link && !body.effect) { toast("請選擇這筆紀錄對關聯項目的影響"); return; }
    await api(`/positions/${p.id}/interactions`, { method: "POST", body });
    toast("已記錄"); reload();
  };

  // drivers
  const tmpl = $("#sel-template");
  if (tmpl) {
    api("/templates").then((ts) => ts.forEach((t) => tmpl.insertAdjacentHTML("beforeend", `<option value="${t.key}">${t.label}：${t.drivers.join("、")}</option>`)));
    tmpl.onchange = async () => { if (!tmpl.value) return; await api(`/positions/${p.id}/template`, { method: "POST", body: { template: tmpl.value } }); toast("模板已套用，請調整檢查點日期"); reload(); };
  }
  $("#form-driver").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    await api(`/positions/${p.id}/drivers`, { method: "POST", body: { name: f.get("name"), unit: f.get("unit") || null, sensitivity: f.get("sensitivity") || null, critical: f.get("critical") ? 1 : 0 } });
    reload();
  };
  $$("[data-dv]").forEach((b) => b.onclick = () => { const r = $(`#series-${b.dataset.dv}`); r.hidden = !r.hidden; });
  $$("[data-ddel]").forEach((b) => b.onclick = async () => { if (!confirm("刪除這個驅動因子與它的所有數值？")) return; await api(`/drivers/${b.dataset.ddel}`, { method: "DELETE" }); reload(); });
  $$(".form-dv").forEach((form) => form.onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(form);
    await api(`/drivers/${form.dataset.id}/values`, { method: "POST", body: { period: f.get("period"), kind: f.get("kind"), value: +f.get("value"), source: f.get("source") || null } });
    toast("數值已儲存"); reload();
  });
  // checkpoints
  $("#form-cp").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    await api(`/positions/${p.id}/checkpoints`, { method: "POST", body: { title: f.get("title"), expected_by: f.get("expected_by"), evidence_needed: f.get("evidence_needed") || null, driver_id: f.get("driver_id") ? +f.get("driver_id") : null } });
    reload();
  };
  const resolve = async (id, status) => { const ev = prompt(status === "confirmed" ? "看到了什麼證據？" : "為什麼判定未達成？"); if (ev === null) return; await api(`/checkpoints/${id}/resolve`, { method: "POST", body: { status, evidence: ev } }); reload(); };
  $$("[data-cok]").forEach((b) => b.onclick = () => resolve(b.dataset.cok, "confirmed"));
  $$("[data-cmiss]").forEach((b) => b.onclick = () => resolve(b.dataset.cmiss, "missed"));
  $$("[data-creopen]").forEach((b) => b.onclick = async () => { await api(`/checkpoints/${b.dataset.creopen}/resolve`, { method: "POST", body: { status: "pending" } }); reload(); });
  $$("[data-cdel]").forEach((b) => b.onclick = async () => { if (!confirm("刪除這個檢查點？")) return; await api(`/checkpoints/${b.dataset.cdel}`, { method: "DELETE" }); reload(); });
  // metrics
  $("#form-metric").onsubmit = async (e) => {
    e.preventDefault(); const f = new FormData(e.target);
    let v = +f.get("value");
    if (f.get("metric") === "gross_margin" && v > 1) v = v / 100;
    await api(`/metrics/${encodeURIComponent(p.company_id)}`, { method: "POST", body: { metric: f.get("metric"), period: f.get("period"), kind: f.get("kind"), value: v } });
    toast("指標已儲存"); reload();
  };
}

/* ---------------- new card dialog */
$("#btn-new").onclick = async () => {
  const sel = $("#form-new select[name=template]");
  if (sel.options.length === 1) (await api("/templates")).forEach((t) => sel.insertAdjacentHTML("beforeend", `<option value="${t.key}">${t.label}（${t.description}）</option>`));
  $("#dlg-new").showModal();
};
$("#form-new").onsubmit = async (e) => {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
  await api("/positions", { method: "POST", body: f });
  $("#dlg-new").close(); e.target.reset(); toast("卡片已建立"); loadBoard(); showTab("board");
};

/* ---------------- graph (layered left-to-right by supply depth) */
async function loadGraph() {
  const g = await api("/graph");
  const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
  const suppliersOf = {}, customersOf = {};
  g.edges.forEach((e) => { (suppliersOf[e.customer_id] ??= []).push(e); (customersOf[e.supplier_id] ??= []).push(e); });
  const depth = {};
  const dep = (id, seen = new Set()) => {
    if (depth[id] != null) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const ups = customersOf[id] || [];
    depth[id] = ups.length ? 1 + Math.max(...ups.map((e) => dep(e.customer_id, seen))) : 0;
    return depth[id];
  };
  const nodes = g.nodes.filter((n) => suppliersOf[n.id] || customersOf[n.id] || n.stage);
  nodes.forEach((n) => dep(n.id));
  const layers = {};
  nodes.forEach((n) => (layers[depth[n.id]] ??= []).push(n));
  const L_W = 230, ROW = 64, PAD = 40;
  const maxRows = Math.max(...Object.values(layers).map((l) => l.length));
  const W = PAD * 2 + (Object.keys(layers).length) * L_W, H = PAD * 2 + maxRows * ROW;
  const pos = {};
  Object.entries(layers).forEach(([d, list]) => {
    list.sort((a, b) => (b.position_pct || 0) - (a.position_pct || 0) || a.id.localeCompare(b.id));
    const offset = (H - list.length * ROW) / 2;
    list.forEach((n, i) => pos[n.id] = { x: PAD + d * L_W + 60, y: offset + i * ROW + ROW / 2 });
  });
  const edgeSvg = g.edges.filter((e) => pos[e.customer_id] && pos[e.supplier_id]).map((e) => {
    const a = pos[e.customer_id], b = pos[e.supplier_id], mx = (a.x + b.x) / 2;
    const w = e.revenue_share == null ? 1 : 1 + e.revenue_share * 14;
    return `<path class="edge ${e.relation}" data-c="${e.customer_id}" data-s="${e.supplier_id}" stroke-width="${w}" d="M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}"
      ${e.revenue_share == null ? 'stroke-dasharray="4 4"' : ""}><title>${esc(byId[e.customer_id].name)} → ${esc(byId[e.supplier_id].name)}（${L.relation[e.relation]}）：${pct(e.revenue_share)}，${e.lag_days ?? "?"} 天，信心${L.conf[e.confidence]}${e.source ? "，" + esc(e.source) : ""}</title></path>`;
  }).join("");
  const nodeSvg = nodes.map((n) => {
    const p = pos[n.id], r = n.stage === "hold" ? 10 + Math.sqrt(n.position_pct || 0) * 3 : 8;
    const color = n.tier ? `var(--${n.tier})` : "var(--none)";
    return `<g class="node" data-id="${n.id}" data-card="${n.stage ? 1 : 0}" transform="translate(${p.x},${p.y})">
      <circle r="${r}" fill="${color}"></circle>
      <text x="${r + 6}" y="-2">${esc(n.name)}</text>
      <text x="${r + 6}" y="12" class="sub">${esc(n.id)}${n.stage === "hold" ? " · " + n.position_pct + "%" : ""}</text></g>`;
  }).join("");
  const labels = Object.keys(layers).map((d) => `<text class="layer-label" x="${PAD + d * L_W + 60}" y="18">${d == 0 ? "終端客戶 / 夥伴" : "第 " + d + " 層供應商"}</text>`).join("");
  $("#graph").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${labels}${edgeSvg}${nodeSvg}</svg>`;
  $$("#graph .node").forEach((n) => {
    n.onclick = () => n.dataset.card === "1" ? openCard(n.dataset.id) : toast(n.dataset.id + " 還沒有卡片，用「＋ 新卡片」建立");
    n.onmouseenter = () => $$("#graph .edge").forEach((e) => { const on = e.dataset.c === n.dataset.id || e.dataset.s === n.dataset.id; e.classList.toggle("hi", on); e.classList.toggle("dim", !on); });
    n.onmouseleave = () => $$("#graph .edge").forEach((e) => e.classList.remove("hi", "dim"));
  });
  const ov = await api("/overlap");
  $("#overlap").innerHTML = `<h3>組合對終端客戶的合計曝險</h3><div class="kv" style="margin-bottom:8px">直接持有 + 沿供應鏈的間接曝險（部位% × 營收比重）。幾檔看似分散的持股，可能其實押在同一個客戶上。</div>
    <table><tr><th>客戶</th><th class="num">直接%</th><th class="num">間接%</th><th class="num">合計%</th><th>經由</th><th style="width:30%"></th></tr>
    ${ov.map((o) => `<tr><td>${esc(o.customer_name)} <span class="kv">${o.customer_id}</span></td><td class="num">${o.directly_held_pct}</td><td class="num">${o.indirect_total_pct}</td><td class="num"><b>${o.combined_pct}</b></td>
      <td class="kv">${o.via.map((v) => esc(v.name) + " " + v.exposure_pct + "%").join("、")}</td><td><div class="bar" style="width:${Math.min(100, o.combined_pct / (ov[0]?.combined_pct || 1) * 100)}%"></div></td></tr>`).join("")}</table>`;
  const o = g.nodes.map((n) => `<option value="${n.id}">${esc(n.name)} (${n.id})</option>`).join("");
  $("#sel-customer").innerHTML = o; $("#sel-supplier").innerHTML = o;
}
$("#btn-edge").onclick = () => $("#dlg-edge").showModal();
$("#form-edge").onsubmit = async (e) => {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
  await api("/edges", { method: "POST", body: { customer_id: f.customer_id, supplier_id: f.supplier_id,
    revenue_share: f.revenue_share === "" ? null : +f.revenue_share / 100, lag_days: f.lag_days === "" ? null : +f.lag_days,
    confidence: f.confidence, source: f.source || null, relation: f.relation } });
  $("#dlg-edge").close(); toast("關係已新增"); loadGraph();
};

/* ---------------- morning */
async function loadMorning() {
  const m = await api("/morning");
  const cps = m.counterparties_to_check;
  const prompt = `請用 stockcrm 的 get_morning_context 拿今天的持股、對手方清單與 attention 列表，然後：
1. 先看 attention：逾期檢查點、落後的驅動因子、偏離的指標。對每一項找最新資訊，能確認或推翻的用 propose_checkpoint_resolution / propose_driver_value 提案。
2. 查 counterparties_to_check 裡每家公司過去 24 小時的重大新聞或財報（特別是 ${cps.filter((c) => c.market === "US").map((c) => c.name).join("、")}）。
3. 對每則會影響我持股論點的消息，用 propose_interaction 提案，掛到相關的 driver_id 或 checkpoint_id，effect 填 supports / weakens / confirms，rationale 要說明它和哪個驅動因子或失效條件有關。
4. 如果某檔持股的失效條件看起來被觸發，用 propose_thesis_status 提案改成 shaken 或 broken。
5. 最後用繁體中文給我一頁早晨簡報：先講會影響論點的事，再講其他。${m.tw_monthly_revenue_window ? "\n6. 現在是台股月營收公布期間（每月 1 到 10 號），查我台股持股的最新月營收，用 propose_metric（metric=revenue_m）提案。" : ""}`;
  const attention = m.attention.length ? m.attention.map((a) => `
      <div class="att"><div><b><a data-open="${a.company_id}">${esc(a.name)}</a></b> <span class="kv">${a.company_id} · 論點${L.thesis[a.thesis_status]}${a.suggest_shaken ? " · 建議標為動搖" : ""}</span>
        <ul class="issues">${a.issues.map((i) => `<li class="${i.severity}">${esc(i.message)}</li>`).join("")}</ul></div></div>`).join("") : '<div class="ok-box">沒有需要注意的卡片。</div>';
  $("#morning").innerHTML = `
    <div class="panel"><h3>需要注意（${m.attention.length}）<span class="kv" style="margin-left:8px">逾期檢查點、落後的驅動因子、偏離模型的指標</span>
      ${m.attention.some((a) => a.suggest_shaken) ? '<button class="ghost tiny" id="btn-apply-flags" style="float:right">把建議的卡片標為動搖</button>' : ""}</h3>
      ${attention}
      ${m.upcoming_checkpoints.length ? `<div class="kv" style="margin-top:10px"><b>14 天內到期的檢查點</b>：${m.upcoming_checkpoints.map((c) => `${esc(c.company_name)}／${esc(c.title)}（${c.expected_by}）`).join("、")}</div>` : ""}
    </div>
    <div class="grid2" style="margin-top:16px">
      <div class="panel"><h3>持股狀態</h3>
        <table><tr><th>持股</th><th>分層</th><th class="num">部位%</th><th>論點</th><th class="num">未更新</th></tr>
        ${m.holdings.map((h) => `<tr><td><a data-open="${h.company_id}">${esc(h.name)}</a> <span class="kv">${h.company_id}</span></td><td><span class="badge ${h.tier}">${L.tier[h.tier]}</span></td><td class="num">${h.position_pct}</td>
          <td><i class="dot ${h.thesis_status}"></i>${L.thesis[h.thesis_status]}</td><td class="num ${h.days_since_update >= 30 ? "stale" : ""}">${h.days_since_update} 天</td></tr>`).join("")}</table>
        ${m.shaken_or_broken.length ? `<div class="warn-box" style="margin-top:10px">論點動搖或失效：${m.shaken_or_broken.map((s) => esc(s.name)).join("、")}</div>` : ""}
        ${m.stale_holdings.length ? `<div class="kv" style="margin-top:8px">超過 30 天沒更新：${m.stale_holdings.map((s) => esc(s.name) + " " + s.days + " 天").join("、")}</div>` : ""}
      </div>
      <div class="panel"><h3>今天該檢查的對手方（${cps.length}）</h3>
        <div class="kv" style="margin-bottom:6px">這些公司是你持股的客戶、供應商或夥伴。美股昨晚的消息會先反映在這裡。</div>
        ${["US", "TW"].map((mk) => `<div class="kv" style="margin-top:6px"><b>${mk}</b></div>` + cps.filter((c) => c.market === mk).map((c) => `
          <div class="rel"><span>${esc(c.name)} <span class="kv">${c.company_id}</span></span>
            <span class="kv">${c.role_to_holdings.map((r) => `${r.role === "customer" ? "是" : "供應"} ${esc(r.holding_name)} ${r.role === "customer" ? "的客戶" : ""} ${r.revenue_share != null ? pct(r.revenue_share) : ""}`).join("；")}</span></div>`).join("")).join("")}
      </div>
      <div class="panel"><h3>7 天內提醒</h3>
        ${m.due_reminders.length ? m.due_reminders.map((r) => `<div class="rel"><span>${r.due_date} · ${L.rkind[r.kind]} · ${esc(r.content)}</span><span class="kv">${esc(r.company_name || "")}</span></div>`).join("") : '<div class="kv">無</div>'}
        <div class="kv" style="margin-top:10px">${m.tw_monthly_revenue_window ? "現在是台股月營收公布期間（1 到 10 號）。" : "台股月營收公布期間是每月 1 到 10 號。"} 待審提案 ${m.pending_proposals} 件。</div>
      </div>
      <div class="panel"><h3>交給 agent 的早晨任務</h3>
        <div class="kv" style="margin-bottom:6px">排程每天會自動跑一次（scripts/morning.ps1）。要手動跑，在 Claude Code 裡貼上這段：</div>
        <pre class="prompt" id="prompt-text">${esc(prompt)}</pre>
        <button class="ghost tiny" id="btn-copy">複製</button>
      </div>
      <div class="panel" id="maint-panel"><h3>維護狀態</h3><div class="kv">載入中…</div></div>
    </div>`;
  $("#btn-copy").onclick = () => navigator.clipboard.writeText($("#prompt-text").textContent).then(() => toast("已複製"));
  loadMaintenance();
  $$("#morning [data-open]").forEach((a) => a.onclick = () => openCard(a.dataset.open));
  const af = $("#btn-apply-flags");
  if (af) af.onclick = async () => { const r = await api("/checks/apply", { method: "POST" }); toast(`已將 ${r.length} 張卡片標為動搖`); loadMorning(); loadBoard(); };
}

/* ---------------- maintenance */
const JOB_L = { backup: "備份", fetch_monthly: "月營收", fetch_quarterly: "季報", fetch_shares: "股數", quality: "品質檢查", morning: "早晨簡報", health: "健康度" };
const Q_L = { edges_without_source: "沒有來源的供應鏈關係", critical_drivers_without_model: "沒有模型值的關鍵驅動因子", holdings_without_invalidation: "沒有失效條件的持股", holdings_without_drivers: "沒有驅動因子的持股", proposals_pending_too_long: "放超過兩週沒批的提案", stale_holdings: "超過 30 天沒更新的持股", overdue_checkpoints: "逾期檢查點", tw_companies_missing_recent_revenue: "月營收不是最新的台股公司" };
async function loadMaintenance() {
  const el = $("#maint-panel");
  if (!el) return;
  const m = await api("/maintenance");
  const last = Object.entries(m.last).map(([j, r]) => `<div class="rel"><span>${JOB_L[j] || j}</span><span class="kv">${r ? `<span class="status ${r.status === "ok" ? "approved" : r.status === "error" ? "rejected" : ""}">${r.status}</span> ${tpe(r.ts)} · ${esc(r.summary)}` : "還沒跑過"}</span></div>`).join("");
  const q = Object.entries(Q_L).filter(([k]) => (m.quality[k] || []).length).map(([k, label]) => `<details><summary>${label}（${m.quality[k].length}）</summary><ul class="issues">${m.quality[k].map((it) => `<li class="low">${esc(Object.entries(it).filter(([, v]) => v != null).map(([kk, v]) => `${kk}=${v}`).join("，"))}</li>`).join("")}</ul></details>`).join("");
  el.innerHTML = `<h3>維護狀態 <span class="kv">腳本直接寫入官方事實（system:mops），其餘走提案</span></h3>
    ${last}
    <div style="margin:10px 0 6px;display:flex;gap:6px;flex-wrap:wrap">
      <button class="ghost tiny" data-job="fetch-monthly">抓月營收</button><button class="ghost tiny" data-job="fetch-quarterly">抓季報</button>
      <button class="ghost tiny" data-job="fetch-shares">抓股數</button><button class="ghost tiny" data-job="backup">備份</button><button class="ghost tiny" data-job="quality">品質檢查</button>
    </div>
    <div><b>資料品質（${m.quality.total}）</b>${q || '<div class="kv">沒有問題。</div>'}</div>`;
  $$("#maint-panel [data-job]").forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = "執行中…";
    try { const r = await api("/maintenance/run", { method: "POST", body: { job: b.dataset.job } }); toast(r.summary); } finally { loadMaintenance(); }
  });
}

/* ---------------- proposals */
$("#show-history").onchange = loadProposals;
async function loadProposals() {
  const all = $("#show-history").checked;
  const list = await api("/proposals?status=" + (all ? "all" : "pending"));
  const pending = all ? list.filter((p) => p.status === "pending").length : list.length;
  $("#proposal-count").textContent = pending || "";
  $("#proposals").innerHTML = list.length ? list.map((p) => `
    <div class="proposal">
      <div class="head"><span><span class="badge">${L.pkind[p.kind] || p.kind}</span> <span class="summary">${esc(p.summary)}</span></span>
        <span class="status ${p.status}">${esc(p.created_by)} · ${tpe(p.created_at)}${p.status !== "pending" ? " · " + (p.status === "approved" ? "已核准" : "已拒絕") : ""}</span></div>
      <div class="rationale">${md(p.rationale)}</div>
      <details><summary>內容</summary><pre>${esc(JSON.stringify(p.payload, null, 1))}</pre></details>
      ${p.status === "pending" ? `<div class="actions"><button class="primary tiny" data-ok="${p.id}">核准並套用</button><button class="ghost tiny" data-no="${p.id}">拒絕</button></div>` : ""}
    </div>`).join("") : '<div class="empty">沒有待審提案。agent 提出的新互動紀錄、供應鏈關係、驅動因子數值、檢查點結果或論點狀態變更會出現在這裡。</div>';
  $$("[data-ok]").forEach((b) => b.onclick = async () => { await api(`/proposals/${b.dataset.ok}/decide`, { method: "POST", body: { approve: true } }); toast("已套用"); loadProposals(); });
  $$("[data-no]").forEach((b) => b.onclick = async () => { await api(`/proposals/${b.dataset.no}/decide`, { method: "POST", body: { approve: false } }); loadProposals(); });
}

/* ---------------- boot */
loadBoard();
api("/proposals").then((l) => $("#proposal-count").textContent = l.length || "");
