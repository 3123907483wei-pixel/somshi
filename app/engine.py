"""
LangGraph multi-agent blog writing system.
Architecture:
  Phase 1 ─ Send() → parallel multi-source trend scanning
  Phase 2 ─ Supervisor selects top-3 topics, Human-in-the-Loop confirms
  Phase 3 ─ Research → Outline → Send() parallel writing (Map-Reduce)
  Phase 4 ─ Merge → Validate → Finalize (titles + hooks)
"""

import asyncio, json, os, sys, time, re
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from typing import Annotated, Literal, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, Send
from langgraph.checkpoint.memory import MemorySaver
from typing_extensions import TypedDict

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Local trend scanner
from scanners.trends_scanner import scan_all as scan_all_trends, TrendItem

# ── 1. LLM 配置 ──────────────────────────────────────────────────────────────
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_KEY:
    raise ValueError("请设置环境变量 DEEPSEEK_API_KEY")
llm = ChatOpenAI(
    model="deepseek-chat",
    openai_api_key=DEEPSEEK_KEY,
    openai_api_base="https://api.deepseek.com",
    temperature=0.7,
)

# ── MCP Bing Web Search 工具（复用 search_server.py） ──────────────────────
@tool
async def bing_search_tool(query: str, count: int = 5, mkt: str = "zh-CN") -> str:
    """Search the web using Bing Search API via MCP server."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent.parent / "servers" / "search_server.py")],
    )
    async def fetch():
        async with stdio_client(server_params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                resp = await s.call_tool("web_search", arguments={"query": query, "count": count, "mkt": mkt})
                return resp.content[0].text if resp.content else "No data."
    try:
        return await fetch()
    except Exception as e:
        return f"MCP search call failed: {e}"


async def _fetch_mcp_search(topic: str, titles: list[str]) -> str:
    """Search Bing for the topic and each section, return compiled research."""
    parts = []
    queries = [topic] + titles[:4]
    for q in queries:
        try:
            raw = await bing_search_tool.ainvoke({"query": q, "count": 3, "region": "cn-zh"})
            data = json.loads(raw) if isinstance(raw, str) else raw
            if data.get("status") != "ok":
                continue
            items = data.get("results", [])
            if items:
                parts.append(f"=== {q} ===\n" + "\n".join(
                    f"- {r['title']}: {r['snippet'][:200]}" for r in items
                ))
        except Exception:
            continue
    return "\n\n".join(parts) if parts else ""


# ── AgentState ──────────────────────────────────────────────────────────────
def merge_dicts(a: dict, b: dict) -> dict:
    """Merge b into a (for parallel writes to article_sections)."""
    return {**a, **b}


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    topic: str
    topic_hint: str                    # 用户输入的话题偏好 (e.g. "美食探店")
    candidate_topics: list
    selected_index: int
    raw_trends: list
    trends_summary: str
    outline: str
    outline_attempts: int
    article_sections: Annotated[dict, merge_dicts]
    section_titles: list
    article: str
    article_attempts: int
    seo_titles: list
    tweet_hooks: list
    human_feedback: str
    quality_score: int

# ── 4. 节点函数 ──────────────────────────────────────────────────────────────

def scan_sources(state: AgentState) -> dict:
    """Phase 1: trend scanning. Always scan (background), hint changes only supervisor."""
    hint = state.get("topic_hint", "")
    print(f"\n{'='*50}\n📡 多源热点扫描\n{'='*50}")
    if hint:
        print(f"   🔍 用户兴趣: {hint} (趋势仅作背景参考)")

    t0 = time.time()
    results = scan_all_trends(max_items=8, global_timeout=20)
    elapsed = time.time() - t0
    print(f"  ⏱ 本地扫描: {elapsed:.1f}s ({len(results)} 条)")

    if not results:
        results = [
            {"title": "AI Agent 在企业级应用中的实践与挑战", "source": "default", "hot_score": 800},
            {"title": "大模型 RAG 技术的最新进展与优化策略", "source": "default", "hot_score": 750},
            {"title": "WebAssembly 在云原生时代的角色重定义", "source": "default", "hot_score": 700},
        ]
        print("  ⚠️ 所有热点源不可用, 使用默认候选话题")

    summary_lines = ["📊 热点扫描汇总:\n"]
    for i, item in enumerate(results[:30], 1):
        summary_lines.append(f"  {i}. [{item['source']:>10}] {item['title'][:60]}")
    summary = "\n".join(summary_lines)

    return {
        "raw_trends": results,
        "trends_summary": summary,
        "messages": [HumanMessage(content=summary)],
    }

def supervisor_select(state: AgentState) -> dict:
    """Phase 2: Supervisor LLM recommends top 3 topics."""
    trends = state.get("trends_summary", "")
    hint = state.get("topic_hint", "")

    if hint:
        prompt = f"""Generate 3 blog post topics about "{hint}".

