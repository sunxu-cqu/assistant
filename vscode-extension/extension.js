"use strict";

/**
 * 简历助手 · VSCode 扩展
 *
 * 定位：编辑器里的一个壳。业务逻辑一行都不在这里 —— 全部走
 * `python -m resume_assistant.mcp_server` 暴露的 MCP 工具。
 *
 * 为什么不直接读 sqlite / 调 CLI：
 * 1. MCP 是这项目对外的唯一契约，CLI、网页、harness 都走它，插件没理由另开一条路；
 * 2. 契约一变，四处一起坏，能立刻发现；各走各的就会「网页上有、插件里 undefined」；
 * 3. 插件不需要知道数据存哪，换个 python 实现也不用改这里。
 *
 * 所以这个文件只做三件事：起子进程、转发工具调用、把结果丢给 webview。
 */

const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const DEFAULT_TIMEOUT = 60000;
// 这几类要等模型，30 秒是不够的；gen 一次 5~15 秒，kb_build 一个项目就可能十几秒
const SLOW_TOOLS = new Set([
  "kb_build", "job_match", "resume_generate", "resume_preview",
  "resume_rerender", "cover_letter_generate", "pitch_generate", "job_add",
]);
const SLOW_TIMEOUT = 300000;

let output; // OutputChannel

function log(msg) {
  if (output) output.appendLine(`[${new Date().toLocaleTimeString()}] ${msg}`);
}

/* ------------------------------------------------------------------ MCP 客户端 */

class McpClient {
  constructor() {
    this.proc = null;
    this.starting = null;
    this.seq = 0;
    this.buf = "";
    this.pending = new Map();
  }

  /** 找 python 和项目根目录。找不到要说清楚找过哪儿，别只报 ENOENT。 */
  resolve() {
    const cfg = vscode.workspace.getConfiguration("resumeAssistant");
    const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
    const root = cfg.get("projectPath") || (folder ? folder.uri.fsPath : "");
    if (!root) {
      throw new Error("没打开工作区。请打开简历助手所在文件夹，或在设置里填 resumeAssistant.projectPath");
    }

    const tried = [];
    let py = (cfg.get("pythonPath") || "").trim();
    if (!py) {
      const venv = process.platform === "win32"
        ? path.join(root, ".venv", "Scripts", "python.exe")
        : path.join(root, ".venv", "bin", "python");
      tried.push(venv);
      if (fs.existsSync(venv)) {
        py = venv;
      } else {
        py = process.platform === "win32" ? "python" : "python3";
        tried.push(py);
      }
    }
    return { root, py, tried };
  }

  start() {
    if (this.starting) return this.starting;

    this.starting = new Promise((resolve, reject) => {
      let py, root, tried;
      try {
        ({ py, root, tried } = this.resolve());
      } catch (e) {
        this.starting = null;
        return reject(e);
      }

      log(`启动 ${py} -m resume_assistant.mcp_server  (cwd=${root})`);
      const proc = cp.spawn(py, ["-m", "resume_assistant.mcp_server"], {
        cwd: root,
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
        windowsHide: true,
      });
      this.proc = proc;

      proc.stdout.setEncoding("utf8");
      proc.stdout.on("data", (chunk) => this.onData(chunk));
      proc.stderr.setEncoding("utf8");
      proc.stderr.on("data", (d) => log(`[server] ${String(d).trim()}`));

      proc.on("error", (err) => {
        this.starting = null;
        this.proc = null;
        reject(new Error(
          `起不来 ${py}：${err.message}\n找过：${tried.join("、")}\n` +
          `在设置里填 resumeAssistant.pythonPath 指到你的 python.exe`
        ));
      });

      proc.on("exit", (code) => {
        log(`MCP server 退出，code=${code}`);
        this.proc = null;
        this.starting = null;
        this.failAll(`MCP server 退出（code ${code}）`);
      });

      this.request("initialize", {
        protocolVersion: "2025-06-18",
        capabilities: {},
        clientInfo: { name: "vscode-resume-assistant", version: "0.1.0" },
      }, 20000).then((res) => {
        const info = (res.result && res.result.serverInfo) || {};
        log(`握手完成：${info.name || "?"} ${info.version || ""}`);
        this.notify("notifications/initialized", {});
        resolve(res);
      }).catch((e) => {
        this.starting = null;
        reject(e);
      });
    });

    return this.starting;
  }

