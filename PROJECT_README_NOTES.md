# 项目阅读笔记：CiteThreads（引脉）

## 1. 项目定位

CiteThreads 是一个全栈学术引用图谱可视化与 AI 写作助手项目。

核心目标：

- 从 OpenAlex、Semantic Scholar、arXiv、DBLP、PubMed 等来源检索和聚合论文数据
- 构建多层引用关系图谱
- 对引用意图进行分析
- 提供 AI 辅助写作环境
- 支持中英文界面

技术栈：

- 前端：React 18 + Vite + TypeScript + Ant Design 5 + Zustand + D3
- 后端：FastAPI + Pydantic + httpx + asyncio
- 国际化：i18next

---

## 2. 目录结构概览

```text
CiteThreads/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── services/
│   │   ├── stores/
│   │   ├── types/
│   │   ├── utils/
│   │   ├── locales/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── i18n.ts
│   ├── package.json
│   ├── vite.config.ts
│   └── tsconfig.json
├── backend/
│   ├── app/
│   │   ├── crawlers/
│   │   ├── models/
│   │   ├── routers/
│   │   ├── services/
│   │   ├── config.py
│   │   └── main.py
│   ├── data/
│   └── requirements.txt
├── docs/
├── .env.example
└── README.md
```

---

## 3. 前端入口

### `frontend/src/main.tsx`

负责：

- 创建 React 根节点
- 挂载 `App`
- 初始化 Ant Design 样式
- 加载 i18n 配置

### `frontend/src/App.tsx`

主应用布局：

- 顶部 Header
- 左侧搜索/筛选 Sidebar
- 中间主内容区：
  - 图谱模式：`GraphCanvas`
  - 写作模式：`WritingAssistant`

核心状态来自 `useGraphStore()`：

```ts
const { currentProject, loadProject, analyzeProject, buildProgress } = useGraphStore();
```

主要功能入口：

- 搜索栏：`SearchBar`
- 图谱：`GraphCanvas`
- 节点详情：`NodePanel`
- 边详情：`EdgePanel`
- 筛选器：`GraphFilters`
- AI 设置：`AISettings`
- 项目列表：`ProjectList`
- 写作助手：`WritingAssistant`

---

## 4. 前端状态管理

### `frontend/src/stores/graphStore.ts`

这是前端最核心的 Zustand store，负责：

- 当前项目 `currentProject`
- 项目列表 `projects`
- 图谱节点/边 `graph.nodes` / `graph.edges`
- 项目构建进度 `buildProgress`
- 搜索论文
- 创建项目
- 加载项目
- 删除项目
- 分析项目
- 导出

重要方法包括：

- `searchPapers(query, source, limit)`
- `createProject(rootPaperId, source, depth, intentAnalysis)`
- `loadProject(projectId)`
- `deleteProject(projectId)`
- `analyzeProject(projectId?)`
- `setSelectedNode(nodeId)`
- `setSelectedEdge(edgeId)`

---

## 5. 前端 API 服务

### `frontend/src/services/api.ts`

封装了大部分后端 API 调用：

- 搜索论文
- 创建项目
- 获取项目列表
- 加载项目
- 删除项目
- 分析项目
- 导出项目
- 获取图谱统计信息
- 获取引用意图分析
- 获取 AI 生成摘要/回答

请求库：

```ts
axios
```

Vite 开发环境通过代理将 `/api/*` 转发到 `http://localhost:8000`。

---

## 6. 前端组件结构

### 搜索与项目

- `SearchBar`
  - 论文搜索
  - 选择数据源
  - 创建项目
  - 设置引用深度和意图分析
- `ProjectList`
  - 项目列表抽屉
  - 新建/加载/删除项目

### 图谱

- `GraphCanvas`
  - 主图谱渲染
  - D3 force simulation
  - 节点/边点击交互
  - 标签显示
  - 缩放/拖拽
- `NodePanel`
  - 节点详情
  - 引用/被引列表
  - 插入引用
  - 节点操作
