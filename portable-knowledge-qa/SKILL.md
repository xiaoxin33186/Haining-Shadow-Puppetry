---
name: portable-knowledge-qa
description: "在 TRAE 中检索当前项目的本地知识库和问答库，并依据原文证据回答。用户询问项目资料、海宁皮影戏事实、历史、人物、技艺、剧目、传承保护、标准问答或信息来源时必须使用；修改代码、运行测试、调整界面、执行动作、普通聊天或知识库无关任务不得使用。支持 .md、.txt、.docx、.xlsx、.xlsm、.xls、.json，不支持 PDF、OCR 或网络搜索。"
---

# TRAE Portable Knowledge QA

使用内置检索器读取 TRAE 当前打开项目中的知识库。只根据检索证据回答，不得使用模型记忆补充事实。

## 执行流程

1. 获取 TRAE 当前工作区的绝对路径。
2. 首次检索必须保留用户原问题，不要先删减实体、年份、地点或限定词。
3. 从本技能目录运行：

```powershell
python scripts/search_knowledge.py "用户原问题" --root "当前工作区绝对路径"
```

4. 检查返回的 `match_type`、`confidence`、`evidence` 和 `authoritative_answer`。
5. `match_type` 为 `qa` 时，只以 `authoritative_answer` 为事实依据，年份、人名、编号和名单必须照录。
6. `match_type` 为 `knowledge` 时，优先摘录 `evidence` 中的关键原文，再归纳成一个简洁段落。
7. `match_type` 为 `none` 时回答：`未找到足够信息，无法根据当前知识库回答。`
8. 首次有证据但信息不完整时，可以保留原问题关键实体并改写关键词继续检索，最多三次。
9. 默认不显示文件路径和引用片段；只有用户明确追问来源、出处、原文或依据时才展示 `sources`。

## 路径处理

如果当前工作区存在 `knowledge-sources.json`，必须优先按其中的文件角色加载：

- `knowledge`：只进入普通知识索引。
- `qa`：只进入权威QA索引，不重复生成知识块。
- `evaluation`、`template`、`deprecated`：不进入生产索引。

没有来源清单时才自动寻找：

`local_kb`、`local_kb_extended`、`question_bank`、`knowledge`、`knowledge_base`、`kb`、`faq`、`qa`、`知识库`、`问答库`。

用户明确指定知识库位置时，使用：

```powershell
python scripts/search_knowledge.py "用户问题" --source "D:\path\to\knowledge" --qa-source "D:\path\to\faq.json"
```

不要写死某台电脑的项目路径。脚本会从自身位置加载检索代码，并以 `--root` 指定的 TRAE 工作区为扫描范围。

## 输出要求

- 最终答案使用一个简洁段落，尽量不超过 100 个中文字符；为保证事实完整时可以适当超出。
- 不输出 JSON、检索过程、工具调用过程或 `</STYLE>` 等内部格式。
- 不把多个相互冲突的证据强行合并。
- 问答库优先于普通知识文档。
- 没有证据时必须拒答，不给出搜索网站、电话咨询等模型自拟建议。

## 环境检查

首次运行失败或遇到 Excel 文件时执行：

```powershell
python scripts/check_runtime.py
```

检查脚本只报告 Python 和可选依赖状态，不自动安装任何内容。

需要检查自动发现的文档和解析错误时执行：

```powershell
python scripts/inspect_knowledge.py --root "当前工作区绝对路径"
```

检查输出中的 `role`、`source_manifest`、文档数量、QA数量和解析错误。项目存在回归集时运行：

```powershell
python scripts/run_regression.py --root "当前工作区绝对路径" --cases evaluation\knowledge-regression.json
```

添加知识文件或排查格式时读取 [data-formats.md](references/data-formats.md)。排查 TRAE 调用问题时读取 [trae-integration.md](references/trae-integration.md)。

## 硬性限制

- 只处理知识库检索、证据阅读和基于证据的回答。
- 不修改代码、动作、语音、动画或界面。
- 不调用外部模型、模型服务、MCP 或网络搜索。
- 不处理 PDF、扫描图片或 OCR。
- 不自动安装 Python 或依赖。
- 不把 `evaluation`、`template` 或 `deprecated` 文件作为回答证据。
