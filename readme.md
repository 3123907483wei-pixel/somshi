# LangGraph 多 Agent 博客写作系统

基于 **LangGraph + MCP + DeepSeek** 的企业级自动化博客写作系统。

[📐 架构设计文档 →](design.md)

**解决痛点**: 热点捕捉慢 / 长文创作周期长 / 多源数据整合难

**核心能力**:
- 4 源热点扫描 (HackerNews / GitHub Trending / 微博 / 抖音)，**始终展示**趋势卡片
- **双模式选题**：留空 = 趋势驱动 / 输入话题 = 话题驱动 (纯 LLM 知识，不关联趋势)
- Human-in-the-Loop 人工审校确认
- Map-Reduce 并行扩写 (Send() + 显式 Join)
- Runtime Checkpoint (MemorySaver, 进程级内存, 非持久化)
- LLM quality evaluator (4 维度评分, <8 自动重试)
- SEO 标题 + 推文钩子输出
- **Web UI** 实时流式进度 + 趋势卡片 + 点击选题

**性能指标 (v4 实测)**:
| 阶段 | 节点 | LLM | 耗时 | Token |
|------|------|-----|------|-------|
| 扫描 | `scan_sources` | ❌ | ~6s | — |
| 选题 | `supervisor_select` | ✅ | ~10s | ~1200 in / 500 out |
| 大纲 | `plan_outline` | ✅ | ~8s | ~300 in / 300 out |
| 调研 | `research_topic` | ❌(MCP) | ~4s | — |
| 并行写 | `write_section` × 3-4 | ✅ | ~15s | 4×300 in / 1800 out |
| 合并 | `merge_and_polish` | ✅ | ~25s | ~2500 in / 1500 out |
| 质评 | `quality_evaluate` | ✅ | ~5s | ~1500 in / 50 out |
| 润色 | `finalize` | ✅ | ~5s | ~1200 in / 200 out |
| **合计** | | | **~75s** | **~9.5K in / 4.3K out** |

---

