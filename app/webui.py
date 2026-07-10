"""
Simple Web UI for the LangGraph blog writer.
Run: python webui.py
Then open http://localhost:8000
"""

import json
import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# Ensure parent dir is in path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.engine import agent, run_pipeline, AgentState
from langgraph.types import Command

app = FastAPI(title="Blog Writer Web UI")

# ── In-memory state ────────────────────────────────────────────────────────
pipeline_state = {
    "phase": "idle",           # idle | scanning | selecting | generating | done | error
    "state": None,             # AgentState dict
    "thread_config": {"configurable": {"thread_id": "web-blog-001"}},
    "initial": None,
    "error": None,
    "started_at": None,
}


class SelectRequest(BaseModel):
    index: int

class StartRequest(BaseModel):
    topic_hint: str = ""


# ── API routes ─────────────────────────────────────────────────────────────

@app.get("/api/status")
def get_status():
    """Return current pipeline status."""
    s = pipeline_state
    state = s["state"]
    result = {
        "phase": s["phase"],
        "progress": s.get("progress", ""),
        "error": s["error"],
        "started_at": s.get("started_at"),
    }

    if state:
        result["candidate_topics"] = state.get("candidate_topics", [])
        result["topic"] = state.get("topic", "")
        result["article"] = state.get("article", "")
        art_len = len(state.get("article", ""))
        result["article_length"] = art_len
        result["seo_titles"] = state.get("seo_titles", [])
        result["tweet_hooks"] = state.get("tweet_hooks", [])
        result["elapsed"] = round(time.time() - s.get("started_at", time.time()), 1) if s.get("started_at") else 0
        result["raw_trends"] = state.get("raw_trends", [])[:10]

    return result


@app.post("/api/start")
def start_pipeline(req: StartRequest = StartRequest()):
    """Phase 1+2: Scan sources + Supervisor analysis. Stops at HITL."""
    if pipeline_state["phase"] not in ("idle", "done", "error"):
        raise HTTPException(400, "Pipeline already running")

    pipeline_state["phase"] = "scanning"
    pipeline_state["error"] = None
    pipeline_state["started_at"] = time.time()
    hint = req.topic_hint if hasattr(req, 'topic_hint') else ""

    def _run():
        try:
            initial = {
                "topic": hint or "AI 技术最新热点",
                "topic_hint": hint,
                "candidate_topics": [], "selected_index": 0,
                "raw_trends": [], "trends_summary": "",
                "outline": "", "outline_attempts": 0,
                "article_sections": {}, "section_titles": [],
                "article": "", "article_attempts": 0,
                "seo_titles": [], "tweet_hooks": [], "human_feedback": "",
            }
            pipeline_state["initial"] = initial
            config = pipeline_state["thread_config"]

            # First invoke — runs scan_sources → supervisor_select, stops before confirm_topic
            state = agent.invoke(initial, config=config)
            pipeline_state["state"] = state
            pipeline_state["phase"] = "selecting"
        except Exception as e:
            pipeline_state["phase"] = "error"
            pipeline_state["error"] = str(e)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"status": "scanning"}


@app.post("/api/select")
def select_topic(req: SelectRequest):
    """Phase 2.5: HITL — user selects a topic. Resumes pipeline."""
    if pipeline_state["phase"] != "selecting":
        raise HTTPException(400, "Not in selecting phase")

    pipeline_state["phase"] = "generating"
    pipeline_state["progress"] = "writing"

    def _resume():
        try:
            config = pipeline_state["thread_config"]
            idx = req.index

            # Stream events for real-time progress + accumulate full state
            events = agent.stream(
                Command(resume={"selected_index": idx}),
                config=config,
                stream_mode="updates",
            )
            final_state = {}
            for event in events:
                for node_name, node_state in event.items():
                    progress_map = {
                        "research_deep": "researching",
                        "plan_outline": "outlining",
                        "generate_section_tasks": "dispatching",
                        "write_section": "writing_sections",
                        "merge_and_polish": "merging",
                        "join_sections": "joining",
                        "quality_evaluate": "evaluating_quality",
                        "finalize": "finalizing",
                    }
                    if node_name in progress_map:
                        pipeline_state["progress"] = progress_map[node_name]
                    if isinstance(node_state, dict):
                        # Merge incremental updates (each node only returns its own keys)
                        final_state = {**final_state, **node_state}
                        # Also update article_sections specially (merge dict keys)
                        if "article_sections" in node_state:
                            if "article_sections" not in final_state:
                                final_state["article_sections"] = {}
                            final_state["article_sections"].update(node_state["article_sections"])
                        pipeline_state["state"] = final_state

            pipeline_state["state"] = final_state
            pipeline_state["phase"] = "done"
            pipeline_state["progress"] = "complete"
        except Exception as e:
            pipeline_state["phase"] = "error"
            pipeline_state["progress"] = "error"
            pipeline_state["error"] = str(e)

    thread = threading.Thread(target=_resume, daemon=True)
    thread.start()
    return {"status": "generating"}


@app.post("/api/reset")
def reset_pipeline():
    """Reset pipeline to idle."""
    pipeline_state["phase"] = "idle"
    pipeline_state["state"] = None
    pipeline_state["error"] = None
    pipeline_state["started_at"] = None
    return {"status": "reset"}


# ── Frontend ───────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blog Writer - 多 Agent 博客写作系统</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #f5f5f7; color: #1d1d1f; }
  .container { max-width: 900px; margin: 0 auto; padding: 20px; }
  h1 { font-size: 1.8rem; margin: 20px 0; }
  .card { background: #fff; border-radius: 12px; padding: 20px; margin: 12px 0; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .card h2 { font-size: 1.1rem; margin-bottom: 10px; color: #1d1d1f; }
  .topic-btn { display: block; width: 100%; padding: 12px 16px; margin: 8px 0; border: 1px solid #d2d2d7; border-radius: 8px;
    background: #fff; text-align: left; cursor: pointer; font-size: .95rem; transition: all .2s; }
  .topic-btn:hover { border-color: #0071e3; background: #f0f7ff; }
  .topic-btn .reason { font-size: .8rem; color: #6e6e73; margin-top: 4px; }
  .topic-btn .badge { display: inline-block; background: #0071e3; color: #fff; border-radius: 12px; padding: 2px 8px; font-size: .7rem; margin-right: 6px; }
  .btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: .95rem; transition: all .2s; }
  .btn-primary { background: #0071e3; color: #fff; }
  .btn-primary:hover { background: #0077ed; }
  .btn-primary:disabled { opacity: .5; cursor: not-allowed; }
  .btn-secondary { background: #e8e8ed; color: #1d1d1f; }
  .btn-secondary:hover { background: #d2d2d7; }
  .status-bar { background: #e8e8ed; border-radius: 8px; padding: 12px 16px; margin: 12px 0; font-size: .9rem; }
  .status-bar.active { background: #e8f5e9; color: #2e7d32; }
  .status-bar.waiting { background: #fff3e0; color: #e65100; }
  .status-bar.error { background: #ffebee; color: #c62828; }
  .article { white-space: pre-wrap; line-height: 1.7; font-size: .95rem; max-height: 600px; overflow-y: auto; padding: 16px; background: #fafafa; border-radius: 8px; }
  .meta-list { list-style: none; padding: 0; }
  .meta-list li { padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
  .meta-list li:last-child { border: none; }
  .trend-item { display: flex; justify-content: space-between; padding: 4px 0; font-size: .85rem; }
  .trend-item .score { color: #6e6e73; }
  .hidden { display: none; }
  #loading { text-align: center; padding: 40px; color: #6e6e73; }
  .poll { display: inline-block; width: 12px; height: 12px; border: 2px solid #0071e3; border-top-color: transparent;
    border-radius: 50%; animation: spin .6s linear infinite; margin-right: 8px; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="container">
  <h1>📝 多 Agent 博客写作系统</h1>
  <p style="color:#6e6e73; margin-bottom: 20px;">多源扫描 → Supervisor 选题 → HITL → Map-Reduce 并行写作</p>

  <div id="status-bar" class="status-bar">⏳ 就绪 — 点击"开始扫描"启动</div>

  <div id="phase-start" class="card">
    <input id="topic-input" type="text" placeholder="想写什么话题？(选填, e.g. 美食探店/量子计算)" style="width:100%;padding:10px 12px;border:1px solid #d2d2d7;border-radius:8px;font-size:.9rem;margin-bottom:10px">
    <button class="btn btn-primary" onclick="startScan()" id="start-btn">🚀 开始扫描热点</button>
  </div>

  <div id="phase-trends" class="card hidden">
    <h2>📡 热点趋势</h2>
    <div id="trends-list"></div>
  </div>

  <div id="phase-topics" class="card hidden">
    <h2>🤖 Supervisor 推荐选题</h2>
    <p style="color:#6e6e73; font-size:.85rem; margin-bottom: 10px;">请选择您想撰写的话题：</p>
    <div id="topics-list"></div>
  </div>

  <div id="phase-article" class="card hidden">
    <h2>📄 最终文章</h2>
    <div id="loading-section" class="hidden"><span class="poll"></span> 生成中，请稍候...</div>
    <div id="article-content" class="article"></div>
  </div>

  <div id="phase-output" class="card hidden">
    <h2>🏷️ 替代标题 &amp; 推文钩子</h2>
    <ul id="output-list" class="meta-list"></ul>
  </div>

  <div style="margin-top: 20px; text-align: center;">
    <button class="btn btn-secondary" onclick="resetPipeline()">🔄 重新开始</button>
  </div>
</div>

<script>
let pollTimer = null;

function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }

function setStatus(msg, type) {
  const el = document.getElementById('status-bar');
  el.textContent = msg;
  el.className = 'status-bar';
  if (type) el.classList.add(type);
}

async function startScan() {
  document.getElementById('start-btn').disabled = true;
  setStatus('⏳ 扫描中... (约 20 秒)', 'active');
  hide('phase-trends'); hide('phase-topics'); hide('phase-article'); hide('phase-output');

  const hint = document.getElementById('topic-input').value.trim();
  try {
    const r = await fetch('/api/start', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({topic_hint: hint}) });
    const d = await r.json();
    if (d.status === 'scanning') {
      pollTimer = setInterval(pollStatus, 1000);
    } else {
      setStatus('❌ ' + (d.detail || JSON.stringify(d)), 'error');
      document.getElementById('start-btn').disabled = false;
    }
  } catch(e) {
    setStatus('❌ 启动失败: ' + e.message, 'error');
    document.getElementById('start-btn').disabled = false;
  }
}

async function pollStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();

    if (d.phase === 'selecting') {
      clearInterval(pollTimer);
      setStatus('👤 Human-in-the-Loop — 请选择话题', 'waiting');

      // Show trends
      if (d.raw_trends && d.raw_trends.length) {
        const sourceIcons = { hackernews: 'HN', github: 'GH', reddit: 'Rd', weibo: 'WB', douyin: 'DY', default: '?' };
        const sourceColors = { hackernews: '#ff6600', github: '#333', reddit: '#ff4500', weibo: '#e6162d', douyin: '#333' };
        const t = document.getElementById('trends-list');
        t.innerHTML = d.raw_trends.map(item => {
          const icon = sourceIcons[item.source] || item.source.slice(0,2);
          const color = sourceColors[item.source] || '#666';
          return `<div class="trend-item">
            <span><span style="display:inline-block;background:${color};color:#fff;border-radius:4px;padding:1px 6px;font-size:.7rem;font-weight:bold;margin-right:6px">${icon}</span>${item.title}</span>
            <span class="score">${item.hot_score}</span>
          </div>`;
        }).join('');
        show('phase-trends');
      }

      // Show topics
      if (d.candidate_topics && d.candidate_topics.length) {
        const el = document.getElementById('topics-list');
        el.innerHTML = d.candidate_topics.map((c, i) =>
          `<button class="topic-btn" onclick="selectTopic(${i+1})">
            <span class="badge">#${i+1}</span><strong>${c.title || '?'}</strong>
            <div class="reason">${c.reason ? c.reason.slice(0,120) : ''}</div>
          </button>`
        ).join('');
        show('phase-topics');
      }
      document.getElementById('start-btn').disabled = false;
    }
    else if (d.phase === 'generating') {
      const progressMap = {
        'researching': '🔬 深度调研中...',
        'outlining': '📝 生成大纲中...',
        'validating_outline': '✅ 校验大纲中...',
        'dispatching': '🔀 分发并行写作任务...',
        'writing_sections': '✍️ 并行写作各章节中...',
        'merging': '🧩 合并润色中...',
        'validating_article': '✅ 校验文章中...',
        'finalizing': '🏁 最终润色中...',
        'writing': '⏳ 生成中...',
      };
      const msg = progressMap[d.progress] || '⏳ 生成中...';
      setStatus(msg, 'active');
      show('phase-article');
      document.getElementById('loading-section').classList.remove('hidden');
    }
    else if (d.phase === 'done') {
      clearInterval(pollTimer);
      setStatus('✅ 文章生成完成! 耗时 ' + (d.elapsed || '?') + 's', 'active');
      document.getElementById('loading-section').classList.add('hidden');

      if (d.article) {
        document.getElementById('article-content').textContent = d.article;
        show('phase-article');
      }

      if (d.seo_titles || d.tweet_hooks) {
        const el = document.getElementById('output-list');
        const items = [];
        if (d.seo_titles) d.seo_titles.forEach((t, i) => items.push(`🏷️ 替代标题 ${i+1}: ${t}`));
        if (d.tweet_hooks) d.tweet_hooks.forEach((h, i) => items.push(`🐦 推文钩子 ${i+1}: ${h}`));
        el.innerHTML = items.map(s => `<li>${s}</li>`).join('');
        show('phase-output');
      }
    }
    else if (d.phase === 'error') {
      clearInterval(pollTimer);
      setStatus('❌ 错误: ' + (d.error || '未知错误'), 'error');
      document.getElementById('start-btn').disabled = false;
    }
  } catch(e) {
    // ignore poll errors
  }
}

async function selectTopic(idx) {
  clearInterval(pollTimer);
  setStatus('⏳ 已选择 #' + idx + ', 生成文章中...', 'active');
  hide('phase-topics');
  show('phase-article');
  document.getElementById('loading-section').classList.remove('hidden');

  try {
    await fetch('/api/select', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({index: idx}) });
    pollTimer = setInterval(pollStatus, 1000);
  } catch(e) {
    setStatus('❌ 选择失败: ' + e.message, 'error');
  }
}

async function resetPipeline() {
  clearInterval(pollTimer);
  await fetch('/api/reset', { method: 'POST' });
  setStatus('⏳ 就绪 — 点击"开始扫描"启动', '');
  hide('phase-trends'); hide('phase-topics'); hide('phase-article'); hide('phase-output');
  document.getElementById('article-content').textContent = '';
  document.getElementById('start-btn').disabled = false;
}
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return HTMLResponse(HTML)


if __name__ == "__main__":
    print("🚀 Web UI starting at http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
