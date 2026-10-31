/**
 * VSCode 扩展冒烟：不开编辑器，桩掉 `vscode` 模块，直接跑扩展的激活与调用链。
 *
 * 为什么能这么测：扩展里没有一行业务逻辑，它只是「起 python 子进程 + 转发工具调用
 * + 塞一段 HTML 给 webview」。这三件事都能在 node 里验证，不需要真的开 VSCode。
 *
 * 验证不到的部分要说清楚：webview 的真实渲染、活动栏图标、主题色适配，
 * 这些只能靠人在编辑器里看。这里只保证「点下去不会崩、数据是对的」。
 *
 * 跑法：node tests/vscode_smoke.js
 */

const fs = require("fs");
const path = require("path");
const Module = require("module");

const ROOT = path.resolve(__dirname, "..");
const EXT = path.join(ROOT, "vscode-extension");

// 沙箱：绝不碰真实 data/
const SB = path.join(ROOT, "tests", "sandbox", "vscode");
process.env.RESUME_ASSISTANT_MOCK = "1";
process.env.RESUME_ASSISTANT_DATA = path.join(SB, "data");
process.env.RESUME_ASSISTANT_WORKSPACE = path.join(SB, "workspace");
delete process.env.DEEPSEEK_API_KEY;

let PASS = 0;
const FAILS = [];
function check(name, cond, detail = "") {
  if (cond) {
    PASS++;
    console.log("  ok   " + name);
  } else {
    FAILS.push(name);
    console.log("  FAIL " + name + (detail ? "  -> " + detail : ""));
  }
}

/* ------------------------------------------------------------------ vscode 桩 */

const captured = { provider: null, commands: {}, html: null, messages: [], errors: [] };
const PY = path.join(ROOT, ".venv", "Scripts", "python.exe");

const vscodeStub = {
  workspace: {
    workspaceFolders: [{ uri: { fsPath: ROOT } }],
    getConfiguration: () => ({
      get: (k) => ({
        "resumeAssistant.pythonPath": PY,
        "resumeAssistant.projectPath": ROOT,
        "resumeAssistant.autoRefreshSeconds": 0,
      }[k]),
    }),
    onDidChangeConfiguration: () => ({ dispose() {} }),
  },
  window: {
    createOutputChannel: () => ({
      appendLine() {}, clear() {}, show() {}, dispose() {},
    }),
    createStatusBarItem: () => ({
      show() {}, dispose() {}, text: "", tooltip: "", command: "",
    }),
    registerWebviewViewProvider: (id, provider) => {
      captured.viewId = id;
      captured.provider = provider;
      return { dispose() {} };
    },
    createWebviewPanel: () => ({
      webview: fakeWebview(),
      onDidDispose() {},
    }),
    showErrorMessage: (m) => captured.errors.push(m),
  },
  commands: {
    registerCommand: (name, fn) => {
      captured.commands[name] = fn;
      return { dispose() {} };
    },
    executeCommand: async () => {},
  },
  Uri: {
    joinPath: (base, ...parts) => {
      const p = path.join(base.fsPath, ...parts);
      return { fsPath: p, toString: () => "file:///" + p.replace(/\\/g, "/") };
    },
    file: (p) => ({ fsPath: p, toString: () => "file:///" + String(p).replace(/\\/g, "/") }),
  },
  env: { openExternal: async () => true },
  StatusBarAlignment: { Left: 1 },
  ViewColumn: { Active: -1 },
};

function fakeWebview() {
  const wv = {
    options: {},
    html: "",
    cspSource: "vscode-resource://",
    asWebviewUri: (u) => "vscode-resource://" + encodeURIComponent(u.fsPath),
    onDidReceiveMessage(fn) { wv._handler = fn; return { dispose() {} }; },
    postMessage(m) { captured.messages.push(m); return Promise.resolve(true); },
  };
  return wv;
}

const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "vscode") return vscodeStub;
  return origLoad.call(this, request, parent, isMain);
};

/* ------------------------------------------------------------------ 工具 */

function waitFor(pred, ms = 60000) {
  return new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = () => {
      const v = pred();
      if (v) return resolve(v);
      if (Date.now() - t0 > ms) return reject(new Error("等待超时"));
      setTimeout(tick, 40);
    };
    tick();
  });
}

/* ------------------------------------------------------------------ 主流程 */

