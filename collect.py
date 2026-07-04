#!/usr/bin/env python3
"""信号采集器：GitHub 趋势 + 新库搜索 + 公开样本动态（HN）。
零依赖（仅标准库）。产出 data/YYYY-MM-DD.json，供下游四个出口使用。
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UA = {"User-Agent": "signal-pipeline/0.1 (personal research pipeline)"}
TODAY = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

# 换成你要长期监控的公开产品/项目/协议（HN 提及监控用）
PUBLIC_SAMPLES = ["Claude Code", "Copilot Studio", "MCP protocol"]


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def github_trending(since: str = "daily") -> list:
    """抓 github.com/trending 页面（无官方 API，HTML 解析，字段尽量防御性提取）。"""
    try:
        html = fetch(f"https://github.com/trending?since={since}")
    except Exception as e:
        print(f"[warn] trending 抓取失败: {e}", file=sys.stderr)
        return []
    repos = []
    for block in re.findall(r'<article class="Box-row".*?</article>', html, re.S):
        m = re.search(r'href="/([^/"]+/[^/"]+)"', block)
        if not m:
            continue
        name = m.group(1)
        desc_m = re.search(r'<p class="[^"]*col-9[^"]*">\s*(.*?)\s*</p>', block, re.S)
        desc = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""
        lang_m = re.search(r'itemprop="programmingLanguage">([^<]+)<', block)
        stars_m = re.search(r'([\d,]+)\s+stars\s+(?:today|this week)', block)
        # 总星数：stargazers 链接的文本节点（防御性：取链接闭合前最后一个数字串）
        total_m = re.findall(r'stargazers"[^>]*>\s*(?:<[^>]+>\s*)*([\d,]+(?:\.\d+)?k?)\s*<', block)
        repos.append({
            "repo": name,
            "desc": desc,
            "lang": lang_m.group(1) if lang_m else "",
            "stars_period": stars_m.group(1).replace(",", "") if stars_m else "",
            "stars_total": total_m[0].replace(",", "") if total_m else "",
            "url": f"https://github.com/{name}",
        })
    return repos


def github_search(query: str, per_page: int = 15) -> list:
    """GitHub Search API（未认证限流 10 次/分钟，够用）。"""
    url = ("https://api.github.com/search/repositories?q="
           + urllib.request.quote(query) + f"&sort=stars&order=desc&per_page={per_page}")
    try:
        items = json.loads(fetch(url)).get("items", [])
    except Exception as e:
        print(f"[warn] search 失败 ({query}): {e}", file=sys.stderr)
        return []
    return [{
        "repo": it["full_name"],
        "desc": (it.get("description") or "")[:300],
        "lang": it.get("language") or "",
        "stars_total": it.get("stargazers_count", 0),
        "created": it.get("created_at", "")[:10],
        "topics": it.get("topics", [])[:8],
        "url": it["html_url"],
    } for it in items]


def hn_mentions(names: list, days: int = 7) -> dict:
    """HN Algolia API：公开样本近 N 天的讨论（可靠 JSON，无需认证）。"""
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    out = {}
    for name in names:
        url = ("https://hn.algolia.com/api/v1/search_by_date?query="
               + urllib.request.quote(f'"{name}"')
               + f"&tags=story&numericFilters=created_at_i>{cutoff},points>10")
        try:
            hits = json.loads(fetch(url)).get("hits", [])
        except Exception as e:
            print(f"[warn] HN 失败 ({name}): {e}", file=sys.stderr)
            continue
        out[name] = [{
            "title": h.get("title", ""),
            "points": h.get("points", 0),
            "comments": h.get("num_comments", 0),
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "date": h.get("created_at", "")[:10],
        } for h in hits[:5]]
    return out


def main():
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    data = {
        "date": TODAY,
        "trending_daily": github_trending("daily"),
        "new_agent_repos": github_search(
            f"created:>{week_ago} topic:agent stars:>50"),
        "new_llm_repos": github_search(
            f"created:>{week_ago} topic:llm stars:>50"),
        "sample_mentions": hn_mentions(PUBLIC_SAMPLES),
    }
    out = Path(__file__).parent / "data" / f"{TODAY}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    n = (len(data["trending_daily"]), len(data["new_agent_repos"]),
         len(data["new_llm_repos"]), sum(len(v) for v in data["sample_mentions"].values()))
    print(f"[ok] {out.name}: trending={n[0]} new-agent={n[1]} new-llm={n[2]} hn-mentions={n[3]}")


if __name__ == "__main__":
    main()
