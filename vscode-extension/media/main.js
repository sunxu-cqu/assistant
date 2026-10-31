/* 简历助手 · Webview 逻辑
 *
 * 只做两件事：把工具调用的结果画出来、把点击变成工具调用。
 * 这里没有一行业务判断 —— 状态机允许推进到哪一步，是服务端算好给我们的。
 */

const vscode = acquireVsCodeApi();
const MODE = document.body.dataset.mode || "panel";

/* ---------------------------------------------------------------- 通信 */

let seq = 0;
const pending = new Map();

function rpc(tool, args) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    vscode.postMessage({ type: "call", id, tool, args: args || {} });
  });
}

window.addEventListener("message", (e) => {
  const m = e.data || {};
  if (m.type === "result") {
    const p = pending.get(m.id);
    if (!p) return;
    pending.delete(m.id);
    if (m.ok) p.resolve(m.data);
    else p.reject(new Error(m.error || "调用失败"));
    return;
  }
  if (m.type === "refresh") {
    refresh().catch(() => {});
  }
});

/* ---------------------------------------------------------------- 工具函数 */

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[c]));
// 内联 onclick 里的字符串参数：先 JSON.stringify 再转义。
// 直接拼 '${path}' 会把 Windows 路径里的 \r \n 当转义字符，预览就废了
const arg = (v) => esc(JSON.stringify(String(v ?? "")));

const state = { data: null, view: "board", busy: false, lastMatch: null };

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toast").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = ".3s"; }, 2400);
  setTimeout(() => el.remove(), 2800);
}

async function act(fn, okMsg) {
  if (state.busy) return null;
  state.busy = true;
  document.body.style.cursor = "progress";
  try {
    const r = await fn();
    if (okMsg) toast(okMsg, "ok");
    await refresh();
    return r;
  } catch (e) {
    toast(e.message || "操作失败", "err");
    return null;
  } finally {
    state.busy = false;
    document.body.style.cursor = "";
  }
}

async function refresh() {
  const data = await rpc("ui_overview", {});
  state.data = data;
  render();
}

/* ---------------------------------------------------------------- 渲染入口 */

const TABS = [
  ["board", "看板"],
  ["jobs", "岗位池"],
  ["kb", "知识库"],
  ["system", "自检"],
];

function render() {
  const d = state.data;
  if (!d) return;
  $("#app").innerHTML = MODE === "sidebar" ? renderSidebar(d) : renderPanel(d);
  if (MODE === "panel" && state.view === "kb") loadReview();
}

/* ---------------------------------------------------------------- 侧栏 */

