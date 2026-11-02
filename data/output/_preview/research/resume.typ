#set page(paper: "a4", margin: (x: 1.8cm, y: 1.5cm))
#set text(font: ("Microsoft YaHei", "SimHei", "Source Han Sans SC", "Noto Sans CJK SC", "PingFang SC"), size: 11.76pt, lang: "zh", region: "cn")
#set par(leading: 0.72em, justify: false, spacing: 0.9em)
#set list(indent: 0.8em, marker: [·], spacing: 0.42em)
#set text(fill: rgb("#1a1a1a"))

#align(center)[#text(size: 22.344pt, weight: "bold")[张三]]
#align(center)[#v(-2pt)#text(size: 10.584pt, fill: rgb("#555555"))[男  ·  2003-05 生  ·  共青团员  ·  138-0000-0000  ·  zhangsan\@example.com  ·  北京]]

#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[教育背景]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[某某大学 | 计算机科学与技术 | 本科]], align(right)[#text(size: 10.584pt, fill: rgb("#555555"))[GPA 3.82/4.0 | 排名 5/120（前 4.2%） | 2022.09 – 2026.06]])

- 实验室：智能计算实验室　导师：王教授
- 核心课程：数据结构 95、操作系统 92、机器学习 94、数据库系统 91

#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[研究兴趣]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)
#v(5pt)
#block(spacing: 0.62em)[研究兴趣集中于图神经网络、推荐系统，希望结合已有工程经验继续深入。]

#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[论文与专利]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[Dual-Channel Graph Aggregation for Cold-Start Recommendation]], align(right)[#text(size: 10.584pt, fill: rgb("#555555"))[ACM RecSys 2025 | 第一作者 | 2025.09]])
#v(2pt)
#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[一种基于社交子图的冷启动推荐方法]], align(right)[#text(size: 10.584pt, fill: rgb("#555555"))[发明专利 | 第二发明人 | 2025.06]])
#v(2pt)
#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[科研与实习经历]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[基于图神经网络的校园社交推荐]], align(right)[#text(size: 10.584pt, fill: rgb("#555555"))[核心成员 | 2025.03 – 至今 | 某大学计算机学院实验室]])

- 冷启动用户前 7 天推荐点击率从 2.1% 提升到 3.4%
- 复现 LightGCN 与 NGCF 两个基线
- 设计用户-物品二部图叠加社交子图的双通道聚合结构

#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[推荐算法实习]], align(right)[#text(size: 10.584pt, fill: rgb("#555555"))[算法实习生 | 2025.07 – 2025.09 | 某科技公司]])

- 负责召回通道的特征迭代，离线 AUC 提升 0.008
- 用 Spark 重算近 30 天行为序列特征

#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[项目经历]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[校园二手交易平台]], align(right)[#text(size: 10.584pt, fill: rgb("#555555"))[后端负责人 | 2024.03 – 2024.06 | 某大学]])

- 重构检索链路后平均响应时间从 1200ms 降至 180ms
- 基于 Spring Boot 实现 23 个 REST 接口
- 用 Elasticsearch 重构商品检索

#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[专业技能]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)
#v(5pt)
#block(spacing: 0.62em)[编程语言：Python、C++、SQL]
#block(spacing: 0.62em)[框架/库：PyTorch、Spring Boot、React]
#block(spacing: 0.62em)[工具/环境：Git、Docker、Linux]
#block(spacing: 0.62em)[英语：CET-6 550]

#v(2pt)
#text(size: 13.524pt, weight: "bold", fill: rgb("#1f3a5f"))[荣誉奖项]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#1f3a5f"))#v(3pt)

- 国家奖学金（2024）
- 校级三好学生（2023、2024）