- [目录结构](#目录结构)
- [核心工作流](#核心工作流)
- [五个核心模块](#五个核心模块)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [Docker 部署](#docker-部署)
- [依赖清单](#依赖清单)
- [演化历程](#演化历程)

---

## 目录结构

```
d:\somshi\
├── app/                    # 应用核心
│   ├── __init__.py
│   ├── engine.py           # LangGraph 工作流 (12 节点 StateGraph)
│   └── webui.py            # FastAPI Web 前端
├── scanners/               # 热点扫描器
│   ├── __init__.py
│   ├── trends_scanner.py   # 4 源热点 (HN/GitHub/微博/抖音)
│   └── crawlers.py         # 爬虫测试工具
├── mcp/ → servers/       # MCP 服务器（改名避免与 pip 包冲突）
│   ├── __init__.py
│   └── search_server.py    # Bing Search MCP server
├── notebooks/
│   └── 1.ipynb             # 原型归档
├── run.py                  # 命令行入口
├── .env.example
├── requirements.txt
└── readme.md
```



## 核心工作流

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Phase 1: 多源热点并行扫描                                                │
│  scan_sources ─── ThreadPool(HN, GitHub, 微博, 抖音) │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Phase 2: Supervisor 选题 + HITL                                       │
│  supervisor_select ── 双模式: 无hint→趋势驱动 / 有hint→LLM知识          │
│  confirm_topic ── interrupt_before 等待用户选择                          │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Phase 3: 大纲 → MCP 实时调研 → 并行写作 (Map → Join → Reduce)          │
│  plan_outline ── LLM 生成 3-4 节大纲                                     │
│  validate_outline ── 快检 H2≥2 → 不合格重试(×3)                          │
│  research_topic ── MCP Bing Search 获取实时数据                          │
│  Send() → write_section × N 并行 + 实时数据                               │
│  join_sections ── 显式 Barrier (等所有分支结束)                           │
│  merge_and_polish ── Reduce 合并润色                                     │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Phase 4: LLM 质量评估 → 最终润色                                       │
│  quality_evaluate ── 4 维度评分 (factuality/structure/SEO/readability)   │
│                    ── score < 8 且重试<3 → 重回 merge_and_polish        │
│  finalize ── 3×SEO标题 + 2×推文钩子                                      │
└─────────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                            END
```

| 模式 | 触发 | 趋势扫描 | 趋势展示 | Supervisor 选题源 |
|------|------|----------|----------|-------------------|
| 🟢 趋势驱动 | 输入框留空 | ✅ 4源扫描 | ✅ | 趋势数据 |
| 🟡 话题驱动 | 输入话题 | ✅ 4源扫描 | ✅ | 纯 LLM 知识 |

| 节点 | 功能 | 调用 LLM | 耗时估算 |
|------|------|----------|----------|
| `scan_sources` | ThreadPool 并行扫 4 源 (始终执行) | ❌ | ~8s |
| `supervisor_select` | 有hint→LLM知识选题 / 无hint→趋势选题 | ✅ | ~10s |
| `confirm_topic` | HITL 处理用户选择 | ❌ | 交互 |
| `plan_outline` | 生成 3-4 节大纲 | ✅ | ~8s |
| `validate_outline` | 非 LLM 快检 (H1/H2 计数) | ❌ | <1s |
| `research_topic` | MCP Bing Search 实时调研 | ❌ | ~4s |
| `write_section` × 3-4 | Send() 并行各写 300-500 字 (含搜索参考) | ✅ | ~12s |
| `join_sections` | 显式 Barrier，确认所有分支完成 | ❌ | <1s |
| `merge_and_polish` | Reduce: 合并润色 | ✅ | ~20s |
| `quality_evaluate` | LLM 4 维度评分 (factuality/structure/SEO/readability) | ✅ | ~5s |
| `finalize` | 3 标题 + 2 推文钩子 | ✅ | ~6s |

### 关键 LangGraph 特性

| 模式 | 实现 | 说明 |
|------|------|------|
| **Send() Map** | `scan_sources` + `write_section` | 并行多源扫描 + 按章节并行写作 |
| **Join Barrier** | `join_sections` | 显式等待所有 Send() 分支结束再进入 Reduce |
| **Reduce** | `merge_and_polish` | 合并各节成完整文章 |
| **Supervisor** | `supervisor_select` | LLM 分析热点推荐选题 |
| **Human-in-the-Loop** | `confirm_topic` + `interrupt_before` + `MemorySaver` | 用户选择话题, 可断点恢复 |
| **Conditional Edge** | `validate_outline` / `quality_evaluate` | 格式坍塌防御 + 质量重试环路 |
| **LLM Evaluator** | `quality_evaluate` | 4 维度评分，score<8 自动重试 |
| **Checkpoint** | `MemorySaver` | 进程级内存检查点，用于 HITL 中断恢复（非持久化） |
| **状态剪枝** | `article_sections` 使用 `Annotated[dict, merge_dicts]` | 支持并行写入合并 |

---

## 五个核心模块

### 1. `app/engine.py` — LangGraph 工作流引擎（核心）

**技术栈**：`langgraph` + `langchain-openai` + `langchain-core` + `mcp`

- 11 个节点的 StateGraph, 覆盖 Map-Reduce / Supervisor / HITL
- `Send()` API 实现并行 fan-out（多源扫描 + 按节写作）
- `interrupt_before` + `Command(resume=...)` 实现 HITL
- `MemorySaver` checkpointer 支持断点恢复
- `Annotated[dict, merge_dicts]` reducer 支持并行写入状态合并
- 条件边实现重试环路（格式坍塌防御）
- Supervisor prompt 分支：有 topic_hint → 纯 LLM 知识 / 无 hint → 基于趋势
- 显式 Join Barrier (`join_sections`) 等待所有 Send() 分支
- LLM Quality Evaluator (`quality_evaluate`)：4 维度评分，<8 自动重试
- `asyncio.new_event_loop()` 模式安全调用 MCP，避免 FastAPI 事件循环冲突

### 2. `scanners/trends_scanner.py` — 多源热点扫描器

**技术栈**：`requests` + `BeautifulSoup` + `ThreadPoolExecutor`

- 4 个热点源并行扫描：
  - **Hacker News** — Firebase API (top 5-6 stories)
  - **GitHub Trending** — Web scraping 热门仓库
  - **微博热搜** — 3 策略 fallback (mobile API / hot_band / side panel)
  - **抖音热搜** — 2 策略 (iesdouyin / aweme API)
- 按源归一化热度 (200-1000) + 去重排序
- 硬超时保护 (`ThreadPoolExecutor` + `as_completed(timeout=15s)`)
- `requests.Session` 连接池复用，减少 TCP 握手开销

### 3. `app/webui.py` — FastAPI Web 前端

**技术栈**：`FastAPI` + `uvicorn` + 原生 JS

- 输入话题偏好文本框（留空=趋势驱动，填入=话题驱动）
- 趋势卡片实时展示 (4 源颜色标签：HN🟠 GH⚫ WB🔴 DY⚫)
- 实时轮询进度 (流式显示每个节点状态)
- HITL 点击选题 → 文章流式展示
- 趋势始终可见，不因输入话题而隐藏
- `/api/start` → `/api/status` → `/api/select` → done

### 4. `scanners/crawlers.py` — 独立爬虫测试工具

- 可单独运行 `python -m scanners.crawlers` 验证微博/抖音爬虫
- 各 API 端点独立测试，输出成功率

### 5. `servers/search_server.py` — MCP Bing Web Search 服务器

**技术栈**：Bing Search API v7 + MCP Stdio 协议（纯 MCP SDK，无 ADK）

- MCP Stdio 协议暴露 `web_search` 工具
- 在 `research_topic` 节点中被调用，为主题和各章节标题搜索实时数据
- 搜索结果注入 `write_section` prompt，解决 LLM 知识截止日期问题
- 环境变量 `BING_SEARCH_API_KEY` 控制开关，缺失时优雅跳过

---

## 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 编排框架 | LangGraph (StateGraph) | DAG 工作流 + Send() + HITL + Checkpoint |
| LLM | DeepSeek Chat (deepseek-chat) | 所有文本生成任务 |
| MCP 客户端 | mcp Python SDK (stdio_client) | Agent ↔ MCP 服务器进程间通信 |
| 数据源 | requests / BeautifulSoup | HN / GitHub / 微博 / 抖音 |
| 实时搜索 | Bing Search API v7 (via MCP) | writing 阶段实时调研 |
| 持久化 | langgraph.checkpoint.memory (MemorySaver) | HITL 断点恢复 |

---

## 快速开始

### 1. 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```powershell
copy .env.example .env
```

在 .env 中填入 DeepSeek API Key：

```ini
DEEPSEEK_API_KEY=sk-your-key-here
```

### 3. 运行

```powershell
# Web UI 模式 (推荐)
python -m app.webui
# 浏览器打开 http://localhost:8000

# 命令行模式
python run.py "你的话题"
# 或使用默认话题
python run.py
```

---

## Docker 部署

```powershell
# 1. 配置 API Key
copy .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY 和可选 BING_SEARCH_API_KEY

# 2. 构建并启动
docker compose up -d

# 3. 打开浏览器
# http://localhost:8000

# 4. 查看日志
docker compose logs -f
```

---

## 依赖清单

```txt
langgraph>=0.4.0
langchain-openai>=0.3.0
langchain-core>=0.3.0
openai>=1.0.0
python-dotenv>=1.0.0
mcp>=1.0.0
fastapi>=0.100.0
uvicorn>=0.20.0
requests>=2.28.0
beautifulsoup4>=4.12.0
lxml>=4.9.0
```

---

## 演化历程

| 阶段 | 框架 | 模型 | 状态 |
|------|------|------|------|
| 原型 (1.ipynb) | Google ADK | Gemini Flash | 已归档 |
| 初版 (agent.py) | Google ADK | Gemini Flash | 已删除 |
| v2 (agent2.py) | LangGraph 线性 DAG | DeepSeek Chat | 已升级 |
| v3 | LangGraph **多阶段 + Send() + HITL** | DeepSeek Chat | 已升级 |
| v4| **双模式选题 + 趋势常显 + 归一化热分 + Web UI** | **DeepSeek Chat** | 已升级 |
| **v5 (当前)** | **Join Barrier + LLM 质量评估 + MCP Bing 调研 + 移除 ADK** | **DeepSeek Chat** | **主力版本** |

演化路径：线性 prompt → ADK 层级 Agent → LangGraph 线性 DAG → LangGraph 多阶段 Send/HITL → v4 双模式+Web UI → **v5 工程化打磨**