function renderSidebar(d) {
  const items = (d.board && d.board.items) || [];
  const p = d.profile || {};

  const hot = items.filter((i) => (i.alerts || []).some((a) => a.level === "error"));
  const todo = items.filter((i) => ["draft", "matched", "composed"].includes(i.status));
  const moving = items.filter(
    (i) => ["sent", "screening", "interview", "replied", "meeting"].includes(i.status)
      && !hot.includes(i)
  );

  const item = (it) => {
    const worst = (it.alerts || []).find((a) => a.level === "error")
      ? "alert-err"
      : ((it.alerts || []).some((a) => a.level === "warn") ? "alert-warn" : "");
    const tags = [];
    if (it.kind === "baoyan") tags.push(`<span class="chip brand">保研</span>`);
    if (it.days_left !== null && it.days_left !== undefined && it.days_left >= 0) {
      tags.push(`<span class="chip ${it.days_left <= 3 ? "err" : ""}">${it.days_left} 天截止</span>`);
    }
    if (it.days_in_status >= 7 && ["sent", "screening"].includes(it.status)) {
      tags.push(`<span class="chip warn">${it.days_in_status} 天没动静</span>`);
    }
    if (it.pdf) tags.push(`<span class="chip ok">PDF</span>`);

    const alert = (it.alerts || []).find((a) => a.level === "error" || a.level === "warn");
    return `<div class="side-item ${worst}" onclick="quickGen(${arg(it.job_id)})" title="点击生成简历">
      <div class="t">${esc(it.title)}</div>
      <div class="s">${esc(it.org || it.mentor || "—")}</div>
      ${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}
      ${alert ? `<div class="s" style="margin-top:4px">${esc(alert.text)}</div>` : ""}
      <div class="n">下一步 · <b>${esc(it.next || "—")}</b></div>
    </div>`;
  };

  const group = (title, list) => (list.length
    ? `<div class="side-group">${title} · ${list.length}</div>${list.map(item).join("")}`
    : "");

  const empty = !items.length
    ? `<div class="empty" style="margin:10px">还没有投递。打开看板 → 岗位池 → 录入岗位。</div>`
    : "";

  const example = p.example
    ? `<div class="warnline">⚠ 示例档案（${esc(p.name)}），改完 profile.yaml 再生成</div>`
    : "";

  return `<div class="side-head">
      <div class="who">${esc(p.ok ? (p.name || "未填姓名") : "档案未就绪")}</div>
      <div class="where">${esc([p.school, p.major].filter(Boolean).join(" · ") || "data/profile.yaml")}</div>
      ${example}
    </div>
    <div class="side-list">
      ${empty}
      ${group("必须处理", hot)}
      ${group("待办", todo)}
      ${group("等回复", moving)}
    </div>
    <div class="side-foot">
      <button class="btn btn-primary" onclick="openBoard()">打开看板</button>
      <button class="btn btn-ghost" onclick="act(refresh)">刷新</button>
    </div>`;
}

/* ---------------------------------------------------------------- 面板 */

function renderPanel(d) {
  const p = d.profile || {};
  const tabs = TABS.map(([k, label]) =>
    `<button class="tab ${state.view === k ? "active" : ""}" onclick="setView(${arg(k)})">${label}</button>`
  ).join("");

  const actions = [];
  if (state.view === "jobs") actions.push(`<button class="btn btn-primary" onclick="openNewJob()">＋ 录入岗位</button>`);
  if (state.view === "kb") {
    actions.push(`<button class="btn btn-ghost" onclick="buildKb(false)">增量抽取</button>`);
    actions.push(`<button class="btn btn-primary" onclick="buildKb(true)">全量重抽</button>`);
  }
  if (state.view === "board") actions.push(`<button class="btn btn-ghost" onclick="openBoardHtml()">导出看板页</button>`);
  actions.push(`<button class="btn btn-ghost" onclick="act(refresh)">刷新</button>`);

  const body = state.view === "board" ? viewBoard(d)
    : state.view === "jobs" ? viewJobs(d)
    : state.view === "kb" ? viewKb(d)
    : viewSystem(d);

  return `<div class="topbar">
      <span class="brand">简历助手</span>
      <div class="tabs">${tabs}</div>
      <div class="spacer"></div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">${actions.join("")}</div>
    </div>
    <div class="content">${body}</div>`;
}

function setView(v) {
  state.view = v;
  render();
}

/* ---------------------------------------------------------------- 看板 */

