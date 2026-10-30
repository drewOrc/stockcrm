/* Stock CRM front-end. Vanilla JS, talks to /api. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const L = {
  stage: { watch: "觀察", hold: "持有", exited: "已出場" },
  tier: { core: "核心", satellite: "衛星", watch: "觀察", blacklist: "黑名單" },
  thesis: { intact: "成立", shaken: "動搖", broken: "失效" },
  kind: { buy: "買進", sell: "賣出", add: "加碼", trim: "減碼", news: "新聞", earnings: "財報", call: "法說", thought: "想法", review: "回顧" },
  rkind: { price: "到價", earnings: "財報", revenue: "營收", review: "回顧", other: "其他" },
  conf: { high: "高", medium: "中", low: "低" },
  pkind: { interaction: "互動紀錄", edge: "供應鏈關係", thesis_status: "論點狀態", reminder: "提醒" },
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => v == null ? "?" : Math.round(v * 100) + "%";
const tpe = (ts) => ts ? new Date(ts.replace(" ", "T") + "Z").toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");

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
      <span class="badge ${p.tier}">${L.tier[p.tier]}</span></div>
    <div class="meta">
      <span><i class="dot ${p.thesis_status}"></i>論點${L.thesis[p.thesis_status]}</span>
      ${p.stage === "hold" ? `<span>部位 ${p.position_pct}%</span>` : ""}
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
function renderCard(p) {
  const c = p.concentration;
  const concBadge = c.largest_customer ? `<span class="badge ${{ high: "hi", medium: "mid", low: "lo" }[c.flag]}">最大客戶 ${esc(c.largest_customer_name)} ${pct(c.largest_customer_share)}</span>` : '<span class="kv">尚無客戶資料</span>';
  const rel = (list, key, nameKey, dir) => list.length ? list.map((e) => `
      <div class="rel"><span>${dir} <a data-open="${e[key]}">${esc(e[nameKey])}</a> <span class="kv">${e[key]}</span></span>
        <span class="kv">${pct(e.revenue_share)} · ${e.lag_days ?? "?"} 天 · 信心${L.conf[e.confidence]}</span></div>`).join("") : '<div class="kv">無</div>';
  $("#drawer-content").innerHTML = `
    <button class="close" id="drawer-close">×</button>
    <h2>${esc(p.company.name)} <span class="ticker">${esc(p.company_id)}</span> <span class="badge ${p.tier}">${L.tier[p.tier]}</span></h2>
    <div class="kv">${esc(p.company.sector || "")} · 想法來源：${esc(p.idea_source || "未填")} · ${p.days_since_update ?? "?"} 天未更新${p.tags?.length ? " · " + p.tags.map(esc).join(", ") : ""}</div>

    <section><div class="row3">
      <label>階段<select id="f-stage">${Object.entries(L.stage).map(([k, v]) => `<option value="${k}" ${k === p.stage ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>分層<select id="f-tier">${Object.entries(L.tier).map(([k, v]) => `<option value="${k}" ${k === p.tier ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>部位 %<input id="f-pct" type="number" step="0.5" min="0" value="${p.position_pct}"></label>
    </div>
    <div id="tier-msg"></div>
    <div class="row">
      <label>目標價<input id="f-target" type="number" step="any" value="${p.target_price ?? ""}"></label>
      <label>停損價<input id="f-stop" type="number" step="any" value="${p.stop_price ?? ""}"></label>
    </div></section>

    <section><h4>投資論點 <span class="seg" id="thesis-seg">${Object.entries(L.thesis).map(([k, v]) => `<button data-s="${k}" class="${k === p.thesis_status ? "on" : ""}">${v}</button>`).join("")}</span></h4>
      <textarea id="f-thesis" rows="3">${esc(p.thesis || "")}</textarea></section>
    <section><h4>失效條件</h4><textarea id="f-inval" rows="2">${esc(p.invalidation || "")}</textarea>
      <div style="margin-top:8px;display:flex;gap:8px;justify-content:flex-end"><button class="primary" id="btn-save">儲存卡片</button></div></section>

    <section><h4>供應鏈關係</h4>
      <div style="margin-bottom:6px">${concBadge}</div>
      ${rel(p.customers, "customer_id", "customer_name", "客戶")}
      ${rel(p.suppliers, "supplier_id", "supplier_name", "供應商")}
      <div style="margin-top:8px"><button class="ghost tiny" id="btn-exposure">看組合對 ${esc(p.company.name)} 的間接曝險</button><div id="exposure"></div></div>
    </section>

    <section><h4>提醒</h4>
      ${p.reminders.map((r) => `<div class="rel"><span>${r.due_date} · ${L.rkind[r.kind]} · ${esc(r.content)}</span><button class="ghost tiny" data-done="${r.id}">完成</button></div>`).join("") || '<div class="kv">無</div>'}
      <form id="form-rem" class="row3" style="margin-top:8px">
        <input name="due_date" type="date" required><select name="kind">${Object.entries(L.rkind).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
        <input name="content" placeholder="提醒內容" required><button class="ghost tiny" style="grid-column:1/-1">＋ 新增提醒</button>
      </form></section>

    <section><h4>互動紀錄（${p.interactions.length}）</h4>
      <form id="form-int">
        <div class="row3">
          <select name="kind">${Object.entries(L.kind).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
          <input name="price" type="number" step="any" placeholder="當時價格">
          <input name="behavior_tags" placeholder="行為標籤：chasing, fomo">
        </div>
        <textarea name="content" rows="2" placeholder="發生了什麼？為什麼？" required style="margin-top:8px"></textarea>
        <div style="margin-top:6px;display:flex;justify-content:flex-end"><button class="primary">記一筆</button></div>
      </form>
      <ul class="timeline" style="margin-top:10px">${p.interactions.map((i) => `
        <li class="${i.kind}"><div class="when">${tpe(i.ts)}${i.price != null ? " · " + i.price : ""}<span class="by">${esc(i.created_by)}</span></div>
          <span class="kind">${L.kind[i.kind]}</span>${md(i.content)}
          ${i.behavior_tags?.length ? `<div class="kv">標籤：${i.behavior_tags.map(esc).join(", ")}</div>` : ""}
          ${i.source_url ? `<div class="kv"><a href="${esc(i.source_url)}" target="_blank" rel="noopener">來源</a></div>` : ""}</li>`).join("")}</ul>
    </section>`;

  $("#drawer-close").onclick = closeDrawer;
  $$("[data-open]").forEach((a) => a.onclick = () => openCard(a.dataset.open));
  $("#f-pct").oninput = async (e) => {
    const r = await api(`/positions/${p.id}/tier_check?new_pct=${e.target.value || 0}`);
    $("#tier-msg").innerHTML = `<div class="${r.exceeds_limit ? "warn-box" : "ok-box"}">${esc(r.message)}</div>`;
  };
  $$("#thesis-seg button").forEach((b) => b.onclick = async () => {
    await api(`/positions/${p.id}/thesis_status`, { method: "POST", body: { status: b.dataset.s } });
    toast("論點狀態已更新"); openCard(p.id); loadBoard();
  });
  $("#btn-save").onclick = async () => {
    await api("/positions", { method: "POST", body: {
      company_id: p.company_id, stage: $("#f-stage").value, tier: $("#f-tier").value,
      position_pct: +$("#f-pct").value, thesis: $("#f-thesis").value, invalidation: $("#f-inval").value,
      target_price: $("#f-target").value === "" ? null : +$("#f-target").value,
      stop_price: $("#f-stop").value === "" ? null : +$("#f-stop").value } });
    toast("已儲存"); openCard(p.id); loadBoard();
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
    await api(`/positions/${p.id}/interactions`, { method: "POST", body: {
      kind: f.get("kind"), content: f.get("content"), price: f.get("price") ? +f.get("price") : null,
      behavior_tags: f.get("behavior_tags").split(",").map((s) => s.trim()).filter(Boolean) } });
    toast("已記錄"); openCard(p.id); loadBoard();
  };
}

/* ---------------- new card dialog */
$("#btn-new").onclick = () => $("#dlg-new").showModal();
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
  // depth = longest customer chain above the node
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
    return `<path class="edge" data-c="${e.customer_id}" data-s="${e.supplier_id}" stroke-width="${w}" d="M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}"
      ${e.revenue_share == null ? 'stroke-dasharray="4 4"' : ""}><title>${esc(byId[e.customer_id].name)} → ${esc(byId[e.supplier_id].name)}：${pct(e.revenue_share)}，${e.lag_days ?? "?"} 天，信心${L.conf[e.confidence]}${e.source ? "，" + esc(e.source) : ""}</title></path>`;
  }).join("");
  const nodeSvg = nodes.map((n) => {
    const p = pos[n.id], r = n.stage === "hold" ? 10 + Math.sqrt(n.position_pct || 0) * 3 : 8;
    const color = n.tier ? `var(--${n.tier})` : "var(--none)";
    return `<g class="node" data-id="${n.id}" data-card="${n.stage ? 1 : 0}" transform="translate(${p.x},${p.y})">
      <circle r="${r}" fill="${color}"></circle>
      <text x="${r + 6}" y="-2">${esc(n.name)}</text>
      <text x="${r + 6}" y="12" class="sub">${esc(n.id)}${n.stage === "hold" ? " · " + n.position_pct + "%" : ""}</text></g>`;
  }).join("");
  const labels = Object.keys(layers).map((d) => `<text class="layer-label" x="${PAD + d * L_W + 60}" y="18">${d == 0 ? "終端客戶" : "第 " + d + " 層供應商"}</text>`).join("");
  $("#graph").innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${labels}${edgeSvg}${nodeSvg}</svg>`;
  $$("#graph .node").forEach((n) => {
    n.onclick = () => n.dataset.card === "1" ? openCard(n.dataset.id) : toast(n.dataset.id + " 還沒有卡片，用「＋ 新卡片」建立");
    n.onmouseenter = () => $$("#graph .edge").forEach((e) => e.classList.toggle("hi", e.dataset.c === n.dataset.id || e.dataset.s === n.dataset.id) || e.classList.toggle("dim", !(e.dataset.c === n.dataset.id || e.dataset.s === n.dataset.id)));
    n.onmouseleave = () => $$("#graph .edge").forEach((e) => e.classList.remove("hi", "dim"));
  });
  const ov = await api("/overlap");
  $("#overlap").innerHTML = `<h3>組合對終端客戶的合計曝險</h3><div class="kv" style="margin-bottom:8px">直接持有 + 沿供應鏈的間接曝險（部位% × 營收比重）。幾檔看似分散的持股，可能其實押在同一個客戶上。</div>
    <table><tr><th>客戶</th><th class="num">直接%</th><th class="num">間接%</th><th class="num">合計%</th><th>經由</th><th style="width:30%"></th></tr>
    ${ov.map((o) => `<tr><td>${esc(o.customer_name)} <span class="kv">${o.customer_id}</span></td><td class="num">${o.directly_held_pct}</td><td class="num">${o.indirect_total_pct}</td><td class="num"><b>${o.combined_pct}</b></td>
      <td class="kv">${o.via.map((v) => esc(v.name) + " " + v.exposure_pct + "%").join("、")}</td><td><div class="bar" style="width:${Math.min(100, o.combined_pct / (ov[0]?.combined_pct || 1) * 100)}%"></div></td></tr>`).join("")}</table>`;
  const opts = g.nodes.map((n) => `<option value="${n.id}">${esc(n.name)} (${n.id})</option>`).join("");
  $("#sel-customer").innerHTML = opts; $("#sel-supplier").innerHTML = opts;
}
$("#btn-edge").onclick = () => $("#dlg-edge").showModal();
$("#form-edge").onsubmit = async (e) => {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
  await api("/edges", { method: "POST", body: { customer_id: f.customer_id, supplier_id: f.supplier_id,
    revenue_share: f.revenue_share === "" ? null : +f.revenue_share / 100, lag_days: f.lag_days === "" ? null : +f.lag_days,
    confidence: f.confidence, source: f.source || null } });
  $("#dlg-edge").close(); toast("關係已新增"); loadGraph();
};

/* ---------------- morning */
async function loadMorning() {
  const m = await api("/morning");
  const cps = m.counterparties_to_check;
  const prompt = `請用 stockcrm 的 get_morning_context 拿今天的持股與對手方清單，然後：
