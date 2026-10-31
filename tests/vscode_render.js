/**
 * Webview 渲染探针：不开编辑器，桩掉 acquireVsCodeApi，直接跑 media/main.js。
 *
 * 重点验两件事：
 * 1. 有数据时四个视图能画出来（不出现 undefined / NaN）
 * 2. 点按钮发出去的是「对的工具名 + 对的参数」——这里最容易写错，
 *    写错了界面不报错，只是点了没反应，最难查。
 *
 * 桩的两个坑（踩过）：
 * - MODE 是脚本加载时从 body.dataset 读一次的常量，改 dataset 不会生效，
 *   必须按模式各造一个实例。
 * - 工具调用要有回包，否则 act() 的 busy 锁一直不释放，后面所有点击都被静默挡掉。
 *
 * 跑法：node tests/vscode_render.js
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "vscode-extension", "media", "main.js"), "utf8");

let PASS = 0;
const FAILS = [];
function check(name, cond, detail = "") {
  if (cond) { PASS++; console.log("  ok   " + name); }
  else { FAILS.push(name); console.log("  FAIL " + name + (detail ? "  -> " + detail : "")); }
}

/* ------------------------------------------------------------------ 数据 */

function fixture() {
  const item = {
    app_id: "app_1", job_id: "job_1", kind: "internship",
    title: "推荐算法实习生", org: "某互联网公司", mentor: "",
    status: "composed", status_label: "已生成",
    days_in_status: 3, days_left: 2, pdf: "C:\\简历助手\\data\\output\\app_1\\resume.pdf",
    next: "打开 PDF 过一遍", allowed: ["sent"], reached: ["draft", "matched", "composed"],
    alerts: [
      { level: "error", text: "投出 12 天没动静" },
      { level: "warn", text: "截止还有 2 天" },
    ],
  };
  const baoyan = {
    app_id: "app_2", job_id: "job_2", kind: "baoyan", title: "推荐系统冷启动",
    org: "", mentor: "张老师", status: "draft", status_label: "建档",
    days_in_status: 1, days_left: null, pdf: "", allowed: ["matched"], alerts: [],
    next: "先跑一次匹配", reached: ["draft"],
  };
  return {
    ok: true,
    board: {
      total: 2,
      items: [item, baoyan],
      alerts: [item],
      funnel: {
        internship: { label: "实习", sent: 4, reply_rate: "50%" },
        baoyan: { label: "保研", sent: 0, reply_rate: "—" },
      },
      columns: [
        { title: "待办", count: 1, items: [baoyan] },
        { title: "已投出", count: 1, items: [item] },
        { title: "进行中", count: 0, items: [] },
        { title: "已结束", count: 0, items: [] },
      ],
    },
    kb: {
      evidence_count: 11, total_metrics: 24, missing_source_metrics: 0,
      by_kind: { project: 6, internship: 3 },
      broken_files: [{ file: "旧版简历.doc", error: "另存为 .docx" }],
    },
    jobs: [
      { id: "job_1", title: "推荐算法实习生", org: "某互联网公司", kind: "internship", requirements: 6, deadline: "2026-11-20" },
      { id: "job_2", title: "推荐系统冷启动", org: "", kind: "baoyan", requirements: 3, deadline: "" },
    ],
    apps: [{ id: "app_1", status: "composed" }],
    profile: { ok: true, name: "张三", school: "某某大学", major: "计算机科学与技术", example: true },
    templates: [{ key: "single_column" }, { key: "two_column" }],
    doctor: {
      ok: true, failed: 0, warned: 1, root: "C:\\简历助手",
      checks: [
        { level: "ok", name: "Python 3.11", detail: "", fix: "" },
        { level: "info", name: "OCR 未启用", detail: "没装", fix: "pip install rapidocr-onnxruntime" },
      ],
    },
    embed: { enabled: false, model: "BAAI/bge-m3", cache_vectors: 0 },
  };
}

/* ------------------------------------------------------------------ 实例 */

function makeEl(id) {
  return {
    id, _html: "", textContent: "", value: "", style: {},
    classList: { add() {}, remove() {}, contains() { return false; } },
    appendChild() {}, remove() {},
    set innerHTML(v) { this._html = v; },
    get innerHTML() { return this._html; },
  };
}

