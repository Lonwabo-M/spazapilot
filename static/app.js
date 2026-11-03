// SpazaPilot front end: vanilla JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const group = s => s.replace(/\B(?=(\d{3})+(?!\d))/g, "\u00a0");
const R = v => { const [i, d] = Math.abs(Number(v || 0)).toFixed(2).split("."); return (v < 0 ? "-R" : "R") + group(i) + "." + d; };
const R0 = v => (v < 0 ? "-R" : "R") + group(String(Math.round(Math.abs(Number(v || 0)))));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS = { paid: "Paid", awaiting_owner: "Needs you", awaiting_paypal: "Confirm in PayPal", blocked: "Blocked", rejected: "Rejected", failed: "Failed", pending: "Paying" };

let CFG = null;
let ITEMS = [];
let ENGINE = "";
let SOURCE = "text";

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `Request failed (${r.status})`);
  return body;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), 2800); }
function busy(btn, on, label) { if (on) { btn._l = btn.innerHTML; btn.innerHTML = `<span class="spin"></span>${label || ""}`; btn.disabled = true; } else { btn.innerHTML = btn._l; btn.disabled = false; } }

// ---------------------------------------------------------------- config / budget
async function loadConfig() {
  CFG = await api("/api/config");
  $("#shopName").textContent = CFG.policy.shop_name;
  const ai = CFG.ai_mode === "mock" ? `<span class="pill mock">AI: mock</span>` : `<span class="pill live">AI: ${esc(CFG.ai_mode)}</span>`;
  const pp = CFG.paypal_mode === "mock" ? `<span class="pill mock">PayPal: mock</span>` : `<span class="pill live">PayPal: ${esc(CFG.paypal_mode)}</span>`;
  $("#modes").innerHTML = ai + pp;
  renderBudget(CFG.week);
  fillSettings();
}
function renderBudget(w) {
  $("#budgetLeft").textContent = R0(w.left);
  $("#autoLimit").textContent = R0(w.auto_approve_limit);
  const pct = w.budget ? Math.min(100, 100 * w.committed / w.budget) : 0;
  $("#budgetFill").style.width = pct + "%";
  $("#budgetText").textContent = `${R0(w.committed)} of ${R0(w.budget)} weekly budget used`;
}
async function refreshBadge() {
  const a = await api("/api/approvals");
  const n = a.orders.length, b = $("#apprBadge");
  b.textContent = n; b.classList.toggle("hidden", !n);
  return a;
}

// ---------------------------------------------------------------- tabs
function show(tab) {
  $$(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".view").forEach(v => v.classList.toggle("hidden", v.id !== "view-" + tab));
  if (tab === "approvals") loadApprovals();
  if (tab === "ledger") loadLedger();
  if (tab === "settings") fillSettings();
  history.replaceState(null, "", "#" + tab);
  window.scrollTo(0, 0);
}
$$(".tabs button").forEach(b => b.onclick = () => show(b.dataset.tab));

// ---------------------------------------------------------------- restock
$$("#examples .chip").forEach(c => c.onclick = () => { $("#note").value = c.dataset.ex; $("#note").focus(); });

$("#parseBtn").onclick = async () => {
  const text = $("#note").value.trim();
  if (!text) { toast("Type what you need first."); return; }
  const btn = $("#parseBtn"); busy(btn, true, " Reading…");
  try { showParsed(await api("/api/parse", { method: "POST", body: JSON.stringify({ text }) }), "text"); }
  catch (e) { toast(e.message); } finally { busy(btn, false); }
};

$("#photo").onchange = async (ev) => {
  const f = ev.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  toast("Looking at your shelf…");
  try {
    const r = await fetch("/api/parse-photo", { method: "POST", body: fd });
    const body = await r.json(); if (!r.ok) throw new Error(body.detail);
    showParsed(body, "photo");
  } catch (e) { toast(e.message); }
  ev.target.value = "";
};

