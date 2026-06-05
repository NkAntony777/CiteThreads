# 引脉 (CiteThreads) - 学术检索与写作助手

[![Version](https://img.shields.io/badge/version-2.1.0-blue)](https://github.com/NkAntony777/CiteThreads) ![License](https://img.shields.io/badge/license-MIT-green) ![React](https://img.shields.io/badge/frontend-React-61DAFB) ![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688) ![i18n](https://img.shields.io/badge/i18n-zh%20%2F%20en-orange)

> **深入探索文献关系，无缝辅助学术写作**
>
> **引脉 (CiteThreads)** 是一个聚焦"搜索 + 写作"两件事的学术助手。用户在 ChatGPT 风格的对话窗口里输入一句自然语言，Agent 自动搜索多源论文、按需做雪球扩、按需触发长文写作流水线，每篇论文自动生成 `[@AuthorYear]` 引用。

![ChatView](docs/assets/chatview-overview.png)

---

[**中文**](./README.md) | [**English**](./README_EN.md)

## ✨ 核心功能

### 1. 🤖 ChatGPT 风格对话入口
整个应用就是一个对话窗口。左侧是会话列表（多对话 sider），中间是 ChatGPT 风格的消息流 + 底部输入框。

- **自然语言输入**：「帮我写一篇 GNN 综述」「推荐 5 篇 Transformer 论文」「查询 X 的引用」——Agent 自动判断意图
- **多对话历史**：每个项目 = 一场对话；点 [+ 新对话] 即开即用
- **持久化**：每条消息持久化到 `Project.chat_history`，刷新后完整恢复

![History Drawer](docs/assets/chatview-history-drawer.png)

### 2. 🔍 Agent 自动搜索 + 雪球扩
内置 7 个工具（多源检索 + 引用网络 + 项目感知），Agent 自动决定何时用哪个：

- **`search_papers`**：跨 OpenAlex / arXiv / DBLP / PubMed 检索
- **`get_citing_papers`** / **`get_referenced_papers`**：从锚点论文雪球扩
- **`search_by_author`**：按作者名找人
- **`get_paper_details`**：拿单篇完整元数据
- **`list_project_references`**：列出项目已引用的论文，避免重复推荐
- **`find_research_gaps`**：在项目引用图谱上发现研究空白

**0 命中升级**：搜索全部 0 命中时，Agent 自动切换到 `search_by_author` 或雪球扩。**搜索结果在工具返回的瞬间就 emit 到前端**，跑到上限也不丢候选。

### 3. 📝 长文写作流水线 (CTDP)
Agent 在用户表达"写一篇 / 帮我写 / 综述"等意图时，**隐式**触发 5 阶段流水线（也可在 ChatView 中显式按 phase 按钮运行）：

| 阶段 | 角色 | 进度 |
|---|---|---|
| 1. Research | 文献调研 | 候选论文 + 摘要 + 研究空白 |
| 2. Structure | 大纲架构 | 章节结构 + 风格化 outline |
| 3. Compose | 6 段写作 | introduction / literature_review / methodology / results / discussion / conclusion |
| 4. Validate | 审稿 + 事实核查 | QA 报告 |
| 5. Compile | 汇编 + 摘要 | 最终论文 .md / .pdf / .docx / .tex |

**进度卡 + 断点续跑 + 重试**（内嵌在 ChatView 中，**不离开对话**）：
- 5 张 phase 卡片实时显示状态（pending / running / ✓ done / ✗ failed / ⊘ skipped）
- Compose 卡内部展开 6 段子段进度
- 每张失败卡有 **Retry** 按钮
- 后端检测 checkpoint 存在时**自动**走 `runner.resume_from()`，不重做已写完的段落
- QualityGate 5 维评分（word_count / citation_density / completeness / structure / graph_health）写入 `quality_history`

### 4. 🌐 引用自动嵌入
每条 chat 中的论文推荐都带 `[@AuthorYear]` 引用，CTDP 写正文时自动嵌入。导出由后端 `exporters.py` 完成：
- Markdown（`.md`）
- PDF（`.pdf`，经 WeasyPrint 渲染 HTML+CSS）
- DOCX（`.docx`，经 python-docx）
- LaTeX 源（`.tex`，可粘贴到 `\documentclass{article}` 项目中）

### 5. ⚙️ AI 设置面板
右上角齿轮按钮 → **AI 设置**：

![AI Settings](docs/assets/chatview-ai-settings.png)

- **Provider**：硅基流动 / OpenAI / DeepSeek / Anthropic / Google / 自定义
- **Model**：提供商对应的模型名
- **API Key**：留空时使用服务器环境变量 `SILICONFLOW_API_KEY`（推荐）
- **Temperature / Max Tokens / System Prompt**：可调
- 配置持久化到 `localStorage`，并**通过请求头 `X-AI-Config` 注入到后端**

### 6. 🌍 多语言支持
实时切换中英文。i18n 走 i18next + Ant Design LocaleProvider，配置持久化到 localStorage，**Ant Design 文案同步切换**。

---

## 🛠 技术栈

### Frontend
- **Framework**: React 18 + Vite + TypeScript
- **UI Library**: Ant Design 5
- **State Management**: Zustand
- **Internationalization**: i18next + react-i18next + Ant Design LocaleProvider
- **Markdown**: react-markdown
- **Visualization**: D3.js（保留供图谱使用）+ dagre
- **Editor**: Vditor（CanvasEditor）
- **Layout**: react-resizable-panels
- **Tests**: Vitest + Testing Library

### Backend
- **Framework**: FastAPI (Python 3.10+) + Pydantic v2
- **Auth**: Bearer Token（`app/auth.py`，所有 API 默认需要）
- **Logging**: 结构化 JSON（`app/logging_config.py`）
- **Observability**: Prometheus `/metrics` + JSON `/api/metrics`
- **Health**: `/health`、`/health/live`、`/health/ready`
- **Agent Runtime**: OpenAI-compatible tool-calling loop（`AgentRuntime` in `agent_runtime/runtime.py`，7 个工具）
- **CTDP Draft Pipeline**: 5 阶段（research / structure / compose / validate / compile）+ QualityGate 5 维评分
- **Crawlers**: OpenAlex / Semantic Scholar / ArXiv / CrossRef / DBLP / PubMed
- **Storage**: JSON-file based（`data/projects/<project_id>/`，含 `chat_history.json` 与 `draft_state.json`）
- **LLM Factory**: `app/services/llm_factory.py` 统一封装 `AsyncOpenAI`
- **Exporters**: WeasyPrint（PDF）/ python-docx（DOCX）/ LaTeX 渲染器
- **AI Integration**: LLM 接口支持（硅基流动 / DeepSeek / OpenAI / Anthropic / Google / 自定义）

---

## 🚀 快速开始

### 环境要求
- Node.js >= 16
- Python >= 3.10

### 1. 启动后端
```bash
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. 启动前端
```bash
cd frontend
npm install
npm run dev
# 打开 http://localhost:5173
```

### 3. 配置 LLM（可选）
打开 UI 后，点右上角齿轮 → **AI 设置**（详见上文第 5 节）。如使用服务器环境变量 `SILICONFLOW_API_KEY`，**留空 API Key 即可**。

### 4. 开始
- 点 [+ 新对话] 开新场对话
- 输入自然语言，例如：「帮我写一篇图神经网络综述」
- Agent 自动搜索 + 写作 + 引用
- 切到历史对话框继续或重新开始

---

## 📁 项目结构

```
backend/app/
  main.py               # FastAPI 入口 + 路由挂载 + CORS + 中间件
  config.py             # pydantic-settings
  auth.py               # Bearer Token 鉴权
  logging_config.py     # JSON 日志
  metrics.py            # Prometheus 指标
  health.py             # /health 合并报告 + liveness/readiness
  cost_guard.py         # LLM 成本护栏
  rate_limit.py         # 速率限制
  users.py              # 用户表（鉴权用）
  agent_runtime/        # Tool-calling agent（AgentRuntime + 7 工具 + 会话记忆）
  crawlers/             # 6 个学术数据库爬虫（OpenAlex / S2 / arXiv / DBLP / PubMed / Crossref）
  routers/              # papers / projects / writing / ai / agent / draft / admin
  services/
    llm_factory.py      # 统一 AsyncOpenAI 客户端
    paper_search_service.py
    storage.py          # JSON-file 项目存储
    cache.py            # 结果缓存
    network_analysis.py # 引用图谱分析
    gap_detection.py    # 研究空白发现
    draft_pipeline/     # CTDP: 5 阶段 + QualityGate + 检查点 + 导出器
  models/               # Pydantic schemas

frontend/src/
  App.tsx               # 顶层壳层（Header + ChatView + 抽屉）
  main.tsx              # 入口
  i18n.ts               # i18next 配置
  components/
    ChatView/           # 主对话窗口（sider + 消息流 + 输入框 + PhaseProgressPanel）
    AISettings/         # AI 设置面板（provider / model / key / temperature）
    HistoryPanel/       # 历史对话抽屉
    AgentChatPanel/     # （旧）独立 chat 面板
    DraftGenerator/     # （旧）CTDP 触发器
    SearchBar/          # （旧）SmartSearch 入口
    GraphCanvas/        # D3 图谱（保留供历史项目）
    WritingAssistant/   # Vditor 编辑器
    LiquidBackground/   # 液态玻璃背景
  stores/               # graphStore / filterStore / uiStore
  services/             # api / chatApi / draftApi / agentStream / writingApi / aiConfig
  hooks/                # useChatStream 等
  locales/              # zh-CN.json + en-US.json
  types/                # 共享 TypeScript 类型
  utils/                # 工具函数 + 图谱布局

backend/tests/                    # Pytest（包含 CTDP 阶段单测 + draft router 集成测试）
frontend/src/**/*.test.tsx         # Vitest
```

---

## 🧪 测试

```bash
# 后端
cd backend
.\.venv\Scripts\python.exe -m pytest tests/ -q

# 前端
cd frontend
npm run test:run
npx tsc --noEmit
npm run lint
```

---

## 📜 License
MIT