  onData(chunk) {
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        log(`非 JSON 输出（已忽略）：${line.slice(0, 200)}`);
        continue;
      }
      // 服务端会在响应之间插 notifications（日志、进度），没有 id 的一律跳过
      if (msg.id === undefined || msg.id === null) continue;
      const p = this.pending.get(msg.id);
      if (!p) continue;
      this.pending.delete(msg.id);
      clearTimeout(p.timer);
      p.resolve(msg);
    }
  }

  send(payload) {
    if (!this.proc || !this.proc.stdin) throw new Error("MCP server 没在跑");
    this.proc.stdin.write(JSON.stringify(payload) + "\n");
  }

  notify(method, params) {
    try {
      this.send({ jsonrpc: "2.0", method, params });
    } catch (e) {
      log(`notify 失败：${e.message}`);
    }
  }

  request(method, params, timeout = DEFAULT_TIMEOUT) {
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} 超时（${Math.round(timeout / 1000)} 秒）。模型慢的时候重试一次`));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this.send({ jsonrpc: "2.0", id, method, params });
      } catch (e) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(e);
      }
    });
  }

  failAll(reason) {
    for (const [, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(new Error(reason));
    }
    this.pending.clear();
  }

  /** 调一个工具。ok=false 直接抛，把 hint 带出去 —— 界面只负责显示，不判断。 */
  async call(tool, args) {
    await this.start();
    const timeout = SLOW_TOOLS.has(tool) ? SLOW_TIMEOUT : DEFAULT_TIMEOUT;
    const resp = await this.request("tools/call", { name: tool, arguments: args || {} }, timeout);
    if (resp.error) {
      throw new Error(resp.error.message || JSON.stringify(resp.error));
    }
    const result = resp.result || {};
    const content = result.content || [];
    const text = content.length ? content[0].text || "" : "";

    // 工具名写错时，服务端回的是纯文本 "Unknown tool: xxx"，不是 JSON。
    // 早先这里解析失败就包成 {ok:true, text} 当成功返回，界面拿到一句英文当数据用。
    if (result.isError) {
      throw new Error(text || "调用失败");
    }
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      throw new Error(text ? text.slice(0, 300) : "工具没有返回内容");
    }
    if (data && data.ok === false) {
      throw new Error(data.hint || data.error || "调用失败");
    }
    return data;
  }

  dispose() {
    this.failAll("扩展已关闭");
    if (this.proc) {
      try { this.proc.kill(); } catch { /* 已经死了就算了 */ }
      this.proc = null;
    }
    this.starting = null;
  }
}

/* ------------------------------------------------------------------ Webview */

function buildHtml(webview, extensionUri, mode) {
  const nonce = crypto.randomBytes(16).toString("base64");
  const uri = (f) => webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", f));
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `style-src ${webview.cspSource} 'unsafe-inline'`,
    `font-src ${webview.cspSource}`,
    `script-src 'nonce-${nonce}'`,
  ].join("; ");

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${uri("main.css")}">
<title>简历助手</title>
</head>
<body data-mode="${mode}">
  <div id="app"><div class="loading"><span class="spinner"></span>正在读取档案…</div></div>
  <div id="toast"></div>
  <div class="modal" id="modal">
    <div class="modal-box">
      <div class="modal-head"><h3 id="modalTitle">预览</h3>
        <button class="icon-btn" id="modalClose" title="关闭">✕</button></div>
      <div class="modal-body" id="modalBody"></div>
    </div>
  </div>
  <script nonce="${nonce}" src="${uri("main.js")}"></script>
</body>
</html>`;
}

/** 把 webview 的「调工具」和扩展的 MCP 客户端接起来。所有动作都走这一条路。 */
function wire(webview, client, onData) {
  webview.onDidReceiveMessage(async (msg) => {
    if (!msg || !msg.type) return;

    if (msg.type === "call") {
      try {
        const data = await client.call(msg.tool, msg.args);
        webview.postMessage({ type: "result", id: msg.id, ok: true, data });
        if (onData && msg.tool === "ui_overview") onData(data);
      } catch (e) {
        log(`工具 ${msg.tool} 失败：${e.message}`);
        webview.postMessage({ type: "result", id: msg.id, ok: false, error: e.message });
      }
      return;
    }

    if (msg.type === "openFile") {
      // VSCode 自己渲染不了 PDF，交给系统默认阅读器
      try {
        await vscode.env.openExternal(vscode.Uri.file(msg.path));
      } catch (e) {
        vscode.window.showErrorMessage(`打不开 ${msg.path}：${e.message}`);
      }
      return;
    }

    if (msg.type === "command" && msg.command === "openBoard") {
      vscode.commands.executeCommand("resumeAssistant.openBoard");
      return;
    }

    if (msg.type === "error") {
      log(`界面报错：${msg.text}`);
    }
  });
}