function showParsed(res, source) {
  ITEMS = res.items.map(i => ({ ...i }));
  ENGINE = res.engine; SOURCE = source;
  $("#engineTag").textContent = res.engine.startsWith("ai") ? "AI parsed" : (res.engine === "mock-vision" ? "sample photo" : "offline parser");
  $("#parseWarn").textContent = res.warning || ""; $("#parseWarn").classList.toggle("hidden", !res.warning);
  const un = res.unrecognised || [];
  $("#unrec").innerHTML = un.length ? `Not in the catalogue yet: <b>${un.map(esc).join(", ")}</b>` : "";
  $("#unrec").classList.toggle("hidden", !un.length);
  $("#parsedCard").classList.remove("hidden");
  $("#result").innerHTML = "";
  renderItems();
  $("#parsedCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderItems() {
  const ul = $("#items");
  if (!ITEMS.length) { ul.innerHTML = `<li class="empty">Nothing to order.</li>`; $("#runBtn").disabled = true; return; }
  $("#runBtn").disabled = false;
  ul.innerHTML = ITEMS.map((it, i) => `
    <li>
      <div class="nm"><b>${esc(it.name)}${it.urgent ? '<span class="urgent">out of stock</span>' : ""}</b>
        <small>${it.product_id === "airtime" ? "rand value" : esc(it.unit)} · “${esc(it.source_text)}”</small></div>
      <div class="stepper">
        <button data-i="${i}" data-d="${it.product_id === "airtime" ? -10 : -1}" aria-label="less">−</button>
        <input data-i="${i}" type="number" min="1" value="${it.qty}" inputmode="numeric" aria-label="quantity">
        <button data-i="${i}" data-d="${it.product_id === "airtime" ? 10 : 1}" aria-label="more">+</button>
      </div>
      <button class="x" data-rm="${i}" aria-label="remove">×</button>
    </li>`).join("");
  $$(".stepper button", ul).forEach(b => b.onclick = () => { const it = ITEMS[b.dataset.i]; it.qty = Math.max(1, it.qty + Number(b.dataset.d)); renderItems(); });
  $$(".stepper input", ul).forEach(inp => inp.onchange = () => { ITEMS[inp.dataset.i].qty = Math.max(1, parseInt(inp.value) || 1); renderItems(); });
  $$("[data-rm]", ul).forEach(b => b.onclick = () => { ITEMS.splice(Number(b.dataset.rm), 1); renderItems(); });
}

$("#runBtn").onclick = async () => {
  const btn = $("#runBtn"); busy(btn, true, " Comparing suppliers & paying…");
  try {
    const run = await api("/api/agent/run", { method: "POST", body: JSON.stringify({ items: ITEMS, note: $("#note").value, source: SOURCE, engine: ENGINE }) });
    renderRun(run);
    renderBudget(run.week);
    refreshBadge();
  } catch (e) { toast(e.message); } finally { busy(btn, false); }
};

function orderCard(o, withActions) {
  const lines = o.lines.map(l => `<li><span>${l.product_id === "airtime" ? `Airtime R${l.qty}` : `${l.qty} × ${esc(l.name)}`}${l.urgent ? ' <span class="urgent">urgent</span>' : ""}</span><span>${R(l.line_total)}</span></li>`).join("")
    + (o.delivery_fee ? `<li><span>Delivery</span><span>${R(o.delivery_fee)}</span></li>` : `<li><span>Delivery</span><span>free</span></li>`);
  const reasons = (o.reasons || []).map(esc).join(" ");
  let extra = "";
  if (o.receipt) extra += `<div class="receipt">${esc(o.receipt)}</div>`;
  else if (reasons) extra += `<div class="reason">${reasons}</div>`;
  if (o.error) extra += `<div class="reason">${esc(o.error)}</div>`;
  if (o.paypal_order_id) extra += `<div class="ref">PayPal order ${esc(o.paypal_order_id)}${o.paypal_capture_id ? " · capture " + esc(o.paypal_capture_id) : ""} · ${esc(o.currency)} ${Number(o.total_usd).toFixed(2)} → ${esc(o.payee_email)}</div>`;
  let actions = "";
  if (withActions && o.status === "awaiting_owner") actions = `<div class="actions"><button class="btn ghost" data-reject="${o.id}">Not now</button><button class="btn gold" data-approve="${o.id}">Approve &amp; pay ${R0(o.total_zar)}</button></div>`;
  if (o.status === "awaiting_paypal" && o.approve_url) actions = `<div class="actions"><a class="btn gold" href="${esc(o.approve_url)}">Confirm ${R0(o.total_zar)} in PayPal</a></div>`;
  if (withActions && o.status === "blocked") actions = `<div class="actions"><button class="btn ghost" data-reject="${o.id}">Dismiss</button><button class="btn ghost" onclick="show('settings')">Change guardrails</button></div>`;
  if (withActions && o.status === "failed") actions = `<div class="actions"><button class="btn ghost" data-reject="${o.id}">Cancel</button><button class="btn gold" data-approve="${o.id}">Retry payment</button></div>`;
  return `<div class="card order" id="order-${o.id}">
    <div class="head">
      <div><div class="sup">${esc(o.supplier_name)}</div><div class="meta">Arrives ${esc(o.delivery_label || o.delivery_date)} · <span class="pill ${o.status}">${STATUS[o.status] || o.status}</span></div></div>
      <div><div class="amt">${R(o.total_zar)}</div><div class="usd">${esc(o.currency)} ${Number(o.total_usd).toFixed(2)}</div></div>
    </div>
    <ul>${lines}</ul>${extra}${actions}</div>`;
}

function wireActions(root, after) {
  $$("[data-approve]", root).forEach(b => b.onclick = async () => {
    busy(b, true, " Paying…");
    try { const o = await api(`/api/orders/${b.dataset.approve}/approve`, { method: "POST" }); toast(o.status === "paid" ? "Paid ✓ receipt saved" : `Order is ${STATUS[o.status] || o.status}`); }
    catch (e) { toast(e.message); }
    await after();
  });
  $$("[data-reject]", root).forEach(b => b.onclick = async () => {
    try { await api(`/api/orders/${b.dataset.reject}/reject`, { method: "POST" }); toast("Okay, not ordered."); } catch (e) { toast(e.message); }
    await after();
  });
}

function renderRun(run) {
  const paid = run.orders.filter(o => o.status === "paid");
  const waiting = run.orders.filter(o => o.status === "awaiting_owner" || o.status === "awaiting_paypal");
  const blocked = run.orders.filter(o => o.status === "blocked" || o.status === "failed");
  let summary = `${paid.length} paid`;
  if (waiting.length) summary += ` · ${waiting.length} waiting for you`;
  if (blocked.length) summary += ` · ${blocked.length} blocked`;
  const sav = run.savings > 0 ? `<div class="savings"><div class="big">Saved ${R(run.savings)}</div><p>Best split costs ${R(run.optimal_total)} vs ${R(run.benchmark_total)} for ${esc(run.benchmark_label || "one supplier")}.</p></div>` : "";
  const unavailable = run.unavailable.length ? `<div class="reason">No supplier stocks: ${run.unavailable.map(u => esc(u.name)).join(", ")}</div>` : "";
  const trace = run.events.map(e => `<li class="k-${e.kind}"><span class="k">${esc(e.kind)}</span>${esc(e.message)}</li>`).join("");
  const box = $("#result");
  box.innerHTML = `${sav}<h2 class="pad">${summary}</h2>${unavailable}${run.orders.map(o => orderCard(o, true)).join("")}
    <details class="trace" open><summary>What the agent did</summary><ol>${trace}</ol></details>`;
  wireActions(box, async () => { renderRun(await api(`/api/runs/${run.run_id}`)); renderBudget((await api("/api/config")).week); refreshBadge(); });
  $("#parsedCard").classList.add("hidden");
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------------------------------------------------------------- approvals
async function loadApprovals() {
  const a = await refreshBadge();
  const el = $("#approvalList");
  el.innerHTML = a.orders.length ? a.orders.map(o => orderCard(o, true)).join("") : `<div class="card empty">Nothing waiting. The agent paid everything inside your limits.</div>`;
  wireActions(el, async () => { await loadApprovals(); renderBudget((await api("/api/config")).week); });
}

// ---------------------------------------------------------------- ledger
async function loadLedger() {
  const [l, ins] = await Promise.all([api("/api/ledger"), api("/api/insights")]);
  $("#insights").innerHTML = ins.insights.map(t => `<li>${esc(t)}</li>`).join("");
  $("#insightEngine").textContent = ins.engine.startsWith("ai") ? "AI insight" : "summary";
  $("#ledgerList").innerHTML = l.orders.length ? l.orders.map(o => orderCard(o, false)).join("") : `<div class="card empty">No agent transactions yet.</div>`;
  renderBudget(l.week);
}

// ---------------------------------------------------------------- settings
function fillSettings() {
  if (!CFG) return;
  const p = CFG.policy;
  $("#sShop").value = p.shop_name; $("#sBudget").value = p.weekly_budget; $("#sLimit").value = p.auto_approve_limit;
  $("#sQty").value = p.max_qty_per_line; $("#sUrgent").value = p.urgent_max_days; $("#sEnabled").checked = !!p.agent_enabled;
  $("#walletText").innerHTML = CFG.paypal_mode === "mock"
    ? "Mock mode: payments are simulated with PayPal-shaped responses. Add sandbox keys to pay for real in the PayPal sandbox."
    : (CFG.wallet_linked ? "Wallet linked. The agent can pay suppliers inside your guardrails without asking each time."
      : "No wallet linked yet. Each payment opens PayPal for a one-tap confirmation. Link once to let the agent autopay small orders.");
  $("#unlinkBtn").classList.toggle("hidden", CFG.paypal_mode === "mock" || !CFG.wallet_linked);
  $("#linkBtn").textContent = CFG.paypal_mode === "mock" ? "Try wallet linking (simulated)" : (CFG.wallet_linked ? "Re-link PayPal wallet" : "Link PayPal wallet");
  const dlabel = iso => new Date(iso + "T12:00:00").toLocaleDateString("en-ZA", { weekday: "short", day: "numeric", month: "short" });
  $("#supplierList").innerHTML = CFG.suppliers.map(s => `<li><b>${esc(s.name)}</b><small>${esc(s.area)} · min ${R0(s.min_order)} · delivery ${s.delivery_fee ? R0(s.delivery_fee) : "free"} · next delivery ${esc(dlabel(s.next_delivery))}</small></li>`).join("");
}
$("#saveBtn").onclick = async () => {
  try {
    await api("/api/policy", { method: "PUT", body: JSON.stringify({
      shop_name: $("#sShop").value.trim() || "My spaza", weekly_budget: Number($("#sBudget").value), auto_approve_limit: Number($("#sLimit").value),
      max_qty_per_line: Number($("#sQty").value), urgent_max_days: Number($("#sUrgent").value), agent_enabled: $("#sEnabled").checked }) });
    await loadConfig(); toast("Guardrails saved");
  } catch (e) { toast(e.message); }
};
$("#linkBtn").onclick = async () => {
  try { const r = await api("/api/paypal/link-wallet", { method: "POST" }); if (r.approve_url) location.href = r.approve_url; } catch (e) { toast(e.message); }
};
$("#unlinkBtn").onclick = async () => { await api("/api/paypal/unlink-wallet", { method: "POST" }); await loadConfig(); toast("Wallet unlinked"); };
$("#resetBtn").onclick = async () => {
  if (!confirm("Clear all demo orders and settings?")) return;
  try { await api("/api/demo/reset", { method: "POST" }); await loadConfig(); refreshBadge(); $("#result").innerHTML = ""; toast("Demo reset"); } catch (e) { toast(e.message); }
};

// ---------------------------------------------------------------- boot
(async function boot() {
  await loadConfig();
  await refreshBadge();
  const params = new URLSearchParams(location.search);
  if (params.get("paid")) toast("Paid in PayPal ✓");
  if (params.get("wallet")) toast("PayPal wallet linked ✓");
  const tab = (location.hash || "#restock").slice(1);
  show(["restock", "approvals", "ledger", "settings"].includes(tab) ? tab : "restock");
})();
