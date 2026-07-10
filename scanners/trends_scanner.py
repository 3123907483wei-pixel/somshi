"""
Multi-source trend scanner: Hacker News, Reddit, Weibo, Douyin.
Each source scanner returns a list of TrendItem dicts.
scan_all() runs all scanners in parallel via asyncio.gather and returns merged results.
"""

import asyncio
import json
import time
import warnings
from typing import List, Optional
from dataclasses import dataclass, field, asdict

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# ── Common data type ────────────────────────────────────────────────────────

@dataclass
class TrendItem:
    title: str
    source: str          # "hackernews" | "reddit" | "weibo" | "douyin"
    url: str = ""
    hot_score: int = 0   # 热度分（归一化 0-1000）
    description: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ── HTTP helpers ────────────────────────────────────────────────────────────

TIMEOUT = (2.0, 3.0)  # (connect, read) seconds — aggressive fast fail

# Shared Session for connection reuse (TCP keep-alive, reduces handshake overhead)
_http = requests.Session()
_http.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def _get(url: str, headers: Optional[dict] = None) -> Optional[requests.Response]:
    """GET via shared Session — reuses TCP connection for same host."""
    try:
        resp = _http.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 429):
            return None
        return None
    except Exception:
        return None


# ── 1. Hacker News ─────────────────────────────────────────────────────────

def scan_hackernews(max_items: int = 5) -> List[TrendItem]:
    """Fetch top stories from Hacker News. Top 15 only for speed."""
    items: List[TrendItem] = []
    resp = _get("https://hacker-news.firebaseio.com/v0/topstories.json")
    if not resp:
        return items
    ids = resp.json()[:max_items]

    for sid in ids:
        item_resp = _get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
        if not item_resp:
            continue
        data = item_resp.json()
        if not data or not data.get("title"):
            continue
        score = data.get("score", 0)
        items.append(TrendItem(
            title=data["title"],
            source="hackernews",
            url=data.get("url", f"https://news.ycombinator.com/item?id={sid}"),
            hot_score=min(score, 1000),
            description=data.get("text", "")[:200],
            timestamp=data.get("time", 0),
        ))
    return items


# ── 2. GitHub Trending ──────────────────────────────────────────────────────

def scan_github(max_items: int = 10) -> List[TrendItem]:
    """Fetch GitHub trending repositories (accessible in China)."""
    items: List[TrendItem] = []
    for lang in ["", "python", "javascript", "typescript", "rust"]:
        url = f"https://github.com/trending/{lang}?since=weekly"
        resp = _get(url)
        if not resp:
            continue
        try:
            soup = BeautifulSoup(resp.text, "lxml")
            for article in soup.select("article.Box-row")[:max_items]:
                h2 = article.select_one("h2")
                if not h2:
                    continue
                repo_name = h2.get_text(strip=True).replace(" ", "")
                desc_tag = article.select_one("p")
                desc = desc_tag.get_text(strip=True)[:120] if desc_tag else ""
                stars_tag = article.select_one(".octicon-star")
                parent = stars_tag.parent if stars_tag else None
                stars = 500
                if parent:
                    try:
                        stars = int(parent.get_text(strip=True).replace(",", ""))
                    except ValueError:
                        stars = 500
                items.append(TrendItem(
                    title=repo_name,
                    source="github",
                    url=f"https://github.com/{repo_name}",
                    hot_score=min(stars, 1000),
                    description=desc,
                ))
            if items:
                break  # Got data, stop trying other languages
        except Exception:
            continue
    return items


# ── 3. Reddit ───────────────────────────────────────────────────────────────

REDDIT_SUBREDDITS = ["programming", "technology"]