class BoardViewProvider {
  constructor(context, client, status) {
    this.context = context;
    this.client = client;
    this.status = status;
    this.view = null;
  }

  resolveWebviewView(webviewView) {
    this.view = webviewView;
    const { webview } = webviewView;
    webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.context.extensionUri, "media")],
    };
    webview.html = buildHtml(webview, this.context.extensionUri, "sidebar");
    wire(webview, this.client, (data) => this.status.update(data));
  }

  refresh() {
    if (this.view) this.view.webview.postMessage({ type: "refresh" });
  }
}

/* ------------------------------------------------------------------ 状态栏 */

class StatusBar {
  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
    this.item.command = "resumeAssistant.openBoard";
    this.item.text = "$(book) 简历助手";
    this.item.tooltip = "打开投递看板";
    this.item.show();
  }

  update(data) {
    const board = (data && data.board) || {};
    const items = board.items || [];
    const todo = items.filter((i) => ["draft", "matched", "composed"].includes(i.status)).length;
    const hot = items.filter((i) => (i.alerts || []).some((a) => a.level === "error")).length;
    if (hot) {
      this.item.text = `$(warning) 简历助手 ${hot}`;
      this.item.tooltip = `${hot} 条必须处理 · 点开看板`;
    } else if (todo) {
      this.item.text = `$(book) 简历助手 ${todo}`;
      this.item.tooltip = `${todo} 条待投 · 点开看板`;
    } else {
      this.item.text = "$(book) 简历助手";
      this.item.tooltip = "打开投递看板";
    }
  }

  dispose() {
    this.item.dispose();
  }
}

/* ------------------------------------------------------------------ 激活 */

function activate(context) {
  output = vscode.window.createOutputChannel("简历助手");
  const client = new McpClient();
  const status = new StatusBar();
  const provider = new BoardViewProvider(context, client, status);

  context.subscriptions.push(
    output,
    { dispose: () => client.dispose() },
    { dispose: () => status.dispose() },
    vscode.window.registerWebviewViewProvider("resumeAssistant.board", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("resumeAssistant.refresh", () => {
      provider.refresh();
    }),

    vscode.commands.registerCommand("resumeAssistant.openBoard", async () => {
      const panel = vscode.window.createWebviewPanel(
        "resumeAssistant.panel",
        "简历助手",
        vscode.ViewColumn.Active,
        {
          enableScripts: true,
          retainContextWhenHidden: true,
          localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
        }
      );
      panel.webview.html = buildHtml(panel.webview, context.extensionUri, "panel");
      wire(panel.webview, client, (data) => status.update(data));
      panel.onDidDispose(() => { /* 面板关了，子进程留着给侧栏用 */ });
    }),

    vscode.commands.registerCommand("resumeAssistant.doctor", async () => {
      try {
        const res = await client.call("doctor", {});
        output.clear();
        output.appendLine(`环境自检 · ${res.root || ""}`);
        output.appendLine("─".repeat(56));
        const icon = { ok: "✓", warn: "!", fail: "✗", info: "·" };
        for (const c of res.checks || []) {
          output.appendLine(`  ${icon[c.level] || "·"} ${c.name}${c.detail ? " — " + c.detail : ""}`);
          if (c.fix && c.level !== "ok") output.appendLine(`      → ${c.fix}`);
        }
        output.appendLine("─".repeat(56));
        output.appendLine(`${res.failed} 项必须处理，${res.warned} 项建议处理`);
        output.show(true);
      } catch (e) {
        vscode.window.showErrorMessage(`自检跑不起来：${e.message}`);
        output.show(true);
      }
    }),

    vscode.commands.registerCommand("resumeAssistant.revealData", async () => {
      try {
        const { root } = client.resolve();
        const dataDir = path.join(root, "data");
        if (!fs.existsSync(dataDir)) fs.mkdirSync(dataDir, { recursive: true });
        await vscode.commands.executeCommand("revealFileInOS", vscode.Uri.file(dataDir));
      } catch (e) {
        vscode.window.showErrorMessage(e.message);
      }
    })
  );

  // 配置改了要重启子进程，否则 pythonPath 改了不生效，用户会以为设置没保存
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("resumeAssistant.pythonPath") || e.affectsConfiguration("resumeAssistant.projectPath")) {
        log("配置变了，重启 MCP 子进程");
        client.dispose();
        provider.refresh();
      }
    })
  );

  log("扩展已激活");
}

function deactivate() {
  if (output) output.dispose();
}

module.exports = { activate, deactivate, McpClient, buildHtml };
