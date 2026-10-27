#set page(paper: "a4", margin: (x: 1.8cm, y: 1.5cm))
#set text(font: ("Microsoft YaHei", "SimHei", "Source Han Sans SC", "Noto Sans CJK SC", "PingFang SC"), size: 12.07pt, lang: "zh", region: "cn")
#set par(leading: 0.72em, justify: false, spacing: 0.9em)
#set list(indent: 0.8em, marker: [·], spacing: 0.42em)
#set text(fill: rgb("#1a1a1a"))

#align(center)[#text(size: 22.933pt, weight: "bold")[张三]]
#align(center)[#v(-2pt)#text(size: 10.863pt, fill: rgb("#555555"))[男  ·  2003-05 生  ·  共青团员  ·  138-0000-0000  ·  zhangsan\@example.com  ·  北京]]

#v(2pt)
#text(size: 13.8805pt, weight: "bold", fill: rgb("#4a3b78"))[教育背景]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#4a3b78"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[某某大学 | 计算机科学与技术 | 本科]], align(right)[#text(size: 10.863pt, fill: rgb("#555555"))[2022.09 – 2026.06 | GPA 3.82/4.0 | 排名 5/120]])

- 核心课程：数据结构 95、操作系统 92、机器学习 94、数据库系统 91
- 在校荣誉：国家奖学金（2024）、校级三好学生（2023、2024）

#v(2pt)
#text(size: 13.8805pt, weight: "bold", fill: rgb("#4a3b78"))[研究兴趣]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#4a3b78"))#v(3pt)

- 研究兴趣集中于图神经网络、社交推荐、冷启动问题。关注推荐系统与图神经网络在真实业务场景下的落地，具备从数据处理到线上部署的完整工程经验。

#v(2pt)
#text(size: 13.8805pt, weight: "bold", fill: rgb("#4a3b78"))[科研与实习经历]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#4a3b78"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[基于图神经网络的校园社交推荐]], align(right)[#text(size: 10.863pt, fill: rgb("#555555"))[核心成员 | 2025.03 – 至今 | 某大学计算机学院实验室]])

- 复现 LightGCN 与 NGCF 两个基线，在自建校园社交数据集上完成对比实验
- 提出用户-物品二部图叠加社交子图的双通道聚合结构，缓解冷启动问题
- 设计消融实验验证社交通道作用，去掉后冷启动用户 Recall\@20 下降 11.7%
- 冷启动用户前 7 天推荐点击率从 2.1% 提升到 3.4%，相对提升 61.9%

#v(2pt)
#text(size: 13.8805pt, weight: "bold", fill: rgb("#4a3b78"))[项目经历]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#4a3b78"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[校园二手交易平台]], align(right)[#text(size: 10.863pt, fill: rgb("#555555"))[后端负责人 | 2024.03 – 2024.06 | 课程项目]])

- 基于 Spring Boot 实现 23 个 REST 接口，覆盖商品发布、搜索、下单、评价全链路
- 用 Elasticsearch 重构商品检索，平均响应时间从 1200ms 降至 180ms
- 引入 Redis 缓存热门列表与会话，缓存命中率 87%，数据库 QPS 从 800 降至 210

#v(2pt)
#text(size: 13.8805pt, weight: "bold", fill: rgb("#4a3b78"))[专业技能]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#4a3b78"))#v(3pt)
#v(5pt)
#block(spacing: 0.62em)[编程语言：Python、C++、SQL]
#block(spacing: 0.62em)[框架/库：PyTorch、Spring Boot、React]
#block(spacing: 0.62em)[工具/环境：Git、Docker、Linux]
#block(spacing: 0.62em)[英语：CET-6 550]

#v(2pt)
#text(size: 13.8805pt, weight: "bold", fill: rgb("#4a3b78"))[荣誉奖项]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#4a3b78"))#v(3pt)

- 国家奖学金（2024）
- 校级三好学生（2023、2024）
