# 简历助手 · VSCode 扩展

编辑器里点得动的简历投递看板。数据全部来自本机的 `resume_assistant` 知识库，不联网、不上传。

## 为什么是「扩展 + MCP」而不是「扩展 + 直连数据库」

这个项目的能力全在 `pipeline.py`，对外只有一套 MCP 工具（24 个）。插件里不写一行业务逻辑：

```
VSCode 扩展  ──MCP(stdio)──▶  python -m resume_assistant.mcp_server  ──▶  pipeline.py
本地网页     ──HTTP───────▶  web/server.py ──overview()──────────────▶  pipeline.py
终端         ──CLI────────▶  cli.py ─────────────────────────────────▶  pipeline.py
```

三个客户端共用同一份 `overview()` 组装逻辑。谁要是自己拼字段，迟早出现
「网页上有、插件里 undefined」——这种 bug 接口返回 200，最难查。

## 装法

**方式一：装 vsix（推荐）**

```bash
code --install-extension resume-assistant-0.1.0.vsix
```

**方式二：开发模式**

在 VSCode 里打开本仓库，按 `F5`（已配好 `.vscode/launch.json`）。

## 用法

1. 打开简历助手所在文件夹（要能看到 `data/` 和 `workspace/`）
2. 左侧活动栏点「简历助手」图标 → 侧栏显示待办
3. 命令面板 `Ctrl+Shift+P` → `简历助手：打开完整看板` → 四视图

| 入口 | 用途 |
|---|---|
| 侧栏「投递看板」 | 窄条：必须处理 / 待办 / 等回复，点卡片直接生成简历 |
| 命令：打开完整看板 | 四栏看板 + 岗位池 + 知识库校对 + 环境自检 |
| 命令：环境自检 | 结果输出到「输出」面板的「简历助手」频道 |
| 状态栏 | 有必须处理的投递时显示 `⚠ 简历助手 N` |

## 配置

| 设置项 | 默认 | 说明 |
|---|---|---|
| `resumeAssistant.pythonPath` | 空 | 留空则找 `.venv/Scripts/python.exe`，再退到 PATH 的 `python` |
| `resumeAssistant.projectPath` | 空 | 留空用当前工作区根目录 |
| `resumeAssistant.autoRefreshSeconds` | 0 | 自动刷新间隔，0 = 关 |

改了前两项会自动重启 MCP 子进程，不用重载窗口。

## 测试

不开编辑器也能测（`vscode` 模块被桩掉）：

```bash
node tests/vscode_smoke.js    # 30 项：清单 / 激活 / 视图 HTML / 真实 MCP 调用 / 错误路径
node tests/vscode_render.js   # 36 项：四视图渲染 + 点击→工具名映射 + Windows 路径转义
```

**测不到的部分**：webview 的真实观感、活动栏图标、深浅色主题适配。
这些只能在编辑器里看，代码层面保证的是「点了不会崩、数据是对的」。

## 已知限制

- 生成简历要等模型，5~15 秒。调用超时设了 5 分钟，但界面上只有转圈，没有进度条
- PDF 用系统默认阅读器打开（VSCode 自己渲染不了 PDF）
- 只支持中文
