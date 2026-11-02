"""模板预览用的示例档案。

为什么把假数据放进包里而不是测试目录：`template-preview` 是给用户选模板用的命令，
它不能读用户的真实资料（没资料的人正是最需要看版式的人），也不能调模型（选个模板
花几千 token 太荒唐）。所以固定一份示例：结构齐全（教育 / 技能 / 实习 / 项目 /
论文 / 专利 / 家庭），每套模板都能把该有的段都撑起来。

数据本身不用多真实，但**必须覆盖所有段类型**——少一个段，预览就会漏掉那一段的排版，
用户看到的「一页纸」和真跑出来的不是一回事。
"""

from __future__ import annotations

from pathlib import Path

from ..models import (
    Education,
    Evidence,
    FamilyMember,
    Job,
    JobKind,
    MatchHit,
    MatchReport,
    Metric,
    Profile,
    Skills,
    Star,
)
# 用闸门同一个正则生成示例的数字池：两边口径一旦不一致，预览就会因为
# 「示例自己造的数字」挂红字，看着像模板坏了，实际是数据缺了一块。
from ..validate.linter import NUM_RE


_ROOT = Path(__file__).resolve().parents[2]

# 示例证件照。只有声明了 page.photo 的模板会用到；文件不在（比如只装了包没带
# tests/）就退化成「不画照片」——这本身也是要看的版式状态之一，不算失败。
DEMO_PHOTO = _ROOT / "tests" / "fixtures" / "photo_demo.png"


def _nums(*texts: str) -> list[str]:
    """示例证据的「原始资料数字池」。

    真实抽取时 `raw_numbers` 来自项目文件夹原文（kb/scanner.py::_collect_numbers），
    示例档案没有文件夹，所以把同一份事实文本里的数字补进去。不补的话，
    组稿把「23 个 REST 接口」写进 bullet，闸门会判它编造——那是闸门对，
    数据缺了一块，不该去改闸门。
    """
    return NUM_RE.findall(" ".join(t for t in texts if t))



