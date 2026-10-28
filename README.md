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
| 套磁信 | 保研专用：读导师方向 + 引用证据写信 | LLM 受限生成 + 代码查编造 |
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
│   ├── projects/             # ★ 你的资料放这里，一个项目一个文件夹
│   └── samples/              # 两个示例项目，想试跑时复制进 projects/
├── resume_assistant/
│   ├── kb/                   # 文件夹扫描 + 证据抽取 + 校对
│   ├── match/                # 召回 + 精排
│   ├── compose/              # 模板 + IR 组装 + 套磁信 + 开场白
│   ├── validate/             # 校验闸门 + 资格校验
│   ├── render/               # Typst 渲染
│   ├── store/                # SQLite
│   ├── jobs/                 # JD 抓取 + 解析
│   ├── board.py              # 投递看板
│   ├── doctor.py             # 环境自检
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

### 3. 先自检一遍

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli doctor
```

```
环境自检 · C://简历助手
────────────────────────────────────────────────────────
  ✓ Python 3.13.14
  ✓ 依赖 13/13
  ✓ DEEPSEEK_API_KEY — 已配置（sk-881…，共 35 位）
  ✗ profile 姓名未填写 — 当前是「张三」
      → 改成你自己的名字
  ! 教育经历仍是示例数据 — 某某大学
      → 改成你的学校/专业
  ✓ 项目文件夹 2 个
  ✓ Typst 中文渲染
────────────────────────────────────────────────────────
1 项必须处理，1 项建议处理。
```

查的都是**会悄悄做错**而不是会崩溃的事：少装一个 python-pptx，表现是「PPT 里的资料没进知识库」；profile 还是示例数据，表现是「简历上写着张三」；缺中文字体，Typst 不报错但 PDF 里全是方框。

### 4. 放资料

把每个项目做成一个文件夹放进 `workspace/projects/`：

```
workspace/projects/2024-某项目/
├── README.md          # 项目说明（最重要，写清背景/你的工作/结果/数字）
├── 报告.pdf
└── 其他资料.docx
```

支持格式：`.md .txt .pdf .docx .pptx .xlsx .csv .json .yaml`

`workspace/projects/` 默认是空的。想先看看效果，把示例复制过来试跑（跑通后记得删掉再放自己的，否则示例项目会和你的真实经历一起抢简历上的位置）：

```bash
cp -r workspace/samples/2024-校园二手交易平台 workspace/projects/
```

两个容易踩的坑，已经处理好了：

- **GBK 编码的 txt/csv**：中文 Windows 导出的文本基本都是 GBK，按 utf-8 读会静默变乱码。现在按 BOM → utf-8 → gb18030 → big5 依次试，读不出来会明确报错而不是塞一堆乱码给模型。
- **读不出来的文件会被列出来，而不是悄悄跳过**：老版 `.doc`（请另存为 docx）、扫描件 PDF、截图（都没有文字层）、压缩包。跑 `kb scan` 就能看到每个文件的状态和原因。

### 5. 先扫一遍，再决定抽不抽

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli kb scan
./.venv/Scripts/python.exe -m resume_assistant.cli kb scan -v    # 列出每个文件
```

```
文件夹                               文件      字符   状态
────────────────────────────────────────────────────────────────────
2024-校园二手交易平台                      2    8421   待抽取
2026-脏资料测试                         3     366   待抽取
2026-扫描件测试                         0       0   读不到任何文字

读不出来的文件（3 个）：
  ✗ 2026-扫描件测试/扫描版证书.pdf
      PDF 没有文字层（扫描件/图片版），P0 不支持 OCR
  ✗ 2026-脏资料测试/旧版报告.doc
      老版 .doc 打不开，请用 Word 另存为 .docx 后重试
  ✗ 2026-脏资料测试/界面截图.png
      图片没有文字层，P0 不支持 OCR（重要内容请手动写进 md）
```

扫描不花 token。**先看这一步，比抽完对着「证据不对」发懵强。**