function viewBoard(d) {
  const f = d.board.funnel || {};
  const stats = [`<div class="stat"><b>${d.board.total}</b><span>投递记录</span></div>`];
  for (const k of Object.keys(f)) {
    const x = f[k];
    stats.push(`<div class="stat"><b>${x.sent}</b><span>${esc(x.label)}已投出</span></div>`);
    if (x.sent) stats.push(`<div class="stat"><b>${x.reply_rate}</b><span>${esc(x.label)}回复率</span></div>`);
  }
  stats.push(`<div class="stat"><b>${d.kb.evidence_count}</b><span>证据条目</span></div>`);
  const hot = d.board.items.filter((it) => (it.alerts || []).some((a) => a.level === "error")).length;
  if (hot) stats.push(`<div class="stat hot"><b>${hot}</b><span>必须处理</span></div>`);

  const cols = d.board.columns.map((col) => {
    const cards = col.items.length
      ? col.items.map(cardHtml).join("")
      : `<div class="empty">这一栏是空的</div>`;
    return `<div class="col">
      <div class="col-head"><h3>${esc(col.title)}</h3><em>${col.count}</em></div>
      <div class="col-body">${cards}</div>
    </div>`;
  }).join("");

  const example = d.profile.example
    ? `<div class="banner warn"><h3>现在用的是示例档案</h3>
       <p>profile.yaml 里还是「${esc(d.profile.name)}」·${esc(d.profile.school || "")}，改完再生成简历</p></div>`
    : "";

  const guide = !d.board.total
    ? `<div class="banner"><h3>还没有投递记录</h3>
       <p>三步跑起来：资料放 workspace/projects/ → 知识库页全量重抽 → 岗位池录入岗位</p></div>`
    : "";

  return `<div class="stats">${stats.join("")}</div>${example}${guide}
    <div class="board">${cols}</div>`;
}

