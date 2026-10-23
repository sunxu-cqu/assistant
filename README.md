# 简历助手

本地知识库驱动的定向简历生成器。核心是一个 **MCP server**，VSCode 插件 / 终端 CLI / 任意 agent harness 都是它的客户端。

不是"AI 帮你写简历"，而是：**先把你的经历拆成带证据的原子单元，再按目标岗位检索、打分、组装，最后用代码校验每个数字有没有出处。**

---

## 核心设计

| 层 | 做什么 | 谁负责 |
|---|---|---|
| 知识库 | 一个项目一个文件夹 → 抽取成原子成就单元 | LLM 抽取，代码校验 |
| 匹配 | BM25 + 标签召回 Top-30 → LLM 精排 + 缺口分析 | 混合 |
| 组稿 | 按模板槽位选材 → LLM 只改写措辞 | LLM 受限生成 |
| 校验 | 结构检查 + 数字溯源比对 | **纯代码，不花 token** |
| 渲染 | Resume IR → Typst → PDF | 纯代码 |
| 状态机 | 投递全生命周期 | 纯代码 + SQLite |

三条铁律：

1. **LLM 只出现在「JD 解析 / 精排 / 措辞」三处**，其余全是确定性代码。
2. **简历里每个数字必须能在证据库找到出处**，找不到就拒绝渲染。
3. **组稿是选择题不是填空题**——LLM 不能新增事实，只能压缩已有事实。

---

## 目录结构

```
简历助手/
├── .env                      # API key（从 .env.example 复制）
├── data/
│   ├── profile.yaml          # 个人信息库：教育、绩点、排名、技能
│   ├── evidence/             # 自动生成：原子成就单元（YAML，可手改）
│   ├── jobs/                 # 自动生成：岗位 YAML
│   ├── applications/         # 投递快照
│   ├── output/<app_id>/      # ir.yaml + resume.typ + resume.pdf
│   └── resume.db             # 岗位池 / 投递记录 / 状态机 / 匹配缓存
├── workspace/
│   └── projects/             # ★ 你的资料放这里，一个项目一个文件夹
│       ├── 2024-校园二手交易平台/
│       └── 2025-社交推荐实验室课题/
├── resume_assistant/
│   ├── kb/                   # 文件夹扫描 + 证据抽取
│   ├── match/                # 召回 + 精排
│   ├── compose/              # 模板 + IR 组装
│   ├── validate/             # 校验闸门
│   ├── render/               # Typst 渲染
│   ├── store/                # SQLite
│   ├── jobs/                 # JD 抓取 + 解析
│   ├── pipeline.py           # 编排层（MCP 与 CLI 共用）
│   ├── mcp_server.py         # MCP 入口
│   └── cli.py                # CLI 入口
└── tests/
```

---

## 快速开始

### 1. 装依赖

```bash
cd C:/简历助手
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### 2. 填配置

```bash
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

编辑 `data/profile.yaml`：姓名、联系方式、教育、**绩点、排名**、技能池。

### 3. 放资料

把每个项目做成一个文件夹放进 `workspace/projects/`：

```
workspace/projects/2024-某项目/
├── README.md          # 项目说明（最重要，写清背景/你的工作/结果/数字）
├── 报告.pdf
└── 其他资料.docx
```

支持 `.md .txt .pdf .docx .csv .json .yaml`。PDF 建议放文字版，扫描件 P0 不支持 OCR。