### 6. 构建知识库

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli kb build
./.venv/Scripts/python.exe -m resume_assistant.cli kb stats
./.venv/Scripts/python.exe -m resume_assistant.cli kb list
```

每个文件夹一次模型调用，默认 3 个并发（`-j 1` 改串行）。资料没变就按文件指纹跳过，不重复烧 token。
一个字都没读到的文件夹不会送去抽取——省 token，也会在报告里单列出来。

**抽完一定要校对。** 抽取是模型做的，错法就那么几种，而且都能用规则查出来：

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli kb review
```

它会列出每条可疑证据和具体问题，同时生成 `data/review.html` 卡片页（几十条一眼扫完）。
改字段不用开编辑器：

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli kb fix ev_2024-xxx_00 -k course -p "2024.03 ~ 2024.06"
```

查的是这七类（纯规则，不花 token）：

| 检查项 | 为什么重要 |
|---|---|
| `kind` 和原文对不上 | 课程大作业被标成 research，会直接污染保研简历的「科研经历」段 |
| `period` 为空 / 没有年份 | 简历上会显示「时间未填」，或时间轴错乱 |
| 指标没有 `source` | 数字溯源闸门无法判断它是真数据还是编的 |
| 两条证据标题高度相似 | 同一个项目被两个文件夹重复抽取，简历上出现两条几乎一样的经历 |
| `claim` 少于 12 字 | 信息量太低，组稿时模型只能靠编来凑 |
| 没有 `skill_tags` | 检索只剩 BM25，召回明显变差 |
| 没有 `raw_excerpt` | 无法核对这条是不是编的 |

### 7. 加岗位并生成简历

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
./.venv/Scripts/python.exe -m resume_assistant.cli letter job_xxx     # 保研套磁信
./.venv/Scripts/python.exe -m resume_assistant.cli pitch job_xxx      # 实习开场白（站内信 + 邮件）
```

产物：`data/output/<app_id>/resume.pdf`（另有 `ir.yaml` 和 `resume.typ`，都可手改后重新编译）。

### 8. 改一两句话再出 PDF

生成的措辞不可能句句合意。`ir.yaml` 就是给你改的——删条目、调顺序、换说法都行，改完不用重新调模型：

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli rerender app_xxx
./.venv/Scripts/python.exe -m resume_assistant.cli rerender app_xxx -T academic   # 顺手换个版式
```

`rerender` 只做「重新校验 + 重新排版」，不花 token。校验有错也照常出 PDF——
你是主动改的，应该立刻看见结果，而不是被闸门挡住；错误会原样打印出来提醒你处理。

### 9. 看板：谁卡住了、谁快截止了

投了十几家之后，`app list` 那张表已经看不出重点。

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli board --open
```

```
投递看板 · 2026-10-28 · 共 3 条

  实习：建档 2 → 投出 1 → 回复 0 → 面试 0 → Offer 0　（回复率 0%｜面试率 0%）
  保研：建档 1 → 投出 0

需要处理：
  ! 快截止岗位　还有 2 天截止，简历还没投出去
  * 投出去很久　投出去 11 天没动静，可以跟进一次
  ! 已经过期　截止日期 2026-10-01 已过 27 天，还停在「已建档」
```

分四栏（待办 / 已投出 / 进行中 / 已结束），同时写出 `data/board.html`。判断全是规则算的：

| 提醒 | 触发条件 |
|---|---|
| 快截止 | 7 天内截止但简历还没做完 / 3 天内还没投 |
| 已过期 | 截止日期已过，还停在待办列 |
| 该跟进 | 已投出 / 简历筛超过 7 天没动静 |
| 生成了没投 | 简历生成 3 天以上还没投出去 |
| 建档没动 | 建档 3 天还没跑 match |

截止日期能认 `2026-11-01`、`2026/11/1`、`11月1日`（跨年自动按下一年）、`20261101`；
写「长期有效」「招满即止」的不会被硬猜成某个日期。

