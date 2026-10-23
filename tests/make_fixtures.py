"""生成刁难用的测试资料集。

覆盖真实场景里会遇到的边界：
- 格式：md / txt / docx / 文字层 PDF / 扫描件 PDF（无文字层）
- 规模：极短（无数字）/ 正常 / 超长（触发截断）
- 结构：规范 README / 散乱笔记 / 中英混排 / 空文件夹

产出目录：tests/sandbox/workspace/projects/
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "tests" / "sandbox"
PROJ = SANDBOX / "workspace" / "projects"


def _write(name: str, filename: str, content: str) -> None:
    d = PROJ / name
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(content, encoding="utf-8")


def _docx(name: str, filename: str, paragraphs: list[str]) -> None:
    import docx

    d = PROJ / name
    d.mkdir(parents=True, exist_ok=True)
    doc = docx.Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    table = doc.add_table(rows=2, cols=3)
    cells = [["指标", "优化前", "优化后"], ["平均耗时", "2.4s", "0.35s"]]
    for r, row in enumerate(cells):
        for c, val in enumerate(row):
            table.cell(r, c).text = val
    doc.save(str(d / filename))


def _pdf(name: str, filename: str, body: str) -> None:
    """用 typst 生成带文字层的 PDF。"""
    import typst

    d = PROJ / name
    d.mkdir(parents=True, exist_ok=True)
    typ = d / "_tmp.typ"
    typ.write_text(
        '#set page(paper: "a4", margin: 2cm)\n#set text(font: ("Microsoft YaHei"), size: 11pt, lang: "zh")\n'
        + body,
        encoding="utf-8",
    )
    typst.compile(str(typ), output=str(d / filename), root=str(d))
    typ.unlink()


def _scanned_pdf(name: str, filename: str) -> None:
    """生成无文字层的图片 PDF，模拟扫描件。"""
    from PIL import Image, ImageDraw

    d = PROJ / name
    d.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(img)
    for i in range(18):
        draw.rectangle([100, 120 + i * 80, 1100, 150 + i * 80], fill=(235, 235, 235))
    img.save(str(d / filename), "PDF", resolution=150.0)


def main() -> int:
    if PROJ.exists():
        shutil.rmtree(PROJ)
    PROJ.mkdir(parents=True, exist_ok=True)

    # 1 极短，没有任何数字 —— 检验 metrics 为空时是否仍能产出证据
    _write(
        "2023-数据结构课设-迷宫求解",
        "说明.txt",
        "大二上学期数据结构课程设计，用栈和队列实现迷宫求解，比较了深度优先和广度优先两种策略。"
        "我负责整体算法设计与代码实现。老师评价思路清晰。",
    )

    # 2 规范但信息密度高
    _write(
        "2024-校园二手交易平台",
        "README.md",
        """# 校园二手交易平台

## 背景
2024 年春季《软件工程》课程设计，4 人小组，我担任后端负责人。解决校内二手交易信息分散在多个微信群、成交效率低的问题。

## 我的工作
1. 设计并实现基于 Spring Boot 的后端服务，共 23 个 REST 接口，覆盖商品发布、搜索、下单、评价全链路。
2. 用 Elasticsearch 实现商品全文检索，支持按价格区间、成色、距离多维筛选，搜索平均响应时间从 1200ms 降到 180ms。
3. 引入 Redis 缓存热门商品列表与用户会话，缓存命中率 87%，数据库 QPS 从峰值 800 降到 210。
4. 设计基于令牌桶的接口限流，压测下单接口在 3200 QPS 下 P99 延迟保持在 95ms 以内。

## 结果
- 课程验收评分 96/100，获评班级最佳项目（4 个班共 160 人，排名第 1）。
- 系统在校园内试运行两周，累计注册用户 412 人，发布商品 683 件，成交 174 单。

## 技术栈
Java 17、Spring Boot 3.1、MySQL 8.0、Redis 7、Elasticsearch 8、Docker Compose、JMeter
""",
    )

    # 3 科研，中英混排
    _write(
        "2025-社交推荐实验室课题",
        "README.md",
        """# 基于图神经网络的校园社交推荐

2025 年 3 月加入实验室，跟随导师做社交推荐方向。

## 课题背景
现有协同过滤方法在冷启动用户上表现差，新用户前 7 天推荐点击率仅 2.1%。

## 我的工作
1. 复现 LightGCN 与 NGCF 两个基线，在自建校园社交数据集上完成对比实验，LightGCN 在 Recall@20 上比 NGCF 高 4.3 个百分点。
2. 提出在用户-物品二部图上叠加社交关系子图的双通道聚合结构，缓解冷启动问题。
3. 用 PyTorch 实现模型，在单张 RTX 3090 上训练，单次实验约 2.5 小时。
4. 设计消融实验验证社交子图的作用，去掉社交通道后冷启动用户 Recall@20 下降 11.7%。