function cardHtml(it) {
  const alerts = (it.alerts || []).map((a) => `<div class="alert ${a.level}">${esc(a.text)}</div>`).join("");
  const chips = [];
  chips.push(it.kind === "baoyan" ? `<span class="chip brand">保研</span>` : `<span class="chip">实习</span>`);
  if (it.days_in_status) chips.push(`<span class="chip">${it.days_in_status} 天未动</span>`);
  if (it.days_left !== null && it.days_left !== undefined && it.days_left >= 0) {
    chips.push(`<span class="chip ${it.days_left <= 3 ? "err" : ""}">${it.days_left} 天截止</span>`);
  }
  if (it.pdf) chips.push(`<span class="chip ok">PDF 就绪</span>`);

  const canGen = ["draft", "matched"].includes(it.status);
  const canAdvance = (it.allowed || []).length > 0;

  return `<div class="card">
    <div class="card-top">
      <span class="dot ${esc(it.status)}"></span>
      <div style="min-width:0">
        <h4>${esc(it.title)}</h4>
        <div class="org">${esc(it.org || it.mentor || "—")}</div>
      </div>
    </div>
    <div class="meta">${chips.join("")}</div>
    ${alerts ? `<div class="alerts">${alerts}</div>` : ""}
    <div class="next">下一步 · <b>${esc(it.next || "—")}</b></div>
    <div class="card-actions">
      ${canGen ? `<button class="btn btn-ghost btn-tiny" onclick="doGen(${arg(it.job_id)})">生成简历</button>` : ""}
      ${canAdvance ? `<button class="btn btn-ghost btn-tiny" onclick="openAdvance(${arg(it.app_id)})">推进状态</button>` : ""}
      <button class="btn btn-ghost btn-tiny" onclick="openNote(${arg(it.app_id)})">记一笔</button>
      ${it.pdf ? `<button class="btn btn-ghost btn-tiny" onclick="openPdf(${arg(it.pdf)})">打开 PDF</button>` : ""}
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- 岗位池 */

function viewJobs(d) {
  const rows = d.jobs.map((j) => `
    <div class="row">
      <div style="min-width:0">
        <div class="t">${esc(j.title || "（未命名）")}</div>
        <div class="s">${esc(j.org || "—")} · ${j.kind === "baoyan" ? "保研" : "实习"} · ${j.requirements} 条要求${j.deadline ? " · 截止 " + esc(j.deadline) : ""}</div>
      </div>
      <div class="r">
        <button class="btn btn-ghost btn-tiny" onclick="doMatch(${arg(j.id)})">匹配</button>
        <button class="btn btn-ghost btn-tiny" onclick="doGen(${arg(j.id)})">生成简历</button>
        <button class="btn btn-ghost btn-tiny" onclick="doLetter(${arg(j.id)}, ${arg(j.kind)})">${j.kind === "baoyan" ? "套磁信" : "开场白"}</button>
      </div>
    </div>`).join("")
    || `<div class="empty" style="margin:14px">岗位池是空的。点右上角「录入岗位」，或先跑一次知识库抽取。</div>`;

  return `<div class="panel">
    <div class="panel-head"><h3>岗位 / 导师</h3><span class="sub">${d.jobs.length} 个目标</span></div>
    <div class="rows">${rows}</div>
  </div>`;
}

/* ---------------------------------------------------------------- 知识库 */

function viewKb(d) {
  const kb = d.kb;
  const kinds = Object.entries(kb.by_kind || {})
    .map(([k, n]) => `<span class="chip">${esc(k)} ${n}</span>`).join(" ");
  const broken = (kb.broken_files || [])
    .map((b) => `<div class="alert error">${esc(b.file)}：${esc(b.error)}</div>`).join("");

  // 取 level 不取 name：名字永远叫 OCR，看不出开没开
  const ocr = (d.doctor.checks || []).find((c) => c.name.startsWith("OCR"));
  const ocrText = !ocr ? "未检测"
    : (ocr.level === "ok" ? `已启用 · ${ocr.detail || ""}`.trim() : "未启用（读不到扫描件）");

  return `<div class="stats">
      <div class="stat"><b>${kb.evidence_count}</b><span>证据条目</span></div>
      <div class="stat"><b>${kb.total_metrics}</b><span>量化指标</span></div>
      <div class="stat ${kb.missing_source_metrics ? "hot" : "good"}"><b>${kb.missing_source_metrics}</b><span>指标缺出处</span></div>
      <div class="stat"><b>${d.templates.length}</b><span>简历模板</span></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>构成</h3><span class="sub">${kinds || "还没有证据"}</span></div>
      ${broken ? `<div class="kv" style="display:block">${broken}</div>` : ""}
      <div class="kv">
        <div><span>语义召回</span><b>${d.embed.enabled ? "已启用 · " + esc(d.embed.model) : "未启用（BM25）"}</b></div>
        <div><span>已缓存向量</span><b>${d.embed.cache_vectors}</b></div>
        <div><span>扫描件 OCR</span><b>${esc(ocrText)}</b></div>
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>校对清单</h3><span class="sub">抽取是模型做的，这几类错要人工过一眼</span></div>
      <div id="reviewBox"><div class="loading"><span class="spinner"></span>正在检查…</div></div>
    </div>`;
}

async function loadReview() {
  const box = $("#reviewBox");
  if (!box) return;
  try {
    const r = await rpc("kb_review", {});
    const fs = r.findings || [];
    if (!fs.length) {
      box.innerHTML = `<div class="empty" style="margin:14px">没有发现疑点。</div>`;
      return;
    }
    box.innerHTML = `<div class="rows">${fs.map((f) => `
      <div class="row">
        <div><div class="t">${esc(f.title || f.evidence_id)}</div>
        <div class="s">${esc(f.message)}${f.suggestion ? " · " + esc(f.suggestion) : ""}</div></div>
        <div class="r"><span class="chip ${f.level === "warn" ? "warn" : ""}">${esc(f.field)}</span></div>
      </div>`).join("")}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty" style="margin:14px">${esc(e.message)}</div>`;
  }
}

/* ---------------------------------------------------------------- 自检 */