1. 查 counterparties_to_check 裡每家公司過去 24 小時的重大新聞或財報（特別是 ${cps.filter((c) => c.market === "US").map((c) => c.name).join("、")}）。
2. 對每則會影響我持股論點的消息，用 propose_interaction 提案，rationale 要說明它和哪檔持股的 invalidation 條件有關。
3. 如果某檔持股的失效條件看起來被觸發，用 propose_thesis_status 提案改成 shaken 或 broken。
4. 最後用繁體中文給我一頁早晨簡報：先講會影響論點的事，再講其他。${m.tw_monthly_revenue_window ? "\n5. 現在是台股月營收公布期間（每月 1 到 10 號），順便查我持股的最新月營收。" : ""}`;
  $("#morning").innerHTML = `
    <div class="grid2">
      <div class="panel"><h3>持股狀態</h3>
        <table><tr><th>持股</th><th>分層</th><th class="num">部位%</th><th>論點</th><th class="num">未更新</th></tr>
        ${m.holdings.map((h) => `<tr><td>${esc(h.name)} <span class="kv">${h.company_id}</span></td><td><span class="badge ${h.tier}">${L.tier[h.tier]}</span></td><td class="num">${h.position_pct}</td>
          <td><i class="dot ${h.thesis_status}"></i>${L.thesis[h.thesis_status]}</td><td class="num ${h.days_since_update >= 30 ? "stale" : ""}">${h.days_since_update} 天</td></tr>`).join("")}</table>
        ${m.shaken_or_broken.length ? `<div class="warn-box" style="margin-top:10px">論點動搖或失效：${m.shaken_or_broken.map((s) => esc(s.name)).join("、")}</div>` : ""}
        ${m.stale_holdings.length ? `<div class="kv" style="margin-top:8px">超過 30 天沒更新：${m.stale_holdings.map((s) => esc(s.name) + " " + s.days + " 天").join("、")}</div>` : ""}
      </div>
      <div class="panel"><h3>今天該檢查的對手方（${cps.length}）</h3>
        <div class="kv" style="margin-bottom:6px">這些公司是你持股的客戶或供應商。美股昨晚的消息會先反映在這裡。</div>
        ${["US", "TW"].map((mk) => `<div class="kv" style="margin-top:6px"><b>${mk}</b></div>` + cps.filter((c) => c.market === mk).map((c) => `
          <div class="rel"><span>${esc(c.name)} <span class="kv">${c.company_id}</span></span>
            <span class="kv">${c.role_to_holdings.map((r) => `${r.role === "customer" ? "是" : "供應"} ${esc(r.holding_name)} ${r.role === "customer" ? "的客戶" : ""} ${r.revenue_share != null ? pct(r.revenue_share) : ""}`).join("；")}</span></div>`).join("")).join("")}
      </div>
      <div class="panel"><h3>7 天內提醒</h3>
        ${m.due_reminders.length ? m.due_reminders.map((r) => `<div class="rel"><span>${r.due_date} · ${L.rkind[r.kind]} · ${esc(r.content)}</span><span class="kv">${esc(r.company_name || "")}</span></div>`).join("") : '<div class="kv">無</div>'}
        <div class="kv" style="margin-top:10px">${m.tw_monthly_revenue_window ? "現在是台股月營收公布期間（1 到 10 號）。" : "台股月營收公布期間是每月 1 到 10 號。"} 待審提案 ${m.pending_proposals} 件。</div>
      </div>
      <div class="panel"><h3>交給 agent 的早晨任務</h3>
        <div class="kv" style="margin-bottom:6px">在 Claude Code 裡貼上這段，它會透過 MCP 讀取同一份資料並提案：</div>
        <pre class="prompt" id="prompt-text">${esc(prompt)}</pre>
        <button class="ghost tiny" id="btn-copy">複製</button>
      </div>
    </div>`;
  $("#btn-copy").onclick = () => navigator.clipboard.writeText($("#prompt-text").textContent).then(() => toast("已複製"));
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
      <div class="head"><span><span class="badge">${L.pkind[p.kind]}</span> <span class="summary">${esc(p.summary)}</span></span>
        <span class="status ${p.status}">${esc(p.created_by)} · ${tpe(p.created_at)}${p.status !== "pending" ? " · " + (p.status === "approved" ? "已核准" : "已拒絕") : ""}</span></div>
      <div class="rationale">${md(p.rationale)}</div>
      <details><summary>內容</summary><pre>${esc(JSON.stringify(p.payload, null, 1))}</pre></details>
      ${p.status === "pending" ? `<div class="actions"><button class="primary tiny" data-ok="${p.id}">核准並套用</button><button class="ghost tiny" data-no="${p.id}">拒絕</button></div>` : ""}
    </div>`).join("") : '<div class="empty">沒有待審提案。agent 提出的新互動紀錄、供應鏈關係或論點狀態變更會出現在這裡。</div>';
  $$("[data-ok]").forEach((b) => b.onclick = async () => { await api(`/proposals/${b.dataset.ok}/decide`, { method: "POST", body: { approve: true } }); toast("已套用"); loadProposals(); });
  $$("[data-no]").forEach((b) => b.onclick = async () => { await api(`/proposals/${b.dataset.no}/decide`, { method: "POST", body: { approve: false } }); loadProposals(); });
}

/* ---------------- boot */
loadBoard();
api("/proposals").then((l) => $("#proposal-count").textContent = l.length || "");
