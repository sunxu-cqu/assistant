# 把项目资料放在这里

一个项目一个文件夹，文件夹名建议带年份，例如：

```
workspace/projects/
├── 2024-校园二手交易平台/
│   ├── README.md
│   ├── 需求文档.docx
│   └── 答辩.pptx
├── 2025-推荐系统课程设计/
│   ├── 报告.pdf
│   └── 实验数据.xlsx
└── 2025-某公司后端实习/
    └── 工作记录.md
```

支持 `.md .txt .pdf .docx .pptx .xlsx .csv .json .yaml`。
扫描版 PDF、截图、老版 `.doc` 读不到文字，`kb scan` 会逐条告诉你原因。

**不要**把整个硬盘的资料一股脑塞进来——`kb build` 会把每个文件夹喂给模型抽取，
一个文件夹一次调用。挑你真要写进简历的 5~15 个项目就够。

想先看看效果，可以把 `workspace/samples/` 里那两个示例复制过来试跑，
跑通了再删掉换成自己的：

```bash
cp -r workspace/samples/2024-校园二手交易平台 workspace/projects/
```

示例资料放在 `samples/` 而不是这里，是因为它一旦进了 `projects/`，
就会和你的真实经历一起参与检索，可能挤掉你自己的项目。
