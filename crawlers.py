"""
Standalone crawlers for Weibo & Douyin hot search.
Test with:  python crawlers.py

Each source has multiple fallback APIs and proper browser headers.
Returns list of {"title", "hot_score", "url"} dicts.
"""

import requests
import json
import time
from typing import List, Optional

# ── HTTP helper ────────────────────────────────────────────────────────────
TIMEOUT = (4.0, 6.0)

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

def _try_get(url: str, extra_headers: dict = None, as_json: bool = True) -> Optional[dict]:
    """Single GET attempt. Returns parsed JSON dict or None."""
    headers = {**_BASE_HEADERS}
    if extra_headers:
        headers.update(extra_headers)
    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        if as_json:
            return resp.json()
        return {"text": resp.text}
    except Exception:
        return None


# ── 1. 微博热搜 ─────────────────────────────────────────────────────────────

def crawl_weibo() -> List[dict]:
    """
    Try multiple Weibo hot search endpoints.
    Each endpoint has a different JSON structure — we normalize them all.
    """
    apis = [
        # Mobile API — most reliable
        {
            "url": "https://m.weibo.cn/api/container/getIndex?"
                   "containerid=106003type%3D25%26t%3D3%26disable_hot%3D1%26filter_type%3Drealtimehot",
            "headers": {"Referer": "https://m.weibo.cn/"},
            "parser": "mobile",
        },
        # Desktop hotband API
        {
            "url": "https://weibo.com/ajax/statuses/hot_band",
            "headers": {"Referer": "https://weibo.com/"},
            "parser": "hot_band",
        },
        # Desktop side panel
        {
            "url": "https://weibo.com/ajax/side/hotSearch",
            "headers": {"Referer": "https://weibo.com/"},
            "parser": "side",
        },
    ]

    for api in apis:
        result = _try_get(api["url"], extra_headers=api["headers"])
        if not result:
            continue

        items = []
        parser = api["parser"]

        try:
            if parser == "mobile":
                cards = result.get("data", {}).get("cards", [])
                for card in cards:
                    if card.get("card_type") == 11:
                        continue  # skip header card
                    for entry in (card.get("card_group") or []):
                        title = entry.get("desc", "").strip()
                        if not title:
                            continue
                        hot = entry.get("desc_extr", 0) or 0
                        scheme = entry.get("scheme", "")
                        items.append({"title": title, "hot_score": min(hot // 10000, 1000),
                                      "url": scheme, "source": "weibo"})

            elif parser == "hot_band":
                band_list = result.get("data", {}).get("band_list", [])
                for entry in band_list:
                    title = entry.get("word", "") or entry.get("note", "")
                    if not title:
                        continue
                    hot = entry.get("raw_hot", 0) or entry.get("num", 0)
                    items.append({"title": title.strip(), "hot_score": min(hot // 10000, 1000),
                                  "url": f"https://s.weibo.com/weibo?q={requests.utils.quote(title)}",
                                  "source": "weibo"})

            elif parser == "side":
                realtime = result.get("data", {}).get("realtime", [])
                for entry in realtime:
                    word = entry.get("word", "") or entry.get("word_scheme", "")
                    if not word:
                        continue
                    hot = entry.get("raw_hot", 0) or entry.get("num", 0)
                    items.append({"title": word.strip(), "hot_score": min(hot // 10000, 1000),
                                  "url": f"https://s.weibo.com/weibo?q={requests.utils.quote(word)}",
                                  "source": "weibo"})

            if items:
                print(f"  [crawler] 微博 OK via {parser} — {len(items)} items")
                return items[:30]

        except Exception as e:
            print(f"  [crawler] 微博 {parser} parse error: {e}")
            continue

    print("  [crawler] 微博: all endpoints failed")
    return []


# ── 2. 抖音热搜 ─────────────────────────────────────────────────────────────

def crawl_douyin() -> List[dict]:
    """Try multiple Douyin hot search endpoints."""
    apis = [
        # iesdouyin API (most reliable)
        {
            "url": "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/",
            "headers": {"Referer": "https://www.douyin.com/"},
            "parser": "ies",
        },
        # aweme API fallback
        {
            "url": "https://aweme.snssdk.com/aweme/v1/hot/search/list/?"
                   "detail_list=1&source=0&main_billboard_count=30",
            "headers": {"Referer": "https://www.douyin.com/"},
            "parser": "aweme",
        },
    ]

    for api in apis:
        result = _try_get(api["url"], extra_headers=api["headers"])
        if not result:
            continue

        items = []
        parser = api["parser"]

        try:
            if parser == "ies":
                billboard = result.get("word_list", []) or result.get("data", {}).get("word_list", [])
                for entry in (billboard or []):
                    word = entry.get("word", "") or entry.get("title", "")
                    if not word:
                        continue
                    hot = entry.get("hot_value", 0) or entry.get("heat", 0)
                    items.append({"title": word, "hot_score": min(hot // 100, 1000),
                                  "url": f"https://www.douyin.com/search/{requests.utils.quote(word)}",
                                  "source": "douyin"})

            elif parser == "aweme":
                word_list = result.get("data", {}).get("word_list", [])
                for entry in (word_list or []):
                    word = entry.get("word", "")
                    if not word:
                        continue
                    hot = entry.get("hot_value", 0)
                    items.append({"title": word, "hot_score": min(hot // 100, 1000),
                                  "url": f"https://www.douyin.com/search/{requests.utils.quote(word)}",
                                  "source": "douyin"})

            if items:
                print(f"  [crawler] 抖音 OK via {parser} — {len(items)} items")
                return items[:30]

        except Exception as e:
            print(f"  [crawler] 抖音 {parser} parse error: {e}")
            continue

    # Fallback: try douyin.com hot page directly
    try:
        resp = requests.get("https://www.douyin.com/hot/", headers={
            **_BASE_HEADERS,
            "Referer": "https://www.douyin.com/",
            "Accept": "text/html,application/xhtml+xml,*/*",
        }, timeout=TIMEOUT)
        # The hot page returns a SPA — we can try to find __ROUTER_DATA__
        if "__ROUTER_DATA__" in resp.text:
            import re
            match = re.search(r'routerData"\s*:\s*({.+?})"type"', resp.text, re.DOTALL)
            if match:
                print("  [crawler] 抖音 fallback: found embedded data")
    except Exception:
        pass

    print("  [crawler] 抖音: all endpoints failed")
    return []


# ── 3. Aggregator ───────────────────────────────────────────────────────────

def crawl_all() -> dict:
    """Crawl both sources and return combined results."""
    results = {}
    t0 = time.time()

    for name, fn in [("weibo", crawl_weibo), ("douyin", crawl_douyin)]:
        results[name] = fn()

    elapsed = time.time() - t0
    total = sum(len(v) for v in results.values())
    print(f"\n{'='*50}")
    print(f"📊 爬虫汇总 ({elapsed:.1f}s): {total} 条")
    for name, items in results.items():
        print(f"   {name}: {len(items)} 条" + (" ✅" if items else " ❌"))
    print(f"{'='*50}\n")
    return results


# ── CLI test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🧪 测试微博 & 抖音爬虫...\n")
    result = crawl_all()
    for source, items in result.items():
        print(f"\n--- {source} (top 5) ---")
        for item in items[:5]:
            print(f"  [{item['hot_score']:>4}] {item['title'][:50]}")
