# CiteThreads (引脉) - Academic Citation Visualization & Intelligent Writing Assistant

[![Version](https://img.shields.io/badge/version-1.0.0-blue)](https://github.com/NkAntony777/CiteThreads) ![License](https://img.shields.io/badge/license-MIT-green) ![React](https://img.shields.io/badge/frontend-React-61DAFB) ![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)

> **Explore Deep Citation Relationships, Seamlessly Assist Academic Writing**
>
> **CiteThreads (引脉)** is a comprehensive research tool integrating academic citation graph construction, multi-source literature aggregation search, and AI-assisted writing. It helps researchers quickly clarify citation threads within a field, discover core literature, and efficiently complete paper writing in an immersive environment.
>
> ![Work Process Overview](docs/assets/sample.webp)

---

[**中文 (Chinese)**](./README.md) | [**English**](./README_EN.md)

## ✨ Core Features

### 1. 🔍 Deep Citation Graph Construction
CiteThreads automatically crawls and integrates multi-source data from **OpenAlex**, **Semantic Scholar**, **ArXiv**, to build a clear literature citation network for you.

- **Multi-level Penetration**: Supports setting citation depth to automatically discover "references of references".
- **Smart Filtering**: Automatically filters high-impact papers and identifies citation intents (Support/Dispute/Mention).
- **Visual Interaction**: High-performance graph based on D3.js/PixiJS, supporting zooming, dragging, and viewing node details.

![Citation Graph](docs/assets/图谱.png)

### 2. 📝 Immersive AI Writing Assistant
Integrates a powerful Markdown editor with a context-aware AI assistant, keeping writing and thinking in sync.

- **Fullscreen Focus Mode**: AI assistance on the left, fullscreen writing on the right, reducing switching distractions.
- **Graph Context Awareness**: The AI assistant understands the citation graph you are currently building, providing more targeted writing suggestions.
- **One-click Citation Insertion**: Insert literature in standard format directly from the graph or search results into the document.
- **WYSIWYG**: Professional Markdown editing experience based on Vditor.

![Fullscreen Editor](docs/assets/AI写作助手.png)

### 3. 🌐 Unified Multi-source Search
Break down data source barriers and get academic resources from the whole web in one stop.

- **Multi-source Aggregation**: Search OpenAlex, ArXiv, DBLP, and other databases simultaneously.
- **Smart ID Resolution**: Automatically identify DOI, ArXiv ID, OpenAlex ID, and other formats for precise cross-database retrieval.
- **Real-time Preview**: Quickly view paper abstracts, citation counts, and publication information.

![Union Search](docs/assets/搜索.png)

### 4. 🌍 Multi-language Support
Real-time switching between Chinese and English, providing an accessible experience for researchers worldwide.


---

## 🛠️ Tech Stack

### Frontend
- **Framework**: React 18 + Vite
- **UI Library**: Ant Design 5
- **Visualization**: D3.js / React-Force-Graph
- **Editor**: Vditor (Markdown)
- **State Management**: MobX

### Backend
- **Framework**: FastAPI (Python 3.10+)
- **Crawlers**: Asynchronous Concurrent Crawlers (httpx + asyncio)
- **Data Sources**:
    - OpenAlex API
    - Semantic Scholar API
    - ArXiv API
    - CrossRef API
- **AI Integration**: LLM Interface Support (DeepSeek/OpenAI Compatible)

---

## 🚀 Quick Start

### Prerequisites
- Node.js >= 16
- Python >= 3.10

### 1. Start Backend
```bash
cd backend
# Install dependencies
pip install -r requirements.txt
# Start service
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Start Frontend
```bash
cd frontend
# Install dependencies
npm install
# Start dev server
npm run dev
```

Visit `http://localhost:5173` to start using.

---

## 📄 License
This project is licensed under the MIT License.