def scan_reddit(max_items: int = 15) -> List[TrendItem]:
    """Fetch hot posts from Reddit (fast-scan, 1 strategy only)."""
    items: List[TrendItem] = []
    per_sub = max(1, max_items // len(REDDIT_SUBREDDITS))

    for sub in REDDIT_SUBREDDITS:
        for url in [
            f"https://old.reddit.com/r/{sub}/hot.json?limit={per_sub}&raw_json=1",
            f"https://www.reddit.com/r/{sub}/hot/.rss",
        ]:
            resp = _get(url)
            if not resp:
                continue
            # RSS returns XML
            if not resp:
                continue
            try:
                data = resp.json()
                for post in data.get("data", {}).get("children", []):
                    p = post.get("data", {})
                    title = p.get("title", "")
                    if not title:
                        continue
                    score = p.get("score", 0)
                    items.append(TrendItem(
                        title=title, source="reddit",
                        url=f"https://reddit.com{p.get('permalink', '')}",
                        hot_score=min(score, 1000),
                        description=f"r/{sub} · {p.get('num_comments', 0)} comments",
                    ))
                break
            except Exception:
                # RSS fallback
                try:
                    soup = BeautifulSoup(resp.text, "lxml-xml")
                    for e in soup.select("entry") or soup.select("item"):
                        t = e.find("title")
                        l = e.find("link")
                        if t:
                            items.append(TrendItem(
                                title=t.text.strip(), source="reddit",
                                url=l.get("href","") if l else "",
                                hot_score=500, description=f"r/{sub}",
                            ))
                    if items:
                        break
                except Exception:
                    continue

    return items


# ── 3. 微博热搜 ─────────────────────────────────────────────────────────────
#    (imports crawlers.py internal module, reimplements inline for zero-deps)

def scan_weibo(max_items: int = 30) -> List[TrendItem]:
    """Fetch Weibo hot search — 3 API strategies with proper browser headers."""
    items: List[TrendItem] = []
    hdrs = {"Referer": "https://m.weibo.cn/"}

    # Strategy 1: mobile API
    url = ("https://m.weibo.cn/api/container/getIndex?"
           "containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot")
    resp = _get(url, headers=hdrs)
    if resp:
        try:
            for card in resp.json().get("data", {}).get("cards", []):
                if card.get("card_type") == 11:
                    continue
                for entry in (card.get("card_group") or []):
                    t = entry.get("desc", "").strip()
                    if not t:
                        continue
                    hot = entry.get("desc_extr", 0) or 0
                    items.append(TrendItem(
                        title=t, source="weibo", hot_score=min(hot // 10000, 1000),
                        description=f"微博热搜 · {hot}", url=entry.get("scheme", "")))
            if items:
                return items[:max_items]
        except Exception:
            pass

    # Strategy 2: desktop hot_band API
    resp = _get("https://weibo.com/ajax/statuses/hot_band", headers={"Referer": "https://weibo.com/"})
    if resp:
        try:
            for e in resp.json().get("data", {}).get("band_list", []):
                t = (e.get("word") or e.get("note", "")).strip()
                if not t:
                    continue
                hot = e.get("raw_hot", 0) or e.get("num", 0)
                items.append(TrendItem(
                    title=t, source="weibo", hot_score=min(hot // 10000, 1000),
                    description=f"微博热搜 · {hot}",
                    url=f"https://s.weibo.com/weibo?q={requests.utils.quote(t)}"))
            if items:
                return items[:max_items]
        except Exception:
            pass

    # Strategy 3: desktop side panel
    resp = _get("https://weibo.com/ajax/side/hotSearch", headers={"Referer": "https://weibo.com/"})
    if resp:
        try:
            for e in resp.json().get("data", {}).get("realtime", []):
                t = (e.get("word") or e.get("word_scheme", "")).strip()
                if not t:
                    continue
                hot = e.get("raw_hot", 0) or e.get("num", 0)
                items.append(TrendItem(
                    title=t, source="weibo", hot_score=min(hot // 10000, 1000),
                    description=f"微博热搜 · {hot}",
                    url=f"https://s.weibo.com/weibo?q={requests.utils.quote(t)}"))
        except Exception:
            pass
    return items[:max_items]


# ── 4. 抖音热搜 ─────────────────────────────────────────────────────────────

def scan_douyin(max_items: int = 30) -> List[TrendItem]:
    """Fetch Douyin/TikTok hot search — iesdouyin API + fallbacks."""
    items: List[TrendItem] = []
    hdrs = {"Referer": "https://www.douyin.com/"}

    # Strategy 1: iesdouyin API
    resp = _get("https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/", headers=hdrs)
    if resp:
        try:
            word_list = resp.json().get("word_list", []) or resp.json().get("data", {}).get("word_list", [])
            for e in (word_list or []):
                t = e.get("word", "") or e.get("title", "")
                if not t:
                    continue
                hot = e.get("hot_value", 0) or e.get("heat", 0)
                items.append(TrendItem(
                    title=t, source="douyin", hot_score=min(hot // 100, 1000),
                    description=f"抖音热搜 · {hot}",
                    url=f"https://www.douyin.com/search/{requests.utils.quote(t)}"))
            if items:
                return items[:max_items]
        except Exception:
            pass

    # Strategy 2: aweme API
    resp = _get("https://aweme.snssdk.com/aweme/v1/hot/search/list/"
                "?detail_list=1&source=0&main_billboard_count=30", headers=hdrs)
    if resp:
        try:
            for e in resp.json().get("data", {}).get("word_list", []):
                t = e.get("word", "").strip()
                if not t:
                    continue
                hot = e.get("hot_value", 0)
                items.append(TrendItem(
                    title=t, source="douyin", hot_score=min(hot // 100, 1000),
                    description=f"抖音热搜 · {hot}",
                    url=f"https://www.douyin.com/search/{requests.utils.quote(t)}"))
        except Exception:
            pass
    return items[:max_items]


# ── 5. Aggregator ──────────────────────────────────────────────────────────

def deduplicate_and_sort(items: List[TrendItem], max_items: int = 50) -> List[TrendItem]:
    """Deduplicate by title, normalize hot scores per-source, then sort."""
    seen = set()
    unique: List[TrendItem] = []
    for item in items:
        key = item.title.strip().lower()[:60]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    # ── Per-source score normalization (spread across 200-1000) ──
    from collections import defaultdict
    by_source = defaultdict(list)
    for item in unique:
        by_source[item.source].append(item)

    for src, src_items in by_source.items():
        scores = [it.hot_score for it in src_items if it.hot_score > 0]
        if not scores:
            continue
        min_s, max_s = min(scores), max(scores)
        span = max_s - min_s if max_s > min_s else 1
        for it in src_items:
            # Normalize to 200-1000 range per source
            if it.hot_score > 0:
                norm = int(200 + (it.hot_score - min_s) / span * 800)
            else:
                norm = 500
            it.hot_score = norm

    unique.sort(key=lambda x: x.hot_score, reverse=True)
    return unique[:max_items]


def scan_all(max_items: int = 8, global_timeout: int = 15) -> List[dict]:
    """
    Run scanners. HN is the only consistently available source in China.
    Timeout ensures we never hang more than `global_timeout` seconds.
    """
    from concurrent.futures import ThreadPoolExecutor, wait
    from concurrent.futures import TimeoutError as FuturesTimeout
    import concurrent.futures as cf

    all_items: List[TrendItem] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(scan_hackernews, 6): "hackernews",
            pool.submit(scan_github, 8): "github",
            pool.submit(scan_weibo, 30): "weibo",
            pool.submit(scan_douyin, 30): "douyin",
        }
        done, _ = wait(futs, timeout=global_timeout)
        for fut in done:
            try:
                r = fut.result(timeout=2)
                if isinstance(r, list):
                    all_items.extend(r)
            except Exception:
                pass
        for fut in futs:
            if not fut.done():
                fut.cancel()

    merged = deduplicate_and_sort(all_items)
    src_counts = {}
    for x in merged:
        src_counts[x.source] = src_counts.get(x.source, 0) + 1
    print(f"\n{'='*50}")
    print(f"📊 热点扫描完成: {len(all_items)} 项 → {len(merged)} 去重")
    for src in ["hackernews", "github", "weibo", "douyin"]:
        c = src_counts.get(src, 0)
        print(f"   {src}: {c} 条" + (" ✅" if c else " ❌"))
    print(f"{'='*50}\n")

    return [item.to_dict() for item in merged]


# ── CLI entry ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = scan_all(max_items=50)
    for i, item in enumerate(results[:15], 1):
        print(f"{i:>2}. [{item['source']:>10}] (hot={item['hot_score']:>4}) {item['title'][:60]}")