def demo_inputs() -> tuple[Profile, Job, MatchReport, list[Evidence]]:
    profile = Profile(
        name="张三",
        gender="男",
        birth="2003-05",
        phone="138-0000-0000",
        email="zhangsan@example.com",
        city="北京",
        political="共青团员",
        native_place="山东济南",
        height="175cm",
        weight="65kg",
        health="良好",
        photo=str(DEMO_PHOTO),
        family=[
            FamilyMember(name="张建国", relation="父亲", workplace="某中学教师", political="中共党员"),
            FamilyMember(name="李秀兰", relation="母亲", workplace="某医院护士", political="群众"),
        ],
        education=[
            Education(
                school="某某大学",
                degree="本科",
                major="计算机科学与技术",
                period="2022-09 ~ 2026-06",
                gpa="3.82/4.0",
                gpa_rank="5/120",
                gpa_rank_pct="4.2%",
                lab="智能计算实验室",
                advisor="王教授",
                courses=["数据结构 95", "操作系统 92", "机器学习 94", "数据库系统 91"],
                honors=["国家奖学金（2024）", "校级三好学生（2023、2024）"],
            )
        ],
        skills=Skills(
            languages=["Python", "C++", "SQL"],
            frameworks=["PyTorch", "Spring Boot", "React"],
            tools=["Git", "Docker", "Linux"],
            english="CET-6 550",
        ),
        summary="关注推荐系统与图神经网络在真实业务场景下的落地，具备从数据处理到线上部署的完整工程经验。",
    )

    evs = [
        Evidence(
            id="ev_demo_01",
            kind="research",
            title="基于图神经网络的校园社交推荐",
            subtitle="实验室课题",
            org="某大学计算机学院实验室",
            role="核心成员",
            period="2025-03 ~ 至今",
            domain_tags=["推荐系统", "图神经网络"],
            skill_tags=["PyTorch", "LightGCN", "NGCF"],
            claim="冷启动用户前 7 天推荐点击率从 2.1% 提升到 3.4%",
            star=Star(
                situation="校园社交产品冷启动阶段新用户没有行为数据",
                task="在不引入额外特征的前提下提升冷启动推荐效果",
                action=["复现 LightGCN 与 NGCF 两个基线", "设计用户-物品二部图叠加社交子图的双通道聚合结构"],
                result="冷启动用户前 7 天点击率从 2.1% 提升到 3.4%",
            ),
            metrics=[
                Metric(name="点击率", value="3.4", unit="%", source="实验记录"),
                Metric(name="Recall@20", value="0.29", source="实验记录"),
            ],
        ),
        Evidence(
            id="ev_demo_02",
            kind="course",
            title="校园二手交易平台",
            subtitle="课程项目",
            org="某大学",
            role="后端负责人",
            period="2024-03 ~ 2024-06",
            domain_tags=["后端"],
            skill_tags=["Spring Boot", "Elasticsearch", "Redis"],
            claim="重构检索链路后平均响应时间从 1200ms 降至 180ms",
            star=Star(
                situation="课程项目要支撑 200 人同时在线交易",
                task="把商品检索从数据库 like 查询换成搜索引擎",
                action=["基于 Spring Boot 实现 23 个 REST 接口", "用 Elasticsearch 重构商品检索"],
                result="平均响应时间从 1200ms 降至 180ms",
            ),
            metrics=[
                Metric(name="响应时间", value="180", unit="ms", source="压测报告"),
                Metric(name="缓存命中率", value="87", unit="%", source="监控"),
            ],
            # 「23 个接口」「200 人同时在线」是动作里的数字，真实抽取时会落在
            # 文件夹原文的数字池里；示例没有文件夹，只能显式补
            raw_numbers=_nums("200 人同时在线", "23 个 REST 接口"),
        ),
        Evidence(
            id="ev_demo_03",
            kind="intern",
            title="推荐算法实习",
            subtitle="暑期实习",
            org="某科技公司",
            role="算法实习生",
            period="2025-07 ~ 2025-09",
            domain_tags=["推荐系统"],
            skill_tags=["Python", "Spark"],
            claim="负责召回通道的特征迭代，离线 AUC 提升 0.008",
            star=Star(
                situation="线上召回通道特征陈旧",
                task="补一批行为序列特征并评估收益",
                action=["用 Spark 重算近 30 天行为序列特征"],
                result="离线 AUC 提升 0.008",
            ),
            metrics=[Metric(name="AUC", value="0.008", source="离线评估")],
            raw_numbers=_nums("近 30 天行为序列特征"),
        ),
        Evidence(
            id="ev_demo_04",
            kind="paper",
            title="Dual-Channel Graph Aggregation for Cold-Start Recommendation",
            org="ACM RecSys 2025",
            role="第一作者",
            period="2025-09",
            claim="提出双通道图聚合结构，冷启动场景 Recall@20 提升 11.7%",
            metrics=[Metric(name="Recall@20 提升", value="11.7", unit="%", source="论文实验章节")],
        ),
        Evidence(
            id="ev_demo_05",
            kind="patent",
            title="一种基于社交子图的冷启动推荐方法",
            org="发明专利",
            role="第二发明人",
            period="2025-06",
            claim="已受理，把社交子图聚合步骤固化为可复用流程",
        ),
    ]

    job = Job(
        id="demo",
        kind=JobKind.BAOYAN,
        title="推荐系统方向学硕",
        org="某大学计算机学院",
        mentor="王老师",
        mentor_topics=["图神经网络", "推荐系统"],
        keywords=["推荐系统", "PyTorch", "图神经网络"],
    )
    report = MatchReport(
        job_id="demo",
        hits=[
            MatchHit(evidence_id=e.id, title=e.title, score=round(0.92 - i * 0.05, 2), reason="方向对口")
            for i, e in enumerate(evs)
        ],
    )
    return profile, job, report, evs