顺手记一笔，不动状态：

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli app note app_xxx "HR 说下周安排一面"
```

备注只进时间线。如果它进了 `history`，最后一条时间戳就会被刷新，
「投出去十天没动静」的提醒永远不会触发。

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
| `doctor` | 环境自检（依赖 / key / profile / 字体） |
| `kb_build` | 扫描项目文件夹并抽取证据（增量） |
| `kb_scan` | 只扫描不抽取，看每个文件夹读到了什么 |
| `kb_stats` / `kb_list_evidence` | 知识库概况 / 明细核对 |
| `kb_review` | 规则校对抽取结果，出卡片页 |
| `kb_fix` | 就地改一条证据的字段 |
| `kb_prune` | 清理文件夹改名/删除后遗留的幽灵证据 |
| `job_add` | 新增岗位（手动文本或 URL 抓取） |
| `job_list` | 岗位池 |
| `job_match` | 匹配打分 + 缺口分析 |
| `resume_generate` | 生成 PDF（含校验闸门） |
| `resume_preview` | 只出 IR 不渲染，快速看结构 |
| `resume_rerender` | 手改 ir.yaml 后重新出 PDF（不调模型） |
| `cover_letter_generate` | 保研套磁信（Markdown，仅 baoyan 岗位） |
| `pitch_generate` | 实习开场白（站内信 + 邮件两版，仅 internship 岗位） |
| `template_list` | 模板列表 |
| `app_list` / `app_detail` / `app_advance` | 投递记录与状态机 |
| `app_timeline` | 状态流转历史 |
| `app_note` | 记一笔备注（不改状态，只进时间线） |
| `app_board` | 投递看板：卡点、截止提醒、跟进提醒 |

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

## 硬性资格校验

技术匹配做得再好，资格不符也是白投。`job_match` 会额外跑一次规则校验：

| 检查项 | 判定方式 | 级别 |
|---|---|---|
| 届别 | 从 JD 提取「2027 届」/「2027 年毕业」，与教育经历推算的毕业年份比对 | fail |
| 学历 | 提取「本科/硕士/博士 + 及以上」，与最高学历比对 | fail |
| 专业 | 提取专业关键词，与 major 模糊匹配 | warn |
| 到岗时间 / 实习时长 | 无法自动判定，仅提示自行确认 | info |

出现 `fail` 会置 `blocked=true` 并在 CLI 输出里高亮，但**不阻止生成简历**——你可能想投，只是必须知道这件事。

用规则而非模型：毕业年份、学历层次有明确标准，而模型只看技术对口度，不会主动发现「岗位要 2027 届，你 2026 年毕业」这种冲突。

---

## 套磁信

`cover_letter_generate` 只对 `baoyan` 岗位开放，实习岗位直接拒绝——硬套一封「尊敬的 HR」的信没有意义。

写法不是自由发挥，而是把**导师方向 + 最相关的前 3 条证据**喂进去，让模型做「连线」而不是「创作」。

三条硬约束，代码和 prompt 双重兜底：

| 约束 | 为什么 | 怎么拦 |
|---|---|---|
| 不提没做过的论文 | 「拜读过您的《XX》」是最容易脱口而出的假话 | 正则抽出正文里所有 `《...》`，与 JD 原文 + 导师方向比对，对不上就重写 |
| 不提不存在的附件 | 系统只产出简历，正文却说「附上成绩单和课题报告」 | 词表黑名单（成绩单/推荐信/获奖证书/作品集/论文全文），命中即重写 |
| 不写空话吹捧 | 「您的研究享誉国际」这类句子让信立刻贬值 | prompt 要求每个夸奖必须挂一个具体方向或证据 |

另外还有两处纯代码修正，属于「模型不该被信任的细节」：

- **称呼**：`mentor` 字段可能已经是「张老师」，直接拼会变成「尊敬的张老师老师：」。`_salutation` 先判断是否已带职称后缀（老师/教授/研究员/院士），`_fix_salutation` 再兜一层叠字替换。
- **落款**：只从 `profile.yaml` 取真实姓名、学校、专业、电话、邮箱，不让模型自己编联系方式。

产物是 `data/output/<app_id>/cover_letter.md`，正文通常 400–700 字。检查不通过会自动带反馈重写一次，仍不过则返回警告清单——**信还是会给你，但风险摆在你面前**。

---

## 实习开场白

保研有套磁信，实习却没有对称的东西。真正要写的是站内信那两句话、或邮件正文那两百字——看着比简历简单，其实最容易翻车：

```bash
./.venv/Scripts/python.exe -m resume_assistant.cli pitch job_xxx
```

一次生成两版，因为长度限制是真实存在的：

| 版本 | 上限 | 用在哪 |
|---|---|---|
| `short` | 80 字 | 站内信 / 打招呼，一屏就截断 |
| `full` | 220 字 | 邮件正文，三段：自我介绍 / 最对口的 1-2 段经历 / 可实习时间与联系方式 |

四条检查，前两条会自动带反馈重写一次：

| 检查 | 触发条件 |
|---|---|
| 可实习时间编造 | `profile.yaml` 的 `internship_available` 为空，正文却出现「每周 4 天」「可实习 6 个月」 |
| 数字无出处 | 正文里的数字在证据、指标、JD、profile 里都找不到 |
| 空话 | 「贵司平台广阔」「非常期待加入」这类删掉后信息量不变的句子 |
| 篇幅 | 站内信 > 90 字，或邮件 > 260 字 |

**为什么专门盯「可实习时间」**：这是模型最爱顺手补的一句，也是面试第一句就会被追问的内容。`profile.yaml` 里留空，它就不许写；要写就先把真实时间填上。

产物 `data/output/<app_id>/pitch.md`，两版并列，直接复制粘贴。

---

## 模板

`resume_assistant/render/templates/*.yaml` 定义版式与槽位（section 顺序、条目上限、每条 bullet 数）。

- `single_column` — ATS 友好，适合实习网申
- `academic` — 前置研究兴趣与科研经历，适合保研套磁

加新模板只需复制一个 yaml 改字段，不用动代码。内容层（IR）与版式层完全解耦，同一份内容可渲染成多套版式。

`include_kinds` 控制该 section 接收哪些类型的证据：

- `single_column` 的 projects **不设** `include_kinds`，所有经历统一进这一段（实习模板不区分科研与项目）
- `academic` 的 experience 严格限定 `[intern, research]`，**没有对应经历时整段不出现**，而不是把课程项目塞进去冒充科研经历
- `academic` 的 projects 接收 `[project, course, award, activity]`

### 字号自适应

内容偏少时（比如只有 3 个项目）用固定字号会把 A4 填成半页空白，看着像没做完。

`render_pdf` 因此做了一次「试排」：以模板字号为起点，依次试 1.20× / 1.10× / 1.07× 三档，
取**页数不变的前提下最大的那个**。标题、姓名、日期都按基准字号等比推导，不会出现「正文变大、标题不变」的错位。
试排产物编译完即删，最终只留 `resume.typ` + `resume.pdf`。

实测：单栏模板填充率 59% → 70%，学术模板 75% → 89%，页数都是 1 页。
上限由模板的 `page.max_scale` 控制（单栏 1.2，学术 1.15），设为 `1.0` 即关闭。

代价是每次生成多 1–3 次 Typst 编译（每次约 100–300ms），可忽略。

---

## 已知限制（P0）

| 限制 | 说明 | 后续 |
|---|---|---|
| 无向量检索 | 只有 BM25 + 标签重叠，语义召回弱 | P1 接 bge-m3（接口已留 `encode_fn`） |
| 无 OCR | 扫描版 PDF / 截图读不到，`kb scan` 会明确列出 | P1 接 PaddleOCR |
| 老版 Office | `.doc` / `.xls` / `.ppt` 打不开，需另存为新格式（会提示） | 不打算支持，二进制格式太脆 |
| 抓取能力弱 | JS 渲染页取不到正文，会引导改手动粘贴 | 视需要接 Playwright |
| 无 VSCode UI | 目前只有 CLI + MCP | P2 做 Webview 看板 + diff 确认 |
| 单语言 | 只支持中文 | — |

---

## 合规说明

`job_add --url` 抓取做了硬性限制：先查 robots.txt（Disallow 直接拒绝）、串行请求、1.5 秒间隔、带明确 UA、不登录不绕验证码。

**批量爬取招聘网站有法律风险，本工具不提供该能力。** 建议只用它抓单个公开页面，批量场景请手动粘贴。

---

## 自测

五个脚本，前三个不消耗 API 额度：

```bash
# 全链路冒烟（95 项断言：资料接入→知识库→匹配→组稿→校验→渲染→状态机→校对→看板→开场白）
set RESUME_ASSISTANT_MOCK=1
./.venv/Scripts/python.exe tests/smoke.py

# MCP 协议握手（initialize / tools/list / tools/call）
./.venv/Scripts/python.exe tests/test_mcp.py

# 模板排版预览（假数据渲染两套模板，产出 data/output/_preview/*.pdf）
./.venv/Scripts/python.exe tests/preview_template.py

# 生成刁难用的测试资料（10 个文件夹：docx/PDF/扫描件/超长/空目录/无数字）
./.venv/Scripts/python.exe tests/make_fixtures.py

# 真实 API 端到端（沙箱隔离，不碰你的真实 data/）
./.venv/Scripts/python.exe tests/e2e_real.py
```

`smoke.py` 与 `e2e_real.py` 都用 `RESUME_ASSISTANT_DATA` / `RESUME_ASSISTANT_WORKSPACE` 指向 `tests/sandbox/`。
这层隔离是必须的：`smoke.py` 会 `kb build --force`，若指向真实 `data/` 就会用 mock 数据覆盖掉你辛苦抽出来的证据。

### 当前已验证

| 项 | 状态 |
|---|---|
| 文件夹知识库扫描 + 指纹增量 | 通过 |
| 多格式抽取：md / txt / docx（含表格）/ 文字层 PDF | 通过 |
| 扫描件 PDF（无文字层）与空文件夹 | 正确跳过并报告 |
| 超长文档截断 | 通过 |
| 无数字资料（metrics 为空） | 通过，不报错 |
| BM25 + 标签召回 | 通过 |
| 模板驱动的 IR 组装（含 include_kinds 选材） | 通过 |
| bullet 重复检测与去重 | 通过 |
| 校验闸门拦截编造数字 / 虚假 evidence_id | 通过 |
| 数字溯源不误杀型号数字（V100）与未入 metrics 的真实数字 | 通过 |
| Typst 渲染中文 PDF（两套模板，单页） | 通过 |
| 字号自适应（页数不变前提下放大，等比推导标题） | 通过 |
| 投递看板：截止提醒 / 跟进提醒 / 四栏分列 | 通过 |
| 岗位与投递编号不撞车（同秒同公司两条互不覆盖） | 通过 |
| 手改 IR 后重渲染（不调模型，校验照跑） | 通过 |
| 知识库校对（kind 标错 / 时间缺失 / 重复项目 / 指标无出处） | 通过 |
| 资料接入：GBK / UTF-16 / BOM 解码，pptx / xlsx 抽取 | 通过 |
| 读不出的文件带原因上报（.doc / 扫描件 / 截图），锁文件静默忽略 | 通过 |
| 并发抽取（3 线程，索引不互相覆盖） | 通过 |
| 环境自检（依赖 / 示例 profile / 中文字体） | 通过 |
| 幽灵证据检测与清理（文件夹改名后不留残影） | 通过 |
| 冒烟测试沙箱隔离（不写真实 data/） | 通过 |
| 双线状态机（保研 / 实习，含非法转移拒绝） | 通过 |
| 硬性资格校验（届别 / 学历 / 专业） | 通过 |
| 套磁信（称呼不叠字、不虚构附件、不编造论文） | 通过 |
| 实习开场白（长度上限、可实习时间不许编、空话识别） | 通过 |
| MCP stdio 握手 + 23 个工具 | 通过 |

真实 API 端到端跑通 5 个岗位（3 实习 + 2 保研），简历全部 0 错误生成，2 封套磁信 0 编造风险。


