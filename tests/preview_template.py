"""模板预览：不消耗 API 额度，用假数据渲染两套模板，用于检查排版效果。

运行：./.venv/Scripts/python.exe tests/preview_template.py
产物：data/output/_preview/<模板名>.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resume_assistant.compose.templates import list_templates, load_template  # noqa: E402
from resume_assistant.models import Bullet, IREntry, IRSection, ResumeIR  # noqa: E402
from resume_assistant.render.typst import render_pdf  # noqa: E402


def demo_ir() -> ResumeIR:
    return ResumeIR(
        job_id="demo",
        contact={
            "name": "张三",
            "gender": "男",
            "birth": "2003-05",
            "political": "共青团员",
            "phone": "138-0000-0000",
            "email": "zhangsan@example.com",
            "city": "北京",
        },
        sections=[
            IRSection(
                type="education",
                title="教育背景",
                entries=[
                    IREntry(
                        heading="某某大学 | 计算机科学与技术 | 本科",
                        meta="2022.09 – 2026.06 | GPA 3.82/4.0 | 排名 5/120",
                        bullets=[
                            Bullet(text="核心课程：数据结构 95、操作系统 92、机器学习 94、数据库系统 91", evidence_id="profile"),
                            Bullet(text="在校荣誉：国家奖学金（2024）、校级三好学生（2023、2024）", evidence_id="profile"),
                        ],
                    )
                ],
            ),
            IRSection(
                type="research_interest",
                title="研究兴趣",
                entries=[
                    IREntry(
                        heading="",
                        meta="",
                        bullets=[
                            Bullet(
                                text="研究兴趣集中于图神经网络、社交推荐、冷启动问题。关注推荐系统与图神经网络在真实业务场景下的落地，具备从数据处理到线上部署的完整工程经验。",
                                evidence_id="profile",
                            )
                        ],
                    )
                ],
            ),
            IRSection(
                type="experience",
                title="科研与实习经历",
                entries=[
                    IREntry(
                        heading="基于图神经网络的校园社交推荐",
                        meta="核心成员 | 2025.03 – 至今 | 某大学计算机学院实验室",
                        bullets=[
                            Bullet(text="复现 LightGCN 与 NGCF 两个基线，在自建校园社交数据集上完成对比实验", evidence_id="ev_1"),
                            Bullet(text="提出用户-物品二部图叠加社交子图的双通道聚合结构，缓解冷启动问题", evidence_id="ev_1"),
                            Bullet(text="设计消融实验验证社交通道作用，去掉后冷启动用户 Recall@20 下降 11.7%", evidence_id="ev_1"),
                            Bullet(text="冷启动用户前 7 天推荐点击率从 2.1% 提升到 3.4%，相对提升 61.9%", evidence_id="ev_1"),
                        ],
                    )
                ],
            ),
            IRSection(
                type="projects",
                title="项目经历",
                entries=[
                    IREntry(
                        heading="校园二手交易平台",
                        meta="后端负责人 | 2024.03 – 2024.06 | 课程项目",
                        bullets=[
                            Bullet(text="基于 Spring Boot 实现 23 个 REST 接口，覆盖商品发布、搜索、下单、评价全链路", evidence_id="ev_2"),
                            Bullet(text="用 Elasticsearch 重构商品检索，平均响应时间从 1200ms 降至 180ms", evidence_id="ev_2"),
                            Bullet(text="引入 Redis 缓存热门列表与会话，缓存命中率 87%，数据库 QPS 从 800 降至 210", evidence_id="ev_2"),
                        ],
                    )
                ],
            ),
            IRSection(
                type="skills",
                title="专业技能",
                entries=[
                    IREntry(
                        heading="",
                        meta="",
                        bullets=[
                            Bullet(text="编程语言：Python、C++、SQL", evidence_id="profile"),
                            Bullet(text="框架/库：PyTorch、Spring Boot、React", evidence_id="profile"),
                            Bullet(text="工具/环境：Git、Docker、Linux", evidence_id="profile"),
                            Bullet(text="英语：CET-6 550", evidence_id="profile"),
                        ],
                    )
                ],
            ),
            IRSection(
                type="honors",
                title="荣誉奖项",
                entries=[
                    IREntry(
                        heading="",
                        meta="",
                        bullets=[
                            Bullet(text="国家奖学金（2024）", evidence_id="profile"),
                            Bullet(text="校级三好学生（2023、2024）", evidence_id="profile"),
                        ],
                    )
                ],
            ),
        ],
    )


def main() -> int:
    out_root = ROOT / "data" / "output" / "_preview"
    base = demo_ir()
    for t in list_templates():
        template = load_template(t["key"])
        allowed = {s.type for s in template.sections}
        ir = base.model_copy(deep=True)
        ir.sections = [s for s in ir.sections if s.type in allowed]
        out = out_root / t["key"]
        typ, pdf = render_pdf(ir, template, out)
        print(f"{t['key']:16s} -> {pdf or 'PDF 编译失败，见 render_error.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