- `EdgePanel`
  - 边详情
  - 引用意图
  - 证据句子
- `GraphFilters`
  - 年份筛选
  - 聚类筛选
  - 引用意图筛选

### AI 与写作

- `AISettings`
  - API Key 配置
  - 模型配置
  - 系统提示词
- `WritingAssistant`
  - Markdown 编辑器
  - AI 对话
  - 图谱上下文
  - 引用插入

---

## 7. 后端入口

### `backend/app/main.py`

FastAPI 应用入口。

注册路由：

```py
app.include_router(papers_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(writing_router, prefix="/api")
```

健康检查：

- `/`
- `/health`

CORS 配置来自 `settings.cors_origins`。

---

## 8. 后端路由

### `backend/app/routers/papers.py`

论文搜索相关接口：

- 搜索论文
- 获取论文详情

### `backend/app/routers/projects.py`

项目管理与图谱构建相关接口：

- 创建项目
- 获取项目列表
- 获取项目详情
- 删除项目
- 分析项目
- 导出项目

### `backend/app/routers/ai.py`

AI 配置与分析相关接口：

- 获取/更新 AI 配置
- 分析引用意图
- 生成摘要/回答

### `backend/app/routers/writing.py`

写作助手相关接口：

- 保存/加载草稿
- AI 写作辅助

---

## 9. 后端模型

### `backend/app/models/schemas.py`

Pydantic 模型主要覆盖：

- 论文搜索请求/响应
- 项目元数据
- 图谱节点/边
- AI 配置
- 引用意图分析
- 写作助手请求/响应

### `backend/app/models/project.py`

项目持久化模型，可能包含：

- 项目 metadata
- graph nodes
- graph edges
- 原始论文数据
- 构建参数

---

## 10. 后端爬虫

`backend/app/crawlers/` 下包含多个学术数据库爬虫：

- OpenAlex
- Semantic Scholar
- arXiv
- DBLP
- PubMed
- CrossRef

统一模式：

- 异步类
- `search_papers()`
- `get_paper_by_id()`
- 内部处理 rate limit
- 返回标准化 `Paper` 模型

---

## 11. 后端服务

`backend/app/services/` 包含业务逻辑：

- 项目构建服务
- 引用图谱分析服务
- AI 服务
- 导出服务

---

## 12. 国际化

前端通过 `i18next` 支持中英文。

主要翻译文件：

- `frontend/src/locales/en-US.json`
- `frontend/src/locales/zh-CN.json`

规则：

- 所有用户可见文本必须使用 `t('...')`
- 新增 UI 文案时需同步维护两个语言文件

---

## 13. 重要开发约束

来自项目 AGENTS.md：

1. 禁止用 `as any`、`@ts-ignore`、`@ts-expect-error` 压制 TypeScript 错误
2. 不提交 `.env` 或 `backend/data/`
3. 所有用户可见字符串必须走 i18n
4. Python 异步操作必须正确使用 `async/await`
5. 外部 API 爬虫必须尊重 rate limit
6. 新增翻译必须同步 `en-US.json` 和 `zh-CN.json`

---

## 14. 已知项目特点

- 前端已有较完整 UI 骨架
- 图谱使用 D3 force simulation 实现
- 项目状态和图谱数据集中存储在 Zustand
- 后端采用标准 FastAPI router 分层
- AI 配置目前依赖用户 API Key
- 项目支持 JSON/BibTeX/RIS 导出

---

## 15. 后续可关注点

如果要继续开发，建议优先查看：

1. `frontend/src/stores/graphStore.ts`
   - 理解当前项目数据流
2. `frontend/src/components/GraphCanvas`
   - 理解图谱渲染和交互
3. `backend/app/routers/projects.py`
   - 理解项目构建流程
4. `backend/app/services/*`
   - 理解核心业务逻辑
5. `backend/app/crawlers/*`
   - 理解多源检索实现