(async () => {
  fs.mkdirSync(path.join(SB, "data"), { recursive: true });
  const prof = path.join(ROOT, "data", "profile.example.yaml");
  if (fs.existsSync(prof)) fs.copyFileSync(prof, path.join(SB, "data", "profile.yaml"));

  const pkg = JSON.parse(fs.readFileSync(path.join(EXT, "package.json"), "utf8"));

  console.log("\n[1] 扩展清单");
  check("有 name / version / engines", !!(pkg.name && pkg.version && pkg.engines.vscode));
  check("main 指向存在的文件", fs.existsSync(path.join(EXT, pkg.main)), pkg.main);
  check("声明了活动栏视图容器", !!(pkg.contributes.viewsContainers.activitybar || []).length);
  const view = (pkg.contributes.views.resumeAssistant || [])[0];
  check("视图 id 是 webview 类型", view && view.type === "webview" && view.id === "resumeAssistant.board", JSON.stringify(view));
  check("图标文件存在", fs.existsSync(path.join(EXT, pkg.contributes.viewsContainers.activitybar[0].icon)));

  const ext = require(path.join(EXT, "extension.js"));

  console.log("\n[2] 激活");
  const context = { extensionUri: { fsPath: EXT }, subscriptions: [] };
  ext.activate(context);
  check("注册了看板视图", captured.viewId === "resumeAssistant.board", String(captured.viewId));
  const declared = (pkg.contributes.commands || []).map((c) => c.command);
  const missing = declared.filter((c) => !captured.commands[c]);
  check(`声明的 ${declared.length} 个命令都有实现`, missing.length === 0, missing.join(", "));

  console.log("\n[3] 侧栏视图");
  const wv = fakeWebview();
  captured.provider.resolveWebviewView({ webview: wv });
  const html = wv.html;
  check("注入了 HTML", html.length > 300, `${html.length} 字符`);
  check("有 CSP", html.includes("Content-Security-Policy"));
  check("CSP 允许 nonce 脚本", /script-src 'nonce-[A-Za-z0-9+/=]+'/.test(html));
  check("引用了 main.css / main.js", html.includes("main.css") && html.includes("main.js"));
  check("模式是 sidebar", html.includes('data-mode="sidebar"'));
  check("media 文件都在", fs.existsSync(path.join(EXT, "media", "main.js"))
    && fs.existsSync(path.join(EXT, "media", "main.css")));

  const html2 = ext.buildHtml(fakeWebview(), { fsPath: EXT }, "panel");
  const nonce1 = (html.match(/nonce-([A-Za-z0-9+/=]+)/) || [])[1];
  const nonce2 = (html2.match(/nonce-([A-Za-z0-9+/=]+)/) || [])[1];
  check("每次生成的 nonce 不同", nonce1 && nonce2 && nonce1 !== nonce2, `${nonce1} vs ${nonce2}`);
  check("panel 模式标记正确", html2.includes('data-mode="panel"'));

  console.log("\n[4] 工具调用（真实 MCP server）");
  const t0 = Date.now();
  wv._handler({ type: "call", id: 1, tool: "ui_overview", args: {} });
  const res = await waitFor(() => captured.messages.find((m) => m.type === "result" && m.id === 1));
  check("返回 ok", res.ok === true, res.error || "");
  const d = res.data || {};
  check("握手到数据（<20 秒）", Date.now() - t0 < 20000, `${Date.now() - t0}ms`);
  check("有 board", !!(d.board && d.board.columns), Object.keys(d).join(","));
  check("board 四栏", (d.board.columns || []).length === 4);
  check("有 doctor 检查项", (d.doctor.checks || []).length > 5, String((d.doctor.checks || []).length));
  check("有 profile", d.profile && "ok" in d.profile);
  check("有 embed 状态", d.embed && "enabled" in d.embed);
  check("有模板列表", Array.isArray(d.templates));

  console.log("\n[5] 出错也要说人话");
  wv._handler({ type: "call", id: 2, tool: "app_detail", args: { app_id: "app_不存在" } });
  const bad = await waitFor(() => captured.messages.find((m) => m.type === "result" && m.id === 2));
  check("失败时 ok=false", bad.ok === false, JSON.stringify(bad).slice(0, 160));
  check("错误信息不含 Traceback", !String(bad.error).includes("Traceback"), bad.error);

  wv._handler({ type: "call", id: 3, tool: "根本不存在的工具", args: {} });
  const nope = await waitFor(() => captured.messages.find((m) => m.type === "result" && m.id === 3));
  check("不存在的工具也返回失败而不是挂死", nope.ok === false, JSON.stringify(nope).slice(0, 160));

  console.log("\n[6] 打开文件走系统程序");
  const before = captured.messages.length;
  wv._handler({ type: "openFile", path: path.join(SB, "x.pdf") });
  await new Promise((r) => setTimeout(r, 120));
  check("openFile 不抛异常", true);
  check("openFile 不污染结果通道", captured.messages.length === before);

  console.log("\n[7] 进程收尾");
  const client = new ext.McpClient();
  await client.start();
  check("能独立起一个客户端", !!client.proc);
  client.dispose();
  await new Promise((r) => setTimeout(r, 200));
  check("dispose 之后子进程已退出", !client.proc);

  for (const s of context.subscriptions) {
    if (s && typeof s.dispose === "function") s.dispose();
  }

  console.log("\n" + "=".repeat(46));
  console.log(`通过 ${PASS} 项，失败 ${FAILS.length} 项`);
  if (FAILS.length) console.log("失败项：\n  - " + FAILS.join("\n  - "));
  console.log("=".repeat(46) + "\n");
  process.exit(FAILS.length ? 1 : 0);
})().catch((e) => {
  console.error("\n崩了：", e && e.stack || e);
  process.exit(1);
});
