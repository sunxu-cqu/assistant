#set page(paper: "a4", margin: (x: 1.5cm, y: 1.3cm))
#set text(font: ("Microsoft YaHei", "SimHei", "Source Han Sans SC", "Noto Sans CJK SC", "PingFang SC"), size: 10.26pt, lang: "zh", region: "cn")
#set par(leading: 0.72em, justify: false, spacing: 0.9em)
#set list(indent: 0.8em, marker: [·], spacing: 0.42em)
#set text(fill: rgb("#1a1a1a"))

#align(center)[#text(size: 19.494pt, weight: "bold")[张三]]
#align(center)[#v(-2pt)#text(size: 9.234pt, fill: rgb("#555555"))[男  ·  2003-05 生  ·  共青团员  ·  138-0000-0000  ·  zhangsan\@example.com  ·  北京]]

#v(2pt)
#text(size: 11.799pt, weight: "bold", fill: rgb("#2b4a6f"))[教育背景]
#v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#2b4a6f"))#v(3pt)
#grid(columns: (1fr, auto), [#text(weight: "bold")[某某大学 | 计算机科学与技术 | 本科]], align(right)[#text(size: 9.234pt, fill: rgb("#555555"))[2022.09 – 2026.06 | GPA 3.82/4.0 | 排名 5/120（前 4.2%）]])

- 核心课程：数据结构 95、操作系统 92、机器学习 94、数据库系统 91

#grid(
  columns: (1fr, 1.75fr),
  column-gutter: 1.5em,
  [
    #v(2pt)
    #text(size: 11.799pt, weight: "bold", fill: rgb("#2b4a6f"))[专业技能]
    #v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#2b4a6f"))#v(3pt)
    #v(5pt)
    #block(spacing: 0.62em)[编程语言：Python、C++、SQL]
    #block(spacing: 0.62em)[框架/库：PyTorch、Spring Boot、React]
    #block(spacing: 0.62em)[工具/环境：Git、Docker、Linux]
    #block(spacing: 0.62em)[英语：CET-6 550]

    #v(2pt)
    #text(size: 11.799pt, weight: "bold", fill: rgb("#2b4a6f"))[荣誉奖项]
    #v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#2b4a6f"))#v(3pt)

    - 国家奖学金（2024）
    - 校级三好学生（2023、2024）

  ],
  [
    #v(2pt)
    #text(size: 11.799pt, weight: "bold", fill: rgb("#2b4a6f"))[实习经历]
    #v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#2b4a6f"))#v(3pt)
    #block[#text(weight: "bold")[基于图神经网络的校园社交推荐]]
    #block[#text(size: 9.234pt, fill: rgb("#555555"))[核心成员 | 2025.03 – 至今 | 某大学计算机学院实验室]]

    - 冷启动用户前 7 天推荐点击率从 2.1% 提升到 3.4%
    - 复现 LightGCN 与 NGCF 两个基线
    - 设计用户-物品二部图叠加社交子图的双通道聚合结构

    #v(3pt)
    #block[#text(weight: "bold")[推荐算法实习]]
    #block[#text(size: 9.234pt, fill: rgb("#555555"))[算法实习生 | 2025.07 – 2025.09 | 某科技公司]]

    - 负责召回通道的特征迭代，离线 AUC 提升 0.008
    - 用 Spark 重算近 30 天行为序列特征

    #v(2pt)
    #text(size: 11.799pt, weight: "bold", fill: rgb("#2b4a6f"))[项目经历]
    #v(-6pt)#line(length: 100%, stroke: 0.7pt + rgb("#2b4a6f"))#v(3pt)
    #block[#text(weight: "bold")[校园二手交易平台]]
    #block[#text(size: 9.234pt, fill: rgb("#555555"))[后端负责人 | 2024.03 – 2024.06 | 某大学]]

    - 重构检索链路后平均响应时间从 1200ms 降至 180ms
    - 基于 Spring Boot 实现 23 个 REST 接口
    - 用 Elasticsearch 重构商品检索

  ],
)