/** 造一个 webview 实例：模式固定、工具调用自动回包（不设 busy 死锁）。 */
function makeInstance(mode, data) {
  const nodes = {};
  const sent = [];
  let onMessage = null;

  const doc = {
    body: { dataset: { mode }, style: {} },
    querySelector(sel) { nodes[sel] = nodes[sel] || makeEl(sel); return nodes[sel]; },
    querySelectorAll() { return []; },
    createElement() { return makeEl("tmp"); },
    addEventListener() {},
  };

  const api = {
    postMessage(m) {
      sent.push(m);
      // 真实扩展一定会回包；不回包的话 act() 的 busy 永远不会释放
      if (m.type === "call") {
        setTimeout(() => {
          const payload = m.tool === "ui_overview" ? data : { ok: true };
          if (onMessage) onMessage({ data: { type: "result", id: m.id, ok: true, data: payload } });
        }, 5);
      }
      return Promise.resolve(true);
    },
  };

  const win = { addEventListener(type, fn) { if (type === "message") onMessage = fn; } };
  const factory = new Function(
    "document", "window", "acquireVsCodeApi", "setTimeout",
    SRC + "\n;return {refresh, render, state, viewBoard, viewJobs, viewKb, viewSystem, doGen, doMatch, doLetter, buildKb, setView};"
  );
  const fns = factory(doc, win, () => api, (fn) => fn());
  return { fns, doc, nodes, sent };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ 跑 */

(async () => {
  const data = fixture();
  const panel = makeInstance("panel", data);
  const fns = panel.fns;

  console.log("\n[1] 首屏");
  await sleep(30);
  check("启动就发了 ui_overview", panel.sent.length >= 1 && panel.sent[0].tool === "ui_overview",
    JSON.stringify(panel.sent[0] || {}));
  check("拿到数据后 state 已填充", !!fns.state.data);

  console.log("\n[2] 面板模式");
  const html = panel.doc.querySelector("#app").innerHTML;
  check("画出了内容", html.length > 500, `${html.length} 字符`);
  const tabs = (html.match(/class="tab[ "]/g) || []).length;
  check("四个 tab", tabs === 4, `实际 ${tabs}`);
  check("四栏看板", (html.match(/class="col"/g) || []).length === 4);
  check("卡片渲染", html.includes("推荐算法实习生") && html.includes("推荐系统冷启动"));
  check("error 告警上屏", html.includes("投出 12 天没动静"));
  check("截止标红", html.includes("2 天截止"));
  check("保研标签", html.includes("保研"));
  check("示例档案提醒", html.includes("现在用的是示例档案"));
  check("回复率取到", html.includes("50%"));
  check("没有 undefined", !html.includes("undefined"));
  check("没有 NaN", !/NaN/.test(html));

  console.log("\n[3] 其它视图");
  const jobs = fns.viewJobs(data);
  check("岗位池两行", (jobs.match(/class="row"/g) || []).length === 2);
  check("保研行显示套磁信", jobs.includes("套磁信"));
  check("实习行显示开场白", jobs.includes("开场白"));

  const kb = fns.viewKb(data);
  check("知识库统计", kb.includes(">11<") && kb.includes(">24<"));
  check("坏文件上屏", kb.includes("旧版简历.doc"));
  const ocrCell = kb.match(/扫描件 OCR<\/span><b>([^<]*)</);
  check("OCR 说清未启用", !!ocrCell && ocrCell[1].includes("未启用"), ocrCell ? ocrCell[1] : "");
  check("语义召回说清未启用", kb.includes("未启用（BM25）"));

  const sys = fns.viewSystem(data);
  check("自检列出检查项", sys.includes("Python 3.11") && sys.includes("OCR 未启用"));
  check("自检带修复建议", sys.includes("pip install rapidocr-onnxruntime"));

  console.log("\n[4] 侧栏模式");
  const side = makeInstance("sidebar", data);
  await sleep(30);
  const sh = side.doc.querySelector("#app").innerHTML;
  check("侧栏画出来了", sh.length > 300, `${sh.length} 字符`);
  check("有待办分组", sh.includes("待办"));
  check("有打开看板按钮", sh.includes("打开看板"), sh.slice(0, 120));
  check("示例档案提醒", sh.includes("示例档案"));
  check("没有 undefined", !sh.includes("undefined"));

  console.log("\n[5] 点击 → 工具名映射");
  const calls = () => panel.sent.filter((m) => m.type === "call");
  const before = calls().length;

  await fns.doGen("job_1");
  await sleep(20);
  let now = calls().slice(before);
  const gen = now.filter((m) => m.tool === "resume_generate")[0];
  check("生成简历 → resume_generate", !!gen, JSON.stringify(now.map((m) => m.tool)));
  check("带上 job_id 和默认模板",
    gen && gen.args.job_id === "job_1" && gen.args.template === "single_column",
    JSON.stringify(gen && gen.args));

  const mark = calls().length;
  await fns.doMatch("job_1");
  await sleep(20);
  const match = calls().slice(mark).filter((m) => m.tool === "job_match")[0];
  check("匹配 → job_match", !!match, JSON.stringify(calls().slice(mark).map((m) => m.tool)));
  check("匹配参数对", match && match.args.job_id === "job_1", JSON.stringify(match && match.args));

  const m2 = calls().length;
  await fns.doLetter("job_2", "baoyan");
  await sleep(20);
  check("保研 → cover_letter_generate",
    calls().slice(m2).some((m) => m.tool === "cover_letter_generate"),
    JSON.stringify(calls().slice(m2).map((m) => m.tool)));

  const m3 = calls().length;
  await fns.doLetter("job_1", "internship");
  await sleep(20);
  check("实习 → pitch_generate",
    calls().slice(m3).some((m) => m.tool === "pitch_generate"),
    JSON.stringify(calls().slice(m3).map((m) => m.tool)));

  const m4 = calls().length;
  await fns.buildKb(true);
  await sleep(20);
  const kbCall = calls().slice(m4).filter((m) => m.tool === "kb_build")[0];
  check("全量重抽 → kb_build{force:true}", kbCall && kbCall.args.force === true,
    JSON.stringify(kbCall && kbCall.args));

  console.log("\n[6] 参数里的 Windows 路径");
  const inline = fns.viewBoard(data);
  const m = inline.match(/openPdf\((&quot;|")(.*?)(&quot;|")\)/);
  check("PDF 路径进了 onclick", !!m);
  check("反斜杠没被当成转义符", !m || !/&#10;|&#13;/.test(m[2]), m ? m[2] : "");

  console.log("\n" + "=".repeat(46));
  console.log(`通过 ${PASS} 项，失败 ${FAILS.length} 项`);
  if (FAILS.length) console.log("失败项：\n  - " + FAILS.join("\n  - "));
  console.log("=".repeat(46) + "\n");
  process.exit(FAILS.length ? 1 : 0);
})().catch((e) => {
  console.error("\n崩了：", (e && e.stack) || e);
  process.exit(1);
});
