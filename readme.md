# LangGraph 多 Agent 博客写作系统

基于 **LangGraph + MCP + DeepSeek** 的企业级自动化博客写作系统。

**解决痛点**: 热点捕捉慢 / 长文创作周期长 / 多源数据整合难

**核心能力**:
- 4 源热点扫描 (HackerNews / GitHub Trending / 微博 / 抖音)，**始终展示**趋势卡片
- **双模式选题**：留空 = 趋势驱动 / 输入话题 = 话题驱动 (纯 LLM 知识，不关联趋势)
- Human-in-the-Loop 人工审校确认
- Map-Reduce 并行扩写 (Send() fan-out)
- Checkpoint 断点恢复
- SEO 标题 + 推文钩子输出
- **Web UI** 实时流式进度 + 趋势卡片 + 点击选题

**性能指标 (v4 实测)**:
| 阶段 | 节点 | LLM | 耗时 | Token |
|------|------|-----|------|-------|
| 扫描 | `scan_sources` | ❌ | ~6s | — |
| 选题 | `supervisor_select` | ✅ | ~10s | ~1200 in / 500 out |
| 大纲 | `plan_outline` | ✅ | ~8s | ~300 in / 300 out |
| 并行写 | `write_section` × 3-4 | ✅ | ~15s | 4×300 in / 1800 out |
| 合并 | `merge_and_polish` | ✅ | ~25s | ~2500 in / 1500 out |
| 润色 | `finalize` | ✅ | ~5s | ~1200 in / 200 out |
| **合计** | | | **~65s** | **~8K in / 4.3K out** |

---

## 目录

- [核心工作流](#核心工作流)
- [两个核心模块](#两个核心模块)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [依赖清单](#依赖清单)
- [演化历程](#演化历程)

---

## 核心工作流

v4 优化版：**11 个节点, 4 个阶段**，双模式选题。砍掉了 `research_deep`，validator 改为非 LLM 快检，趋势始终展示：

```
Phase 1 ─ scan_sources ── ThreadPool 并行 4 源扫描 (始终执行)
Phase 2 ─ supervisor_select ── 有 hint → 纯 LLM 知识选题 / 无 hint → 基于趋势选题
       ─ [interrupt_before] ── HITL 选题确认
Phase 3 ─ plan_outline ── 大纲 (3-4 节)
       ─ validate_outline ── 快检 (非 LLM)
       ─ Send() 并行写各节 (Map)
       ─ merge_and_polish (Reduce)
Phase 4 ─ validate_blog ── 快检 (非 LLM)
       ─ finalize ── 标题 + 推文钩子 → END
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
| `write_section` × 3-4 | Send() 并行各写 300-500 字 | ✅ | ~12s |
| `merge_and_polish` | Reduce: 合并润色 | ✅ | ~20s |
| `validate_blog` | 非 LLM 快检 (长度/截断) | ❌ | <1s |
| `finalize` | 3 标题 + 2 推文钩子 | ✅ | ~6s |

### 关键 LangGraph 特性

| 模式 | 实现 | 说明 |
|------|------|------|
| **Send() Map** | `scan_sources` + `write_section` | 并行多源扫描 + 按章节并行写作 |
| **Reduce** | `merge_and_polish` | 合并各节成完整文章 |
| **Supervisor** | `supervisor_select` | LLM 分析热点推荐选题 |
| **Human-in-the-Loop** | `confirm_topic` + `interrupt_before` + `MemorySaver` | 用户选择话题, 可断点恢复 |
| **Conditional Edge** | `validate_outline` / `validate_blog` | 校验失败自动重试 ×3 |
| **Checkpoint** | `MemorySaver` | 中断点可恢复执行 |
| **状态剪枝** | `article_sections` 使用 `Annotated[dict, merge_dicts]` | 支持并行写入合并 |

---

## 三个核心模块

### 1. `agent2.py` — LangGraph 工作流引擎（核心）

**技术栈**：`langgraph` + `langchain-openai` + `langchain-core` + `mcp`

- 11 个节点的 StateGraph, 覆盖 Map-Reduce / Supervisor / HITL
- `Send()` API 实现并行 fan-out（多源扫描 + 按节写作）
- `interrupt_before` + `Command(resume=...)` 实现 HITL
- `MemorySaver` checkpointer 支持断点恢复
- `Annotated[dict, merge_dicts]` reducer 支持并行写入状态合并
- 条件边实现重试环路（格式坍塌防御）
- Supervisor prompt 分支：有 topic_hint → 纯 LLM 知识 / 无 hint → 基于趋势

### 2. `trends_scanner.py` — 多源热点扫描器

**技术栈**：`requests` + `BeautifulSoup` + `ThreadPoolExecutor`

- 4 个热点源并行扫描：
  - **Hacker News** — Firebase API (top 5-6 stories)
  - **GitHub Trending** — Web scraping 热门仓库
  - **微博热搜** — 3 策略 fallback (mobile API / hot_band / side panel)
  - **抖音热搜** — 2 策略 (iesdouyin / aweme API)
- 按源归一化热度 (200-1000) + 去重排序
- 硬超时保护 (`ThreadPoolExecutor` + `as_completed(timeout=15s)`)

### 3. `webui.py` — FastAPI Web 前端

**技术栈**：`FastAPI` + `uvicorn` + 原生 JS

- 输入话题偏好文本框（留空=趋势驱动，填入=话题驱动）
- 趋势卡片实时展示 (4 源颜色标签：HN🟠 GH⚫ WB🔴 DY⚫)
- 实时轮询进度 (流式显示每个节点状态)
- HITL 点击选题 → 文章流式展示
- 趋势始终可见，不因输入话题而隐藏
- `/api/start` → `/api/status` → `/api/select` → done

### 4. `crawlers.py` — 独立爬虫测试工具

- 可单独运行 `python crawlers.py` 验证微博/抖音爬虫
- 各 API 端点独立测试，输出成功率

### 5. `server.py` — MCP Google Trends 服务器

**技术栈**：`pytrends` + MCP low-level API + Google ADK FunctionTool

- MCP Stdio 协议暴露 `trends` 工具
- 三级 Fallback: related_queries → trending_searches → realtime_trending

---

## 技术栈

| 层 | 技术 | 用途 |
|----|------|------|
| 编排框架 | LangGraph (StateGraph) | DAG 工作流 + Send() + HITL + Checkpoint |
| LLM | DeepSeek Chat (deepseek-chat) | 所有文本生成任务 |
| MCP 客户端 | mcp Python SDK (stdio_client) | Agent ↔ server.py 进程间通信 |
| MCP 服务器 | mcp low-level + ADK FunctionTool | 封装 pytrends 为工具 |
| 数据源 | pytrends / requests / BS4 | Google Trends + 多源热点 |
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
python webui.py
# 浏览器打开 http://localhost:8000

# 命令行模式
python agent2.py "你的话题"
# 或使用默认话题
python agent2.py
```

---

## 依赖清单

```txt
langgraph>=0.4.0
langchain-openai>=0.3.0
langchain-core>=0.3.0
openai>=1.0.0
pytrends>=4.9.0
pandas>=1.0.0
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
| **v3 (当前)** | LangGraph **多阶段 + Send() + HITL** | DeepSeek Chat | 已升级 |
| **v4 (当前)** | **双模式选题 + 趋势常显 + 归一化热分 + Web UI** | **DeepSeek Chat** | **主力版本** |

演化路径：线性 prompt → ADK 层级 Agent → LangGraph 线性 DAG → LangGraph 多阶段 Send/HITL → **v4 双模式 + Web UI**