## 结果
- 冷启动用户前 7 天推荐点击率从 2.1% 提升到 3.4%，相对提升 61.9%。
- 全量用户 Recall@20 达到 0.187，比最优基线 LightGCN 提升 6.2%。
- 论文初稿撰写中（第二作者）。

## 技术栈
Python、PyTorch 2.1、DGL、NumPy、Pandas、Matplotlib
""",
    )

    # 4 实习，docx 格式（含表格）
    _docx(
        "2025-某公司后端开发实习",
        "实习总结.docx",
        [
            "实习时间：2025 年 7 月至 2025 年 9 月，岗位为后端开发实习生，所在团队负责订单中心。",
            "主要工作一：重构订单状态机模块，把原本散落在 5 个 Service 里的状态流转收敛到统一的状态机，"
            "新增状态只需改配置，需求平均交付周期从 3 天缩短到 0.5 天。",
            "主要工作二：排查并修复订单超时未关闭的问题，定位到定时任务分片不均，改为一致性哈希分片后，"
            "超时订单占比从 0.8% 降到 0.02%。",
            "主要工作三：为订单查询接口增加多级缓存，接口 P99 从 480ms 降到 95ms。",
            "技术栈：Java、Spring Boot、MyBatis、MySQL、Redis、Kafka。",
        ],
    )

    # 5 竞赛，极短
    _write(
        "2025-蓝桥杯程序设计竞赛",
        "获奖说明.md",
        "2025 年第十六届蓝桥杯软件类 C/C++ 程序设计大学 A 组，重庆市一等奖。"
        "赛题共 10 题，我完成 8 题。",
    )

    # 6 超长文档 —— 触发截断逻辑
    long_body = "\n\n".join(
        f"## 实验记录 {i}\n本节记录文件系统实验中第 {i} 步的调试过程，"
        f"涉及 inode 分配、目录项缓存与块位图管理。测试用例 {i * 17} 全部通过，"
        f"平均块分配耗时 {i * 3} 微秒。"
        for i in range(1, 90)
    )
    _write(
        "2024-操作系统实验-文件系统实现",
        "实验报告.md",
        "# 简易文件系统实现\n\n基于 C 语言在模拟磁盘上实现了一个类 ext2 的简易文件系统，"
        "支持创建、删除、读写、目录遍历。\n\n" + long_body + "\n\n## 总结\n最终实现 12 个系统调用，"
        "通过全部 340 个测试用例，平均块分配耗时 45 微秒。",
    )

    # 7 开源贡献，纯英文
    _write(
        "2025-开源项目贡献",
        "CONTRIBUTIONS.md",
        """# Open Source Contributions

## fastjson2 (alibaba)
Contributed 3 merged PRs fixing JSON deserialization edge cases with nested generics.
Issue #2417 was reported by 40+ users; my fix reduced related bug reports by roughly 70%.

## DGL (dmlc)
Submitted a documentation patch and a test case for heterogeneous graph sampling.
The test increased coverage of `dgl.sampling` module from 78% to 84%.

## 技术栈
Java, Python, Git, GitHub Actions
""",
    )

    # 8 文字层 PDF
    _pdf(
        "2025-图像分类课程大作业",
        "报告.pdf",
        """#set par(leading: 0.8em)
= 基于 ResNet 的细粒度图像分类

== 任务
在 CUB-200-2011 数据集上做 200 类鸟类细粒度分类，训练集 5994 张。

== 方法
在 ResNet-50 基础上加入双线性注意力池化（BAP），替换全局平均池化，参数量仅增加 3.2%。

== 结果
- Top-1 准确率从 76.4% 提升到 83.1%。
- 在单张 V100 上训练 60 个 epoch，耗时约 4.5 小时。
- 消融实验显示注意力分支贡献了 4.7 个百分点的提升。

== 技术栈
Python, PyTorch, torchvision, NumPy
""",
    )

    # 9 扫描件 PDF —— 无文字层，应被跳过并记录在 skipped
    _scanned_pdf("2026-扫描件测试", "扫描版证书.pdf")

    # 10 空文件夹 —— 应被报为 empty
    (PROJ / "2026-空文件夹测试").mkdir(parents=True, exist_ok=True)

    print(f"生成完成：{PROJ}")
    for d in sorted(PROJ.iterdir()):
        files = [p.name for p in d.rglob("*") if p.is_file()]
        print(f"  {d.name:32s} {files or '(空)'}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
