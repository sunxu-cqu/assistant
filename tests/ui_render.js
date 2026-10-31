// 前端渲染探针：不开浏览器，直接把 ui.html 的内联脚本跑起来，断言四个视图渲染正确。
//
// 为什么需要它：这台机器上 Edge 的无头模式产不出截图（退出码 0、零输出、无文件），
// 而「看板有告警」「OCR 已启用」这类分支只有数据非空时才走到，
// 光靠接口测试发现不了「字段取错了」——它渲染出的是 undefined，接口照样 200。
//
// 用法：node tests/ui_render.js <overview.json>
// 退出码 0 = 全过；1 = 有失败。没装 node 时由 web_smoke.py 跳过。
const fs = require("fs");

const fixture = process.argv[2];
if (!fixture) {
  console.error("用法: node tests/ui_render.js <overview.json>");
  process.exit(2);
}
const data = JSON.parse(fs.readFileSync(fixture, "utf8"));

// ---- 最小 DOM 桩：只实现 ui.html 用到的那几个方法 ----
function el(id) {
  return {
    id, _html: "", textContent: "", hidden: false, value: "", dataset: {},
    style: {}, classList: { add() {}, remove() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, remove() {},
    set innerHTML(v) { this._html = v; }, get innerHTML() { return this._html; },
    querySelector() { return el("child"); },
    closest() { return null; },
  };
}
const nodes = {};
const document = {
  querySelector(sel) { nodes[sel] = nodes[sel] || el(sel); return nodes[sel]; },
  querySelectorAll() { return []; },
  createElement() { return el("tmp"); },
  addEventListener() {},
  body: { style: {} },
};

const html = fs.readFileSync("resume_assistant/web/ui.html", "utf8");
const src = html.slice(html.indexOf("<script>") + 8, html.lastIndexOf("</script>"));
// 去掉自动执行的 refresh()，改由脚本自己驱动
const patched = src.replace(/refresh\(\)\.catch[\s\S]*?\}\);/, "");
const fn = new Function("document", "window", "setInterval", "fetch",
  patched + "\n;return {refresh, render, state, viewBoard, viewJobs, viewKb, viewSystem};");
// fetch 永远返回这份快照，探针不依赖服务在跑
const stubFetch = () => Promise.resolve({ status: 200, json: () => Promise.resolve(data) });
const api = fn(document, {}, () => 0, stubFetch);

let bad = 0;
const ok = (name, cond, extra = "") => {
  console.log(`  ${cond ? "ok  " : "FAIL"} ${name}${extra ? " · " + extra : ""}`);
  if (!cond) bad++;
};

(async () => {
  await api.refresh();
  const d = api.state.data;
  if (!d || !d.board) { console.log("  FAIL 快照里没有 board，先跑一次 overview"); process.exit(1); }

  // ---- 灌满：让「有告警 / 有截止 / 有 PDF」这些分支真的被渲染 ----
  const it = d.board.items && d.board.items[0];
  if (it) {
    it.alerts = [
      { level: "error", text: "投出 12 天没有任何动静，建议跟一封" },
      { level: "warn", text: "截止日期还有 2 天" },
      { level: "info", text: "面试前把 JD 和证据再过一遍" },
    ];
    it.days_left = 2;
    it.pdf = "C:\\简历助手\\data\\output\\app_1\\resume.pdf";
    it.days_in_status = 12;
    d.board.alerts = [it];
    d.board.columns[0].items = [it];
    d.board.columns[0].count = 1;
  }
  if (d.board.funnel && d.board.funnel.internship) {
    d.board.funnel.internship.sent = 4;
    d.board.funnel.internship.reply_rate = "50%";
  }
  if (d.jobs && d.jobs[0]) api.state.expanded[d.jobs[0].id] = true;
  d.kb.broken_files = [{ file: "旧版简历.doc", error: "老版 Office 格式打不开，另存为 .docx" }];
  d.kb.missing_source_metrics = 3;

  console.log("[1] 看板");
  const board = api.viewBoard(d);
  ok("渲染出卡片", board.includes('class="card"'));
  ok("error 告警上屏", board.includes('class="alert error"'));
  ok("warn 告警上屏", board.includes('class="alert warn"'));
  ok("截止 2 天标红", board.includes("chip err") && board.includes("2 天截止"));
  ok("PDF 就绪标记", board.includes("PDF 就绪"));
  ok("停留天数上屏", board.includes("12 天未动"));
  ok("回复率取到", board.includes("50%"));
  ok("没有 undefined", !board.includes("undefined"));
  ok("没有 NaN", !/NaN/.test(board));

  console.log("\n[2] 岗位池");
  const jobs = api.viewJobs(d);
  ok("渲染出行", jobs.includes('class="row"'));
  ok("展开占位有样式", jobs.includes('class="mt"') && /\.mt\{margin-top:10px\}/.test(html));
  ok("没有 undefined", !jobs.includes("undefined"));

  console.log("\n[3] 知识库 · OCR 未启用");
  const ocrCheck = (d.doctor.checks || []).find(c => c.name.startsWith("OCR"));
  if (ocrCheck) { ocrCheck.level = "info"; ocrCheck.name = "OCR 未启用"; ocrCheck.detail = "没装"; }
  const kb = api.viewKb(d);
  const cell1 = kb.match(/扫描件 OCR<\/span><b>([^<]*)</)?.[1] || "";
  ok("坏文件上屏", kb.includes("旧版简历.doc"));
  ok("缺出处标红", kb.includes('class="stat hot"'));
  ok("OCR 说清未启用", cell1.includes("未启用"), cell1);
  ok("没有 undefined", !kb.includes("undefined"));

  console.log("\n[4] 知识库 · OCR 已启用");
  if (ocrCheck) { ocrCheck.level = "ok"; ocrCheck.name = "OCR rapidocr-onnxruntime"; ocrCheck.detail = "rapidocr-onnxruntime"; }
  const kb2 = api.viewKb(d);
  const cell2 = kb2.match(/扫描件 OCR<\/span><b>([^<]*)</)?.[1] || "";
  ok("显示引擎名", cell2.includes("已启用") && cell2.includes("rapidocr"), cell2);
  ok("不是裸的检查项名", !cell2.trim().startsWith("OCR "), cell2);

  console.log("\n[5] 环境自检");
  const sys = api.viewSystem(d);
  ok("有内容", sys.length > 200);
  ok("没有 undefined", !sys.includes("undefined"));

  console.log(`\n失败 ${bad} 项`);
  process.exit(bad ? 1 : 0);
})();