For each topic provide:
- Title: a clickable blog title
- Reason: why this matters now (1 sentence)
- Angle: suggested writing angle (1-2 sentences)

Reply with each topic on separate lines like this example:

## TOPIC 1
Title: How Electric Toothbrushes Changed Oral Care Forever
Reason: Smart toothbrushes now track brushing habits via Bluetooth
Angle: Review top 3 smart toothbrushes, compare features, discuss price vs value

## TOPIC 2
Title: ...
Reason: ...
Angle: ...

## TOPIC 3
Title: ...
Reason: ...
Angle: ..."""
    else:
        prompt = f"""Based on these trends, recommend TOP 3 blog topics.

Trends:
{trends[:1200]}

For each topic provide Title, Reason, Angle. Reply like this:

## TOPIC 1
Title: ...
Reason: ...
Angle: ...

## TOPIC 2
...same format...
## TOPIC 3
...same format..."""

    t0 = time.time()
    msg = llm.invoke([HumanMessage(content=prompt)])
    elapsed = time.time() - t0
    nc = len(msg.content)
    print(f"\n🤖 Supervisor ({elapsed:.1f}s, {len(prompt)}→{nc}c)")

    # Robust parsing: split by "## TOPIC" headers (case-insensitive)
    content = msg.content.strip()
    print(content[:500])  # debug: always print supervisor output

    candidates = []
    blocks = re.split(r'##\s*TOPIC\s*\d*', content, flags=re.IGNORECASE)
    for block in blocks[1:]:
        item = {}
        # Handle both multi-line and inline " / " formats
        lines = block.strip().split("\n")
        if len(lines) == 1 and " / " in lines[0]:
            lines = lines[0].split(" / ")
        for line in lines:
            line = line.strip()
            # Strip markdown bold/italic markers like **Title:** or *Title:*
            clean = line.replace("**", "").replace("*", "")
            low = clean.lower()
            if low.startswith("title:"):
                item["title"] = clean[6:].strip()
            elif low.startswith("reason:"):
                item["reason"] = clean[7:].strip()
            elif low.startswith("angle:"):
                item["angle"] = clean[6:].strip()
        if item.get("title"):
            candidates.append(item)

    # Fallback: try parsing by "Title:" lines if no blocks found
    if not candidates:
        current = {}
        for line in content.split("\n"):
            line = line.strip()
            clean = line.replace("**", "").replace("*", "")
            if clean.startswith("## TOPIC"):
                if current.get("title"):
                    candidates.append(current)
                current = {}
            elif clean.lower().startswith("title:"):
                current["title"] = clean[6:].strip()
            elif clean.lower().startswith("reason:"):
                current["reason"] = clean[7:].strip()
            elif clean.lower().startswith("angle:"):
                current["angle"] = clean[6:].strip()
        if current.get("title"):
            candidates.append(current)

    return {
        "candidate_topics": candidates[:3],
        "messages": [HumanMessage(content=f"[Supervisor] {len(candidates)} topics")],
    }

def confirm_topic(state: AgentState) -> dict:
    """Phase 2.5: Human-in-the-Loop — user selects a topic.
    NOTE: The actual input() call happens in run_pipeline() between
    the two invoke() calls. This node processes the resume value."""
    candidates = state.get("candidate_topics", [])
    idx = state.get("selected_index", 0)
    if idx < 1 or idx > len(candidates):
        idx = 1
    selected = candidates[idx - 1]
    topic = selected.get("title", candidates[0].get("title", "默认话题"))
    print(f"\n{'='*50}")
    print(f"👤 话题已确认: {topic}")
    print(f"{'='*50}")
    return {
        "topic": topic,
        "selected_index": idx,
        "human_feedback": f"选择话题 {idx}: {topic}",
        "messages": [HumanMessage(content=f"[Human] 选择话题 #{idx}: {topic}")],
    }

def plan_outline(state: AgentState) -> dict:
    """Generate blog outline with 3-4 sections max (optimized)."""
    topic = state["topic"]
    print(f"\n{'='*50}\n📝 规划大纲: {topic}\n{'='*50}")
    prompt = f"""Topic: {topic}

