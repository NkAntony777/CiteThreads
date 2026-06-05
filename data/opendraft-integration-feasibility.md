# opendraft → CiteThreads 集成可行性调研

> 调研对象：`D:\CLI-paper\opendraft`（本地副本，HEAD 在 main）
> 目标项目：`E:\vibe_coding_project\CiteThreads`（HEAD 在 main，commit `ced7166`）
> 调研目的：评估将 opendraft 整合进 CiteThreads 以**加强论文写作能力**的可行性，并给出推荐方案。
> 调研日期：2026-06-04

---

## 0. TL;DR — 推荐结论

**opendraft 不应作为单一模块直接 vendor 进来**（16,600 行 utils + 2,750 行 phases 全部塞入会破坏现有架构），但它的**分层多 Agent 流水线设计、引用编排器、质量门、提示词模板**都是 CiteThreads 当前缺失或薄弱的能力。

**推荐路径（三阶段、可独立交付）：**

1. **第一阶段（1 周，低风险）** — 借 opendraft 的**纯函数工具层**补齐 CiteThreads 的引用与导出能力：
   - 替换简版 `ReferenceList.to_bibtex` 为 `pybtex` + `citeproc-py` 支持 5 种引用样式（APA / IEEE / Chicago / MLA / NALT）
   - 引入 `quality_gate` 思想为 `review_generator` 加 4 维质量评分（字数 / 引用密度 / 完整性 / 结构）
   - 引入 `citation_compiler` 的 `{cite_001}` → 格式化引用替换逻辑，给 `WritingAssistant` 的 chat 输出做引用规范化

2. **第二阶段（2 周，中等风险）** — 把 opendraft 的**多阶段编排思想**移植为 FastAPI 友好的异步流水线：
   - 复用 `prompts/0[1-6]_*.md` 21 个提示词文件（双语译版）
   - 新增 `backend/app/services/draft_pipeline/`，实现 `Scout → Scribe → Signal → Architect → Formatter → Crafter → Refiner → Compiler` 异步阶段
   - 新增 `/api/draft/generate` 和 `/api/draft/checkpoint` 两个端点；前端 `WritingAssistant` 增加"长文生成"模式
   - 保持 LLM 工厂通过 `LLMFactory` 注入，复用现有 AsyncOpenAI 客户端（无需引入 `google-genai`）

3. **第三阶段（2-3 周，高价值）** — 深化能力：
   - 引入 `checkpoint/resume`、`circuit breaker`、`partial output capture` 三件套，把 10-30 分钟长跑的失败恢复做扎实
   - 引入 PDF / DOCX 导出（通过 `weasyprint` + `python-docx`），并把 CiteThreads 现有的项目 JSON 存档纳入产物
   - 把 opendraft 的 `sentry_config.py` / `structured_logger.py` 思想用到现有后端

**预估收益：**
- 论文写作从"对话式辅助"升级为"端到端多阶段生成"，可生成 5,000–20,000 字长稿
- 引用样式规范化，避免手写 BibTeX
- 失败可恢复（用户可关闭浏览器再回来继续）
- 区分一次性"长文生成"与日常"对话式辅助"两种 UX

**预估成本：**
- 第一阶段：~5 工时；新增依赖 `pybtex`、`citeproc-py`（轻量，零系统依赖）
- 第二阶段：~15 工时；零新依赖（提示词进仓库，纯 Python）
- 第三阶段：~20 工时；新增 `weasyprint`（含 GTK/Pango 系统依赖，Windows 安装略麻烦），可选 `python-docx`（轻量）

---

## 1. opendraft 项目概览

### 1.1 是什么

opendraft 是一个**纯 Python CLI + Flask Web UI** 的"AI 论文草稿生成器"，定位"用 19 个专门 Agent 合作生成 5,000–20,000 字带真引用的研究草稿"。

- License：MIT
- 入口：`engine/draft_generator.py`（901 行），CLI 在 `engine/opendraft/cli.py`（单文件 1,700 行）
- 体量：engine 目录下 91 个 Python 文件
  - `phases/`：6 个文件（research / structure / citations / compose / validate / compile），共 ~2,750 行
  - `utils/`：约 35 个文件，共 ~16,600 行
  - `prompts/`：21 个 .md 提示词（按 01_research → 06_enhance 分组）