function viewSystem(d) {
  const icon = { ok: "✓", warn: "!", fail: "✗", info: "·" };
  const cls = { ok: "ok", warn: "warn", fail: "err", info: "" };
  const rows = d.doctor.checks.map((c) => `
    <div class="row">
      <div><div class="t">${esc(c.name)}</div>
      ${c.detail ? `<div class="s">${esc(c.detail)}</div>` : ""}
      ${c.fix ? `<div class="s">→ ${esc(c.fix)}</div>` : ""}</div>
      <div class="r"><span class="chip ${cls[c.level]}">${icon[c.level]} ${c.level}</span></div>
    </div>`).join("");

  return `<div class="stats">
      <div class="stat ${d.doctor.failed ? "hot" : "good"}"><b>${d.doctor.failed}</b><span>必须处理</span></div>
      <div class="stat"><b>${d.doctor.warned}</b><span>建议处理</span></div>
      <div class="stat"><b>${d.doctor.checks.length}</b><span>检查项</span></div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3>自检结果</h3><span class="sub mono">${esc(d.doctor.root)}</span></div>
      <div class="rows">${rows}</div>
    </div>`;
}

/* ---------------------------------------------------------------- 动作 */

async function buildKb(force) {
  await act(async () => {
    const r = await rpc("kb_build", { force });
    const n = (r.created || []).length;
    toast(force ? `全量重抽完成，${n} 个文件夹` : `增量抽取完成，${n} 个新文件夹`, "ok");
    return r;
  });
}

async function doMatch(jobId) {
  await act(async () => {
    const r = await rpc("job_match", { job_id: jobId });
    const hits = (r.report && r.report.hits) || r.hits || [];
    const gaps = (r.report && r.report.gaps) || r.gaps || [];
    state.lastMatch = { jobId, hits, gaps };
    toast(`匹配完成，${hits.length} 条对口`, "ok");
    openMatchModal(jobId);
    return r;
  });
}

async function doGen(jobId, template) {
  await act(async () => {
    const r = await rpc("resume_generate", { job_id: jobId, template: template || "single_column" });
    if (r.pdf_path) {
      const errs = (r.lint && r.lint.stats && r.lint.stats.errors) || 0;
      toast(`简历已生成 · ${r.pages || "?"} 页 · ${errs} 错误`, "ok");
      openPdf(r.pdf_path);
    } else {
      toast(r.hint || "PDF 没生成出来", "err");
    }
    return r;
  });
}

async function doLetter(jobId, kind) {
  await act(async () => {
    const tool = kind === "baoyan" ? "cover_letter_generate" : "pitch_generate";
    const r = await rpc(tool, { job_id: jobId });
    const text = (r.letter && r.letter.body) || (r.pitch && r.pitch.full) || JSON.stringify(r).slice(0, 400);
    const warns = (r.letter && r.letter.warnings) || (r.pitch && r.pitch.warnings) || [];
    openTextModal(kind === "baoyan" ? "套磁信" : "开场白", text, warns);
    return r;
  });
}

function openPdf(path) {
  vscode.postMessage({ type: "openFile", path });
  toast("已用系统阅读器打开", "ok");
}

function openBoard() {
  vscode.postMessage({ type: "command", command: "openBoard" });
}

async function openBoardHtml() {
  await act(async () => {
    const r = await rpc("app_board", { html: true });
    if (r.html_path) {
      vscode.postMessage({ type: "openFile", path: r.html_path });
      toast("看板页已生成，用浏览器打开了", "ok");
    }
    return r;
  });
}

// 侧栏点一下卡片：直接生成简历，省得先开面板
async function quickGen(jobId) {
  await doGen(jobId);
}

/* ---------------------------------------------------------------- 弹层 */

function openModal(title, html) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = html;
  $("#modal").classList.add("open");
}
function closeModal() { $("#modal").classList.remove("open"); }