Create a Markdown outline:
# {topic}
## short intro
## 3-4 main sections with bullet points (no generic placeholders)
## conclusion

Each H2 section ~500 words. Return ONLY the outline."""
    t0 = time.time()
    msg = llm.invoke([SystemMessage(content=prompt), HumanMessage(content=topic)])
    elapsed = time.time() - t0
    print(f"  ⏱ outline: {elapsed:.1f}s | {len(prompt)}→{len(msg.content)} chars")
    outline = msg.content

    titles = []
    for line in outline.split("\n"):
        if line.strip().startswith("## ") and not line.strip().startswith("### "):
            titles.append(line.strip()[3:].strip())

    return {
        "outline": outline,
        "section_titles": titles[:4],
        "outline_attempts": state.get("outline_attempts", 0) + 1,
    }

def validate_outline(state: AgentState) -> dict:
    """Fast check: does outline have H1, H2 sections, and non-empty?"""
    outline = state.get("outline", "")
    has_title = outline.strip().startswith("# ")
    h2_count = sum(1 for l in outline.split("\n") if l.strip().startswith("## "))
    ok = has_title and h2_count >= 2 and len(outline) > 100
    result = "OK" if ok else f"Retry: h1={has_title}, h2={h2_count}, len={len(outline)}"
    print(f"\n{'='*50}\n✅ 校验大纲: {result} ({h2_count} H2, {len(outline)} chars)\n{'='*50}")
    return {"messages": [HumanMessage(content=f"[Outline check] {result}")]}


def research_topic(state: AgentState) -> dict:
    """Phase 3.5: search Bing for real-time data on topic + sections via MCP."""
    topic = state["topic"]
    titles = state.get("section_titles", [])
    print(f"\n{'='*50}\n🔍 深度调研: {topic}\n{'='*50}")
    t0 = time.time()
    # 安全调用 async: 创建独立事件循环，避免 FastAPI 环境中炸
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        research = loop.run_until_complete(_fetch_mcp_search(topic, titles))
    finally:
        loop.close()
    elapsed = time.time() - t0
    if research:
        print(f"  ⏱ 调研: {elapsed:.1f}s, 获取 {len(research)} chars 参考信息")
    else:
        print(f"  ⚠️ 调研无结果，跳过")
    return {"research_data": research, "messages": [HumanMessage(content=f"[Research] {len(research)} chars")]}


def write_section(state: AgentState) -> dict:
    """NOT USED directly — sections are written via Send() in parallel_write."""
    raise NotImplementedError("Use parallel_write with Send() instead")


def parallel_write_section(section_title: str, topic: str, outline: str, research: str) -> dict:
    """Write a single H2 section (light context for speed)."""
    prompt = f"""Write ONE section about "{topic}". Section: "{section_title}"

300-500 words Markdown (H2 + sub-points). Stay on THIS section only. Include 1-2 code snippets if relevant."""

    if research:
        research_snippet = research[:800]
        prompt += f"""