### 1.2 流水线骨架

```
Research   → Scout + Scribe + Signal      （找论文 / 总结 / 找空白）
Structure  → Architect + Formatter        （搭大纲 / 套格式）
Citations  → 确定性管线（去重 / 抓标题元数据 / 质量过滤）
Compose    → 7 个 Crafter 顺序写章节      （引言/文献综述/方法/结果/讨论/结论/附录）
Validate   → Thread + Narrator + FactCheck（叙事一致性 / 语气统一 / 事实核查）
Compile    → 组装 + 摘要 + PDF / DOCX / LaTeX
```

每个阶段都写到磁盘，支持 `--resume` 断点续跑；阶段间通过 `DraftContext` 数据类传递状态（`engine/phases/context.py`，95 行）。

### 1.3 关键模块速览

| 模块 | 行数 | 作用 | 可移植性 |
|---|---|---|---|
| `phases/research.py` | 328 | 通过 OpenAlex API 找论文、生成研究空白 | **高**，但要改成 async |
| `phases/structure.py` | 95 | 生成大纲、套用 APA 7 格式 | **极高**，纯 LLM 调用 |
| `phases/cite_compiler.py` | (in utils) 120+ | `{cite_001}` → 格式化引用；缺引时自动调用 Crossref | **高**，无副作用 |
| `phases/validate.py` | 276 | 4 维质量评分（字数/引用/完整/结构） | **极高**，纯计算 |
| `phases/compile.py` | 569 | 摘要生成、PDF / DOCX / LaTeX 导出 | 中，依赖 weasyprint |
| `utils/citation_database.py` | 大 | `CitationDatabase` + `add_citations_batch`（去重 / 批量） | **高** |
| `utils/quality_gate.py` | ~150 | 100 分制评分（25×4 维度） | **极高** |
| `utils/checkpoint.py` | ~200 | 阶段间状态序列化与恢复 | **高**，可移植 |
| `utils/retry.py` | 含 `CircuitBreaker` | 5 失败开路 / 半开探测 | **高** |
| `utils/api_citations/` | ~10 文件 | Crossref → S2 → Gemini Grounded → LLM 的引用查找降级链 | **高**，仅需替换 LLM 客户端 |
| `utils/gemini_client.py` | ~120 | `google-genai` 兼容性包装 | 低，**应替换为 OpenAI 兼容** |
| `utils/scrape_citation_*` | 大 | 抓论文标题/元数据 | 中，复用价值小（CiteThreads 已有爬虫） |
| `prompts/0[1-6]_*.md` | 21 文件 | 6 类 Agent 的英文提示词 | **极高**，内容是核心资产 |

### 1.4 它不擅长什么（也帮我们避坑）

- **不是异步 FastAPI 友好**：阶段函数是同步阻塞，调用方是 CLI
- **不是交互式**：批量一次跑完，不支持用户在中间插入"图谱上下文"
- **不感知引用图谱**：Scout 阶段只查 API，不知道 CiteThreads 已经构建好的 `GraphData`
- **不感知项目元数据**：每篇论文是独立的 topic → draft，不挂在某个 project 下
- **强依赖 Gemini**：`google-genai` 是默认后端，且 `valid_gemini_models` 硬编码了 7 个模型名
- **重型系统依赖**：`weasyprint` 在 Windows 上需要 GTK3 / Pango
- **36 个 utils 文件职责分散**（opendraft 自己也在 ISSUES.md 里吐槽）

---

## 2. CiteThreads 写作子系统现状

### 2.1 已有能力（按层次）

**路由层**（`backend/app/routers/`）：
- `writing.py` — 画布 CRUD、参考文献 CRUD、文献综述生成、AI 对话、章节生成
- `agent.py` — 工具调用 Agent（含 `/api/agent/chat` + `/api/agent/chat/stream` SSE 流）
- `ai.py` — AI 配置、连接测试（带 SSRF 防护、URL 校验）

**服务层**（`backend/app/services/`）：
- `llm_factory.py` — 统一 `AsyncOpenAI` 客户端工厂（OpenAI 兼容，**默认走 SiliconFlow + DeepSeek-V3**）
- `writing_assistant.py` — 对话式写作助手（chat / generate_section / expand_content）
- `review_generator.py` — 单次 LLM 调用的文献综述生成（不评分、不断点）
- `graph_builder.py` — 引用图谱构建（多源 + 智能 fallback）
- `gap_detection.py` — 研究空白检测
- `network_analysis.py` — PageRank / 中介中心性 / 社区发现
- `embedding_service.py` — 嵌入（用于网络分析）
- `cache.py`、`storage.py` — 论文/项目缓存

**Agent 运行时**（`backend/app/agent_runtime/`）：
- `runtime.py` — OpenAI 兼容的工具调用 Agent，已支持 4 个工具（search_papers / get_paper_details / list_project_references / find_research_gaps）
- `memory.py` — 会话级消息历史
- `tools.py` — Tool 注册表

**前端**（`frontend/src/components/WritingAssistant/`）：
- `WritingAssistant.tsx`、`CanvasEditor.tsx`、`FullscreenCanvas.tsx`
- 基于 Vditor Markdown 编辑器，**左侧 AI 助手 + 右侧全屏写作**
- "图谱上下文感知"已经在 README 中宣传，但实际只把 references 拼到 prompt

### 2.2 数据模型（`backend/app/models/`）

- `Paper`、`CitationEdge`、`GraphData` — 论文与图谱
- `references.py`：`Reference`、`ReferenceList`、`LiteratureReviewDraft`、`WritingContext`、`ChatMessage`
- 引用键生成规则：`FirstAuthorLastName + Year`（如 `Zhang2024`）
- **简单版 BibTeX 导出**（`to_bibtex`，只支持 `@article`）

### 2.3 关键不足（与 opendraft 对照）

| 能力 | opendraft | CiteThreads | 影响 |
|---|---|---|---|
| 多阶段长文生成 | ✅ 6 阶段 | ❌ 一次 LLM 调用 | 长稿质量难控 |
| 阶段间断点续跑 | ✅ `checkpoint` | ❌ 失败即重头 | 30 分钟白跑 |
| 质量评分门 | ✅ 100 分制 + 阈值 | ❌ 无 | 不知道输出够不够好 |
| 引用样式模板 | ✅ 5 种（APA / IEEE / Chicago / MLA / NALT） | ❌ 手写 BibTeX | 跨期刊格式不统一 |
| `{cite_001}` 自动展开 | ✅ `CitationCompiler` | ❌ 纯字符串 | 容易出现未替换的占位符 |
| 缺引时自动找 | ✅ Crossref → S2 → Gemini Grounded → LLM | ❌ 用户自己搜 | 缺引只能手动补 |
| 章节级事实核查 | ✅ FactCheck agent | ❌ 无 | 写错了没人拦 |
| 长上下文处理 | ✅ `smart_truncate` + `token_counter` | ❌ 全量塞 prompt | 上下文超长 OOM |
| PDF / DOCX / LaTeX 导出 | ✅ WeasyPrint + python-docx | ❌ 仅 Markdown | 投稿/打印不便 |
| 引用图谱感知 | ❌ | ✅ **核心能力** | opendraft 看不到上下文，CT 占优 |
| 中文 + i18n | 弱（提示词英） | ✅ 完整 i18n | CT 占优 |
| OpenAI 兼容 | 仅 OpenAI 客户端 | ✅ AsyncOpenAI + 任意 base_url | CT 占优 |
| 工具调用 Agent | ❌（顺序流水线） | ✅ `agent_runtime` | CT 占优 |
| 项目维度组织 | ❌ 一次性 | ✅ `ProjectStorage` | CT 占优 |

**结论：两边不是替代关系，是互补关系。**

---

## 3. 集成方案：四个备选

### 方案 A：直接 vendor 整个 opendraft（**不推荐**）

把 `engine/` 整包复制到 `backend/app/_vendored/opendraft/`。

**否决理由：**
1. 16,600+2,750 行代码 + 21 文件提示词 + 30+ utils，**不可控**
2. 与 CiteThreads 的 `Paper` / `Reference` / `Project` 模型不兼容，要写大量 adapter
3. 默认 Gemini 后端会与现有 SiliconFlow 配置冲突
4. `phases/` 是同步阻塞的，**塞进 FastAPI 需要全量改 async**（不是 import 一行就完事）
5. `pyproject.toml` 把 `phases/` `utils/` 当顶层包导出（`[tool.setuptools.packages.find]` include `phases*`），与我们现有的 `app.services` 风格冲突
6. 引入 `weasyprint` 等重型系统依赖，可能影响现有 dev 环境

### 方案 B：只借提示词和"流水线思想"（**第二阶段推荐**）

不搬代码，按 opendraft 的 6 阶段模型在 `backend/app/services/draft_pipeline/` 下**重新实现**：

```
draft_pipeline/
├── context.py        # 类 DraftContext 但用 Pydantic + async
├── research.py       # async 包装：调用 paper_search_service
├── structure.py      # 套用 opendraft prompts/02_structure/*
├── citations.py      # 套用 ReferenceList + pybtex
├── compose.py        # 套用 opendraft prompts/03_compose/crafter.md
├── validate.py       # 套用 opendraft utils/quality_gate.py 思路
├── compile.py        # Markdown 组装 + 摘要生成（PDF/DOCX 后续）
├── checkpoint.py     # 借用 opendraft utils/checkpoint.py 思想
├── progress.py       # 套用 opendraft progress_tracker.py 思想
└── runner.py         # async orchestrator
```

**优势：**
- 不破坏现有架构
- LLM 走现有 `LLMFactory`，与 SiliconFlow / DeepSeek / 自定义 base_url 兼容
- 完全用 `Paper` / `Reference` 现有模型，能感知引用图谱（opendraft 做不到）
- 通过 `ReferenceList` 复用项目维度的引用
- 提示词进仓库前要做中英双语化（21 文件 × 2 = 42 文件，但与现有 i18n 风格分离，提示词走 prompt directory）

**风险：**
- 21 个提示词要翻译/本地化（不是小工作量，估算 1 个工日）
- 测试覆盖要从零写（opendraft 461 个测试不会自动跟过来）
- 我们之前没有 SSE 长事件流，phase progress 推送要新建（已有 `agent.py` SSE 模板可抄）

### 方案 C：微内核 — 只搬纯函数工具（**第一阶段推荐**）

只引入**与 LLM 无关、与状态无关**的纯函数模块：

| opendraft 模块 | 适配成本 | 引入理由 |
|---|---|---|
| `utils/quality_gate.py` | 1 小时 | 4 维评分可直接挂到 `review_generator` 输出 |
| `utils/citation_compiler.py` 核心 | 2 小时 | 替代 `LiteratureReviewDraft.remove_citation` 的粗暴正则 |
| `utils/citation_database.py` 模型 | 3 小时 | 升级现有 `ReferenceList`（批量插入、去重、样式枚举） |
| 提示词 `prompts/03_compose/crafter.md` | 1 小时 | 给 `WritingAssistant.generate_section` 升级 prompt |
| 提示词 `prompts/04_validate/quality_*.md` | 1 小时 | 给评分器加事实核查 prompt |

**新依赖**：`pybtex`、`citeproc-py`（轻量，跨平台，零系统依赖）

**优势：**
- 单 PR 即可合并
- 与现有架构无侵入
- 把 opendraft 核心资产（提示词 + 评分思路）"以最薄方式"吸收

### 方案 D：Web Service 互调（**不推荐**）

opendraft 启 Flask 跑 8500 端口，CiteThreads 通过 HTTP 调用。

**否决理由：**
- 增加部署复杂度
- 引入跨进程 API 不稳定（opendraft CLI 假设一次性跑完，没有"接上下文"接口）
- 数据要序列化两次（项目 → JSON → 重建），丢失类型

---

## 4. 推荐路径：分阶段推进

### 4.1 第一阶段 — 引用能力升级（1 周）

**目标：** 把 `ReferenceList` 从"能存能用"升级为"能导出 5 种样式 + 能自动查缺 + 能评分"。

**改动清单：**
1. `backend/requirements.txt` 加 `pybtex>=0.24.0,<0.25.0` + `citeproc-py>=0.6.0,<0.7.0`
2. `backend/app/services/citation_styles.py`（新文件）— 5 种样式枚举 + `format_bibliography(refs, style)`
3. `backend/app/services/citation_resolver.py`（新文件）— 包装 `paper_search_service`，提供 "topic → 候选 Paper" 能力
4. `backend/app/services/quality_gate.py`（新文件）— 4 维评分（25 × 4）
5. `backend/app/models/references.py` 升级 `to_bibtex(style: CitationStyle = "apa")`
6. `backend/app/services/review_generator.py` 在返回 `LiteratureReviewDraft` 前调用 `quality_gate` 计算 `quality_score`
7. 新增 `GET /api/writing/projects/{id}/export?style=apa&format=md` 端点，返回带格式化引用的完整文献
8. 前端 `WritingAssistant` 加一个"引用样式"下拉框（APA / IEEE / Chicago / MLA / NALT）

**不引入** `weasyprint`、`pymupdf`、`google-genai`、`beautifulsoup4`（避免 Windows 端系统依赖）。

**验收：**
- `pytest backend/tests/test_citation_styles.py` 全部通过
- 现有 `pytest` 全部通过
- 前端 `npm run lint && npx tsc --noEmit` 通过

### 4.2 第二阶段 — 多阶段长文生成（2 周）

**目标：** 新增长文生成模式，前端"AI 写作助手"加 tab"长文生成"，后端异步跑 6 阶段流水线。

**改动清单：**
1. `backend/app/services/draft_pipeline/` 新建 9 个文件（见方案 B）
2. 复用 `engine/prompts/0[1-6]_*.md` 的 21 个提示词，在 `backend/app/services/draft_pipeline/prompts/` 下放英文版，**额外翻译/适配中文版**（按 `zh.md` 后缀分文件）
3. `backend/app/services/draft_pipeline/checkpoint.py` 序列化到 `data/projects/{id}/draft_checkpoint.json`
4. `backend/app/services/draft_pipeline/progress.py` 通过 `asyncio.Queue` + SSE 推送阶段进度
5. 新增 `POST /api/draft/projects/{id}/generate`（启动长文生成）
6. 新增 `GET /api/draft/projects/{id}/stream`（SSE 看进度）
7. 新增 `POST /api/draft/projects/{id}/resume`（从 checkpoint 续跑）
8. 新增 `GET /api/draft/projects/{id}/export`（Markdown 草稿下载）
9. 前端 `WritingAssistant/index.ts` 加"长文生成"tab，组件 `DraftGenerator.tsx`（新文件）
10. 前端 `services/draftApi.ts`（新文件）调用新端点

**不引入** `google-genai`（继续走 AsyncOpenAI + 任意 base_url），不引入 `weasyprint`（暂只导出 Markdown）。

**验收：**
- 一个 5 引用 5,000 字草稿能在 5-10 分钟内生成完毕
- 中途关闭浏览器，回来能"恢复"继续
- 任何阶段失败时，对应章节用占位符标记，其他章节不受影响
- 前端能实时看到 "Scout 找论文中... Scribe 总结中... Crafter 写引言..." 进度

### 4.3 第三阶段 — 深度补全（2-3 周）

**目标：** 补 PDF / DOCX 导出、长任务弹性、深层质量保障。

**改动清单：**
1. `backend/requirements.txt` 加 `weasyprint>=60.0`（**注意 Windows 需要单独装 GTK3**）和 `python-docx>=1.0`
2. `backend/app/services/draft_pipeline/compile.py` 增加 `to_pdf` / `to_docx` / `to_latex`
3. `backend/app/services/draft_pipeline/retry.py` — 熔断器 + 30+ 瞬时错误模式（**思想来自** `opendraft/utils/retry.py`）
4. `backend/app/services/draft_pipeline/partial_capture.py` — Agent 超时时保存已写部分
5. `backend/app/services/draft_pipeline/factcheck.py` — 事实核查 agent（基于现有 `Reference` 验证章节中的 `[@CitationKey]` 都有对应真实论文）
6. 前端 `DraftGenerator.tsx` 加"导出 PDF / DOCX"按钮
7. `backend/app/services/structured_logger.py`（新文件）— 借鉴 opendraft 思想，结构化 JSON 日志
8. 文档：把"AI 写作助手"的功能矩阵写进 `README.md`（已经滞后于实际能力）

**验收：**
- 草稿能导出 LaTeX 风格的 PDF（5,000+ 字，10+ 引用，正常排版）
- 30 分钟任务跑一半关掉浏览器、再开能恢复
- 章节中的 `[@Zhang2024]` 都能在 ReferenceList 找到对应 Paper，否则标红
- `sentry_config.py` 风格的异常上下文记录到位

---

## 5. 风险清单与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| opendraft 提示词是英文，需要中文化 | 中 | 21 个 .md 翻译到 21 × 2 = 42 个；翻译由人而非 LLM 一次性完成（避免 LLM 翻译漂移） |
| `weasyprint` Windows 系统依赖 | 中 | 推迟到第三阶段；先只导出 Markdown + DOCX（`python-docx` 纯 Python） |
| 长任务前端 SSE 在 Nginx 后缓冲 | 低 | 加 `X-Accel-Buffering: no`（opendraft 没有此问题，他们用 Flask 自带） |
| 用户误以为 opendraft 是 ChatGPT 替代 | 低 | README 明示"研究草稿"非"终稿"，与 opendraft 一致 |
| LLM 工厂与 opendraft 的 gemini_client 不一致 | 低 | **完全不引入** gemini_client；走现有 AsyncOpenAI 兼容 |
| 引入 opendraft 后导致依赖膨胀 | 中 | 严格按阶段引入；每个 PR 跑 `pip check` |
| 现有 `agent_runtime` 与新流水线"两条路" | 中 | 第二阶段在 `agent_runtime` 下新建 `DraftAgent`，与现有 `agent.py` 共享 LLM 客户端和 SSE 工具 |
| `data/projects/{id}/` 文件污染 | 低 | 参考 opendraft `tests/outputs/`，把 draft 产物放到 `data/projects/{id}/draft/` 子目录 |
| `prompts/` 进仓库导致 i18n 漂移 | 中 | 提示词不进 `frontend/src/locales/`，**单独放** `backend/app/services/draft_pipeline/prompts/{en,zh}/`，与 UI 翻译分离 |
| 测试覆盖率 | 中 | 阶段 1 目标 ≥ 70%，阶段 2 ≥ 60%，阶段 3 ≥ 80% |

---

## 6. 与项目约束的相容性

来自 `AGENTS.md` 的硬约束：

| 约束 | 影响 |
|---|---|
| 禁止 `as any` / `@ts-ignore` / `@ts-expect-error` | 不冲突；新代码走类型化 |
| 不提交 `.env` 或 `backend/data/` | `data/projects/{id}/draft/` 加进 `.gitignore` 模板 |
| 用户可见字符串走 i18n | 后端报错/提示走现有 i18n；新提示词是后端资源，不入 i18n 字典 |
| Python 异步正确 | 第二阶段全部 async；opendraft 的同步阶段函数要逐个改造 |
| 爬虫尊重 rate limit | 引用查找走 `paper_search_service`，复用现有速率控制 |
| 中英 i18n 同步 | 提示词双语版同步维护（不在 `frontend/src/locales/`，单独目录） |

来自 `CLAUDE.md` 的 Auto-Develop Harness：

- 一个任务一 session — 把第一阶段拆成 3 个 harness 任务（cite_styles / quality_gate / citation_resolver），第二阶段拆成 5 个，第三阶段拆成 6 个
- 不要自动 commit — 每个 PR 由用户显式触发
- 优先小而可验证的改动 — 推荐路径严格按"小步骤可独立验证"切分

---

## 7. 决策建议

**如果只能做一件事 → 做第一阶段**（1 周，最小投入、最大可见价值）
- 把"引用样式"和"质量评分"做出来
- 复用现有 ReviewGenerator / WritingAssistant / ReferenceList，不开新模块
- 用户立刻能导出 APA 论文级别的参考文献，看到综述质量分

**如果还能做一件事 → 加第二阶段**（再加 2 周）
- 把"长文生成"做成产品级能力
- 与现有 `agent_runtime` 并存（一个面向"一次性写完整篇"，一个面向"边聊边写"）
- 形成与 opendraft 差异化的竞争力：**多阶段 + 图谱感知 + 双语**

**第三阶段是 nice-to-have**：当产品定位向"投稿级论文工具"靠拢时再做；目前阶段 1+2 已经能覆盖 80% 用户场景。

---

## 8. 关键文件交叉引用

**opendraft（要借的资源）：**
- `engine/draft_generator.py:118-150` — `run_phase_with_retry` 重试模式
- `engine/phases/context.py:1-95` — `DraftContext` 状态对象（思想）
- `engine/phases/validate.py:53-80` — `_build_qa_content` 章节拼接
- `engine/phases/cite_compiler.py` — `{cite_001}` 替换（思想）
- `engine/utils/quality_gate.py:27-68` — 100 分制评分（直接搬）
- `engine/utils/checkpoint.py:1-60` — 阶段序列化
- `engine/utils/retry.py` — `CircuitBreaker` 类
- `engine/utils/api_citations/orchestrator.py:1-40` — Crossref → S2 → LLM 降级链
- `engine/prompts/01_research/scout.md` — 论文发现提示词
- `engine/prompts/03_compose/crafter.md` — 章节写作提示词
- `engine/prompts/04_validate/{skeptic,verifier,referee,factcheck_judge}.md` — QA 提示词

**CiteThreads（要改的地方）：**
- `backend/app/services/llm_factory.py:15-30` — 复用为 LLM 注入点
- `backend/app/services/review_generator.py:64-125` — 加 `quality_gate` 调用
- `backend/app/services/writing_assistant.py:206-292` — `generate_section` 升级 prompt
- `backend/app/models/references.py:107-125` — `to_bibtex` 升级样式
- `backend/app/routers/writing.py:330-378` — `/review/generate` 端点扩字段
- `backend/app/agent_runtime/runtime.py:41-65` — 共享 LLM 客户端和 SSE 模式
- `frontend/src/components/WritingAssistant/index.ts` — 加 tab 入口
- `frontend/src/services/writingApi.ts` — 加新端点封装

---

## 9. 不做的事（明确排除）

为了保持项目边界清晰，下面这些**不做**：
1. 不复制 opendraft 的 `engine/opendraft/cli.py`（CLI 入口，FastAPI 项目不需要）
2. 不引入 `google-genai` SDK（与现有 OpenAI 兼容栈冲突）
3. 不复制 opendraft 的 `web_ui/`（Flask 栈，与 FastAPI + React 前端重复）
4. 不复制 `engine/concurrency/`（opendraft 自己也在 ISSUES.md 标注待重构）
5. 不复制 `engine/utils/pdf_engines/`（多 PDF 引擎选择，opendraft 自己也说"过度工程"）
6. 不复制 `engine/utils/scrape_citation_titles.py` 等抓取工具（已有更准的爬虫）
7. 不复制 `engine/sentry_config.py`（Sentry 是项目外基础设施，opendraft 引入但 CT 不必跟）
8. 不复制 `engine/opendraft.egg-info/`（打包元数据）
9. 不复制 `engine/dist/`（构建产物）

---

## 10. 验收指标（每阶段都应可量化）

| 阶段 | 关键指标 |
|---|---|
| 1 | 5 种引用样式各导出 1 个样例，0 报错；综述质量分覆盖率 100% |
| 2 | 一个 5 引用 5,000 字草稿生成 < 10 分钟；checkpoint resume 成功率 100% |
| 3 | PDF 导出 5,000+ 字成功；30 分钟任务 5 次中断恢复全部成功；事实核查覆盖率 ≥ 80% |

---

*调研完成。是否进入第一阶段？如需进一步讨论某个模块的细节，告诉我具体切入点。*