document.addEventListener("click", (e) => {
  if (e.target.id === "modal" || e.target.id === "modalClose") closeModal();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

function openMatchModal(jobId) {
  const m = state.lastMatch;
  if (!m || m.jobId !== jobId) return;
  const hits = m.hits.map((h) => `<div class="row">
      <div><div class="t">${esc(h.title)} <span class="chip brand">${h.score}</span></div>
      <div class="s">${esc(h.reason || "")}</div></div>
      <div class="r">${(h.matched_keywords || []).slice(0, 4).map((k) => `<span class="chip">${esc(k)}</span>`).join("")}</div>
    </div>`).join("") || `<div class="empty">没有达到阈值的对口经历</div>`;
  const gaps = m.gaps.length
    ? `<div class="sep"></div><div class="s muted">能力缺口：${m.gaps.map(esc).join("、")}</div>` : "";
  openModal("匹配结果", `<div class="rows">${hits}</div>${gaps}`);
}

function openTextModal(title, text, warns) {
  const w = (warns || []).length
    ? `<div class="sep"></div>` + warns.map((x) => `<div class="alert warn">${esc(x)}</div>`).join("")
    : "";
  openModal(title, `<div style="white-space:pre-wrap;line-height:1.75">${esc(text)}</div>${w}`);
}

function openAdvance(appId) {
  const d = state.data;
  const item = d.board.items.find((i) => i.app_id === appId);
  const allowed = (item && item.allowed) || [];
  const opts = allowed.map((s) => `<option value="${esc(s)}">${esc(s)}</option>`).join("");
  openModal("推进状态", `
    <div class="field"><label>当前状态</label>
      <input value="${esc((item && item.status_label) || "")}" disabled></div>
    <div class="field"><label>推进到</label><select id="advTo">${opts}</select></div>
    <div class="field"><label>备注（可选）</label><input id="advNote" placeholder="如：HR 说下周约面"></div>
    <button class="btn btn-primary" onclick="submitAdvance(${arg(appId)})">确认推进</button>`);
}

async function submitAdvance(appId) {
  const to = $("#advTo").value;
  const note = $("#advNote").value;
  closeModal();
  await act(async () => rpc("app_advance", { app_id: appId, to_status: to, note }), "状态已推进");
}

function openNote(appId) {
  openModal("记一笔", `
    <div class="field"><label>备注</label>
      <textarea id="noteText" placeholder="HR 说了什么、约了几点、面试问了什么"></textarea></div>
    <button class="btn btn-primary" onclick="submitNote(${arg(appId)})">保存</button>`);
}

async function submitNote(appId) {
  const note = $("#noteText").value.trim();
  if (!note) { toast("备注不能为空", "err"); return; }
  closeModal();
  await act(async () => rpc("app_note", { app_id: appId, note }), "已记录");
}

function openNewJob() {
  openModal("录入岗位", `
    <div class="grid2">
      <div class="field"><label>类型</label>
        <select id="njKind"><option value="internship">实习</option><option value="baoyan">保研套磁</option></select></div>
      <div class="field"><label>公司 / 学院</label><input id="njOrg" placeholder="某互联网公司"></div>
      <div class="field"><label>岗位 / 方向</label><input id="njTitle" placeholder="推荐算法实习生"></div>
      <div class="field"><label>导师（保研必填）</label><input id="njMentor" placeholder="张老师"></div>
    </div>
    <div class="field"><label>JD 正文（粘贴）</label>
      <textarea id="njText" placeholder="岗位职责 / 任职要求…"></textarea></div>
    <button class="btn btn-primary" onclick="submitJob()">保存并解析</button>`);
}

async function submitJob() {
  const body = {
    kind: $("#njKind").value,
    org: $("#njOrg").value.trim(),
    title: $("#njTitle").value.trim(),
    mentor: $("#njMentor").value.trim(),
    raw_text: $("#njText").value.trim(),
  };
  if (!body.raw_text) { toast("JD 正文不能为空", "err"); return; }
  closeModal();
  await act(async () => rpc("job_add", body), "岗位已录入");
}

/* ---------------------------------------------------------------- 启动 */

refresh().catch((e) => {
  $("#app").innerHTML = `<div class="empty" style="margin:16px">
    读不到数据：${esc(e.message)}<br><br>
    检查：python 路径（设置里 resumeAssistant.pythonPath）、依赖装没装（pip install -r requirements.txt）</div>`;
  vscode.postMessage({ type: "error", text: e.message });
});