### 4. 构建知识库

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli kb build
./.venv/Scripts/python.exe -m resume_assistant.cli kb stats
./.venv/Scripts/python.exe -m resume_assistant.cli kb list
```

第一次会把每个项目喂给 DeepSeek 抽取。之后资料没变就自动跳过（按文件指纹判断），不重复烧 token。

**抽完一定要人工看一遍 `data/evidence/*.yaml`**，改掉 LLM 抽错的地方。这一步值得花时间。

### 5. 加岗位并生成简历

```bash
# 手动粘贴（推荐）
./.venv/Scripts/python.exe -m resume_assistant.cli job add -k internship --file jd.txt --org 某公司 --title 后端开发实习生

# 或给官网链接自动抓
./.venv/Scripts/python.exe -m resume_assistant.cli job add -k internship --url https://example.com/job/123

# 保研套磁
./.venv/Scripts/python.exe -m resume_assistant.cli job add -k baoyan --file mentor_homepage.txt --mentor 李老师 --org 某大学计算机学院

./.venv/Scripts/python.exe -m resume_assistant.cli job list
./.venv/Scripts/python.exe -m resume_assistant.cli match job_xxx
./.venv/Scripts/python.exe -m resume_assistant.cli gen job_xxx --template academic
```

产物：`data/output/<app_id>/resume.pdf`（另有 `ir.yaml` 和 `resume.typ`，都可手改后重新编译）。

---

## 接入 MCP

### Claude Code / WorkBuddy / DeepSeek harness

在 MCP 配置里加：

```json
{
  "mcpServers": {
    "resume-assistant": {
      "command": "C:/简历助手/.venv/Scripts/python.exe",
      "args": ["-m", "resume_assistant.mcp_server"],
      "env": {
        "RESUME_ASSISTANT_ROOT": "C:/简历助手"
      }
    }
  }
}
```

WorkBuddy 的配置文件是 `~/.workbuddy-ai/mcp.json`。写完后需要在连接器管理页右上角的自定义连接器里点「信任」才会生效。

### 工具清单

| 工具 | 用途 |
|---|---|
| `kb_build` | 扫描项目文件夹并抽取证据（增量） |
| `kb_stats` / `kb_list_evidence` | 知识库概况 / 明细核对 |
| `job_add` | 新增岗位（手动文本或 URL 抓取） |
| `job_list` | 岗位池 |
| `job_match` | 匹配打分 + 缺口分析 |
| `resume_generate` | 生成 PDF（含校验闸门） |
| `resume_preview` | 只出 IR 不渲染，快速看结构 |
| `template_list` | 模板列表 |
| `app_list` / `app_detail` / `app_advance` | 投递记录与状态机 |
| `app_timeline` | 状态流转历史 |

接了 MCP 之后，你可以直接对 agent 说：

> 帮我给这个岗位生成简历，用 academic 模板：<粘贴 JD>

---

## 状态机

两条线共用一套状态枚举，转移规则在 `models.py::TRANSITIONS` 里硬编码。

**保研套磁**
```
draft → matched → composed → sent → replied → meeting → offer
```

**实习投递**
```
draft → matched → composed → sent → screening → interview → offer
```

两条线都可随时转 `rejected` / `withdrawn`。非法转移会被拒绝并提示当前可去的状态。

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli app list
./.venv/Scripts/python.exe -m resume_assistant.cli app advance app_xxx sent -n "已发邮件给李老师"
./.venv/Scripts/python.exe -m resume_assistant.cli app show app_xxx
```

---

## 校验闸门

`resume_generate` 会在渲染前跑两道纯代码校验：

| 闸 | 检查内容 | 结果 |
|---|---|---|
| 结构 | `evidence_id` 是否存在、bullet 是否为空 | 有错即拒绝渲染 |
| 溯源 | bullet 里的每个数字能否在证据 metrics / profile 里找到 | 有错即拒绝渲染 |
| 提示 | bullet 超长、结尾句号、技术词不在 skill_tags | 仅告警，不阻断 |

校验不过会自动带反馈重写一次；仍不过则返回错误清单，不产出 PDF。

这套东西的价值：**数字编造是简历最致命的错误，而它恰好能用纯规则拦住。**

---

## 模板

`resume_assistant/render/templates/*.yaml` 定义版式与槽位（section 顺序、条目上限、每条 bullet 数）。

- `single_column` — ATS 友好，适合实习网申
- `academic` — 前置研究兴趣与科研经历，适合保研套磁

加新模板只需复制一个 yaml 改字段，不用动代码。内容层（IR）与版式层完全解耦，同一份内容可渲染成多套版式。

---

## 已知限制（P0）

| 限制 | 说明 | 后续 |
|---|---|---|
| 无向量检索 | 只有 BM25 + 标签重叠，语义召回弱 | P1 接 bge-m3（接口已留 `encode_fn`） |
| 无 OCR | 扫描版 PDF / 图片资料读不到 | P1 接 PaddleOCR |
| 抓取能力弱 | JS 渲染页取不到正文，会引导改手动粘贴 | 视需要接 Playwright |
| 无 VSCode UI | 目前只有 CLI + MCP | P2 做 Webview 看板 + diff 确认 |
| 单语言 | 只支持中文 | — |

---

## 合规说明

`job_add --url` 抓取做了硬性限制：先查 robots.txt（Disallow 直接拒绝）、串行请求、1.5 秒间隔、带明确 UA、不登录不绕验证码。

**批量爬取招聘网站有法律风险，本工具不提供该能力。** 建议只用它抓单个公开页面，批量场景请手动粘贴。

---

## 自测

三个脚本，都不消耗 API 额度：

```bash
# 全链路冒烟（20 项断言：知识库→匹配→组稿→校验→渲染→状态机）
set RESUME_ASSISTANT_MOCK=1
./.venv/Scripts/python.exe tests/smoke.py

# MCP 协议握手（initialize / tools/list / tools/call）
./.venv/Scripts/python.exe tests/test_mcp.py

# 模板排版预览（假数据渲染两套模板，产出 data/output/_preview/*.pdf）
./.venv/Scripts/python.exe tests/preview_template.py
```

`RESUME_ASSISTANT_MOCK=1` 时 LLM 调用返回占位内容，只用来验证代码路径是否通，产出的简历没有实际意义。

### 当前 P0 已验证

| 项 | 状态 |
|---|---|
| 文件夹知识库扫描 + 指纹增量 | 通过 |
| 证据抽取（含 metrics 出处） | 通过（mock） |
| BM25 + 标签召回 | 通过 |
| 模板驱动的 IR 组装 | 通过 |
| 校验闸门拦截编造数字 / 虚假 evidence_id | 通过 |
| Typst 渲染中文 PDF（两套模板，单页） | 通过 |
| 双线状态机（保研 / 实习，含非法转移拒绝） | 通过 |
| MCP stdio 握手 + 13 个工具 | 通过 |

尚未用真实 API key 跑过，第一次跑请先 `kb build` 检查抽取质量。