Reference web search results (use for facts/data):
{research_snippet}"""

    t0 = time.time()
    msg = llm.invoke([HumanMessage(content=prompt)])
    elapsed = time.time() - t0
    print(f"    ✍️ 「{section_title[:25]}」: {elapsed:.1f}s ({len(msg.content)} chars)")
    return {section_title: msg.content.strip()}


def generate_section_tasks(state: AgentState) -> dict:
    """Map step node: print status and pass through."""
    titles = state.get("section_titles", [])
    print(f"\n{'='*50}\n🔀 准备并行展开 {len(titles)} 个章节\n{'='*50}")
    return {}


def route_to_sections(state: AgentState) -> list[Send]:
    """Map step router: generate a Send() task per H2 section."""
    titles = state.get("section_titles", [])
    topic = state["topic"]
    outline = state.get("outline", "")
    research = state.get("research_data", "")
    return [
        Send("write_section", {
            "section_title": title,
            "topic": topic,
            "outline": outline,
            "research": research,
        })
        for title in titles
    ]


def write_section(state: dict) -> dict:
    """Write a single section (called by Send() per task)."""
    section_title = state["section_title"]
    topic = state["topic"]
    outline = state["outline"]
    research = state.get("research", "")
    result = parallel_write_section(section_title, topic, outline, research)
    return {"article_sections": result}


def join_sections(state: AgentState) -> dict:
    """Barrier node: explicitly waits for all parallel write_section to complete.
    Reports how many sections were written before passing to merge."""
    sections = state.get("article_sections", {})
    print(f"\n{'='*50}\n🔗 join: {len(sections)} 个章节全部写入完毕\n{'='*50}")
    return {"messages": [HumanMessage(content=f"[Join] {len(sections)} sections complete")]}


def merge_and_polish(state: AgentState) -> dict:
    """Reduce step: merge all sections and polish into final article."""
    sections = state.get("article_sections", {})
    topic = state["topic"]
    print(f"\n{'='*50}\n🧩 合并 {len(sections)} 个章节\n{'='*50}")

    merged = f"# {topic}\n\n"
    for sec_title, content in sections.items():
        merged += f"\n{content}\n\n"

    prompt = f"""Merge these sections into a cohesive article about "{topic}".
Add an intro and conclusion if missing. Fix transitions between sections.
Keep Markdown formatting. Don't introduce new facts.

Sections:
{merged[:2500]}"""
    t0 = time.time()
    msg = llm.invoke([HumanMessage(content=prompt)])
    elapsed = time.time() - t0
    print(f"  ⏱ merge: {elapsed:.1f}s | {len(prompt)}→{len(msg.content)} chars")
    article = msg.content.strip()
    print(f"  📄 合并完成 ({len(article)} chars)")

    return {
        "article": article,
        "article_attempts": state.get("article_attempts", 0) + 1,
    }

def quality_evaluate(state: AgentState) -> dict:
    """LLM quality evaluator: scores article on 4 dimensions.
    Score < 8 → retry merge_and_polish (up to 3 attempts)."""
    article = state.get("article", "")
    topic = state["topic"]
    attempts = state.get("article_attempts", 0)
    print(f"\n{'='*50}\n🏅 LLM 质量评估 (第{attempts}次)\n{'='*50}")
    prompt = f"""Rate this article about "{topic}" on 4 dimensions (1-10 each):

- factuality: are claims backed by data/examples?
- structure: clear intro, body sections, conclusion?
- seo: is the title compelling, keywords used naturally?
- readability: fluent, scannable, engaging?

Article (first 1500 chars):
{article[:1500]}

Return ONLY a JSON object, no other text:
{{"factuality": N, "structure": N, "seo": N, "readability": N, "average": N}}"""
    t0 = time.time()
    msg = llm.invoke([HumanMessage(content=prompt)])
    elapsed = time.time() - t0
    content = msg.content.strip()

    m = re.search(r'average[\s:]+(\d+(?:\.\d+)?)', content)
    score = int(float(m.group(1))) if m else 5
    print(f"  ⏱ 评估: {elapsed:.1f}s | score={score}/10 | {content[:120]}")
    return {"quality_score": score}


def finalize(state: AgentState) -> dict:
    """Generate 3 alternative titles & 2 tweet hooks from the finished article."""
    article = state.get("article", "")
    topic = state["topic"]
    print(f"\n{'='*50}\n🏁 最终润色: {topic}\n{'='*50}")
    prompt = f"""Based on the article below about "{topic}", generate:

1. 3 alternative blog post titles (concise, clickable, SEO-friendly)
2. 2 tweet-length hooks (≤280 chars each, designed to drive engagement)

Return in this format:
## Alternative Titles
1. ...
2. ...
3. ...

## Tweet Hooks
1. ...
2. ...

Article:
{article[:1200]}"""
    t0 = time.time()
    msg = llm.invoke([HumanMessage(content=prompt)])
    elapsed = time.time() - t0
    print(f"  ⏱ finalize: {elapsed:.1f}s | {len(prompt)}→{len(msg.content)} chars")
    content = msg.content.strip()

    titles, hooks = [], []
    current = None
    for line in content.split("\n"):
        line = line.strip()
        if "alternative title" in line.lower() or line.startswith("## Alternative"):
            current = "titles"
        elif "tweet hook" in line.lower() or line.startswith("## Tweet"):
            current = "hooks"
        elif current == "titles" and line and line[0].isdigit():
            titles.append(line.split(".", 1)[-1].strip())
        elif current == "hooks" and line and line[0].isdigit():
            hooks.append(line.split(".", 1)[-1].strip())

    print(f"  替代标题: {titles}")
    print(f"  推文钩子: {hooks}")
    return {
        "seo_titles": titles[:3],
        "tweet_hooks": hooks[:2],
        "messages": [HumanMessage(content=f"[Finalize] Titles: {titles[:3]}, Hooks: {hooks[:2]}")],
    }

# ── 5. 路由逻辑 ──────────────────────────────────────────────────────────────

def route_after_confirm(state: AgentState) -> str:
    """After human confirms, proceed to research. If no selection, retry."""
    topic = state.get("topic", "")
    if topic and state.get("selected_index", 0) > 0:
        print(f"  → 话题已确认:「{topic[:40]}」，进入深度调研")
        return "plan_outline"
    print("  ↻ 未选择，重新请求")
    return "confirm_topic"

def route_after_validate_outline(state: AgentState) -> Literal["plan_outline", "research_topic"]:
    last = state["messages"][-1].content if state.get("messages") else ""
    if "retry" in last.lower() and state.get("outline_attempts", 0) < 3:
        print("  ↻ 大纲不合格，重试...")
        return "plan_outline"
    print("  → 大纲通过，进入深度调研")
    return "research_topic"

def route_after_quality_evaluate(state: AgentState) -> str:
    score = state.get("quality_score", 0)
    attempts = state.get("article_attempts", 0)
    if score < 8 and attempts < 3:
        print(f"  ↻ 质量分 {score}/10 < 8，第{attempts}次重试合并润色...")
        return "merge_and_polish"
    if score < 8:
        print(f"  ⚠ 已达最大重试次数 ({attempts})，接受当前质量")
    else:
        print(f"  → 质量分 {score}/10，通过")
    return "finalize"

# ── 6. 构建图 ────────────────────────────────────────────────────────────────

builder = StateGraph(AgentState)

# Phase 1: Trend scanning
builder.add_node("scan_sources", scan_sources)
# Phase 2: Supervisor + HITL
builder.add_node("supervisor_select", supervisor_select)
builder.add_node("confirm_topic", confirm_topic)
# Phase 3: Outline + Research + Write
builder.add_node("plan_outline", plan_outline)
builder.add_node("validate_outline", validate_outline)
builder.add_node("research_topic", research_topic)
# Phase 3: Map-Reduce parallel writing
builder.add_node("generate_section_tasks", generate_section_tasks)
builder.add_node("write_section", write_section)
builder.add_node("join_sections", join_sections)
builder.add_node("merge_and_polish", merge_and_polish)
# Phase 4: Quality evaluate + Finalize
builder.add_node("quality_evaluate", quality_evaluate)
builder.add_node("finalize", finalize)

# ── Edges ──
builder.add_edge(START, "scan_sources")
builder.add_edge("scan_sources", "supervisor_select")
builder.add_edge("supervisor_select", "confirm_topic")
builder.add_conditional_edges("confirm_topic", route_after_confirm)
builder.add_edge("confirm_topic", "plan_outline")
builder.add_edge("plan_outline", "validate_outline")
builder.add_conditional_edges("validate_outline", route_after_validate_outline)

# research → Send() fan-out
builder.add_edge("research_topic", "generate_section_tasks")
# Send() fan-out: one task per H2 section (router function returns list[Send])
builder.add_conditional_edges("generate_section_tasks", route_to_sections, ["write_section"])
# Explicit join node: waits for all Send() branches
builder.add_edge("write_section", "join_sections")
builder.add_edge("join_sections", "merge_and_polish")
# merge → quality eval → conditional retry or finalize
builder.add_edge("merge_and_polish", "quality_evaluate")
builder.add_conditional_edges("quality_evaluate", route_after_quality_evaluate)
builder.add_edge("finalize", END)

checkpointer = MemorySaver()
agent = builder.compile(checkpointer=checkpointer, interrupt_before=["confirm_topic"])

# ── 7. 运行入口 ──────────────────────────────────────────────────────────────

def run_pipeline(topic_hint: str = ""):
    """Run the full pipeline with Human-in-the-Loop support (two-step invoke)."""
    initial = {
        "topic": topic_hint or "AI 技术最新热点",
        "topic_hint": topic_hint,
        "candidate_topics": [],
        "selected_index": 0,
        "raw_trends": [],
        "trends_summary": "",
        "research_data": "",
        "outline": "",
        "outline_attempts": 0,
        "article_sections": {},
        "section_titles": [],
        "article": "",
        "article_attempts": 0,
        "quality_score": 0,
        "seo_titles": [],
        "tweet_hooks": [],
        "human_feedback": "",
    }

    print(f"\n{'#'*60}")
    print(f"# 🚀 LangGraph 多 Agent 博客写作系统")
    print(f"#    模式: 多源扫描 → Supervisor → HITL → Map-Reduce写作")
    print(f"{'#'*60}\n")

    config = {"configurable": {"thread_id": "blog-agent-001"}}
    t_start = time.time()

    # ── Step 1: Run until HITL interrupt ──
    state = agent.invoke(initial, config=config)

    # ── Step 2: Human-in-the-Loop ──
    candidates = state.get("candidate_topics", [])
    print(f"\n{'='*55}")
    print("  [HITL] Human-in-the-Loop: 请选择博客话题")
    print(f"{'='*55}")
    if candidates:
        for i, c in enumerate(candidates, 1):
            title = c.get('title', '?')[:60]
            reason = c.get('reason', '')[:80]
            print(f"\n  ({i}) {title}")
            print(f"      理由: {reason}")
        print()
        raw = input("  请输入编号 1/2/3 (直接回车=1): ").strip()
        try:
            idx = int(raw) if raw else 1
            if idx < 1 or idx > len(candidates):
                idx = 1
        except ValueError:
            idx = 1
    else:
        print("  (无候选话题, 使用默认主题)")
        idx = 1

    # ── Step 3: Resume with user's choice ──
    state = agent.invoke(Command(resume={"selected_index": idx}), config=config)

    t_total = time.time() - t_start

    # ── Output ──
    print(f"\n{'='*60}")
    print(f"📄 最终文章 ({len(state.get('article', ''))} chars)")
    print(f"{'='*60}")
    article = state.get("article", "")
    print(article if article else "（无输出）")

    titles = state.get("seo_titles", [])
    hooks = state.get("tweet_hooks", [])
    if titles:
        print(f"\n{'='*50}")
        print(f"🏷️  替代标题建议")
        for i, t in enumerate(titles, 1):
            print(f"  {i}. {t}")
    if hooks:
        print(f"\n{'='*50}")
        print(f"🐦 推文钩子")
        for i, h in enumerate(hooks, 1):
            print(f"  {i}. {h}")

    print(f"\n{'='*50}")
    print(f"⏱ 总耗时: {t_total:.1f}s")
    print(f"{'='*50}")

    return state


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else ""
    run_pipeline(topic)