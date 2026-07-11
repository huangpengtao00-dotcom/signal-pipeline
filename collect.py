#!/usr/bin/env python3
"""信号采集器：GitHub 趋势 + 新库搜索 + 公开样本动态（HN）。

零依赖（仅 Python 标准库）。产出 data/YYYY-MM-DD.json，供下游四个出口使用。
改成你自己的管道：通常只需改下面「配置」一段，无需动采集逻辑。

单源失败不拖垮全局：每个数据源各自 try/except，抓不到就跳过并留一条 warn，
当天照常出文件——采集不完备是常态，缺口交给下游报告，不是让整条管道崩掉。
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

# ── 配置（改成你自己的管道：一般只动这一段）──────────────────────────────
# 你要长期监控的公开产品/项目/协议（HN 提及监控用）
PUBLIC_SAMPLES = ["Claude Code", "Copilot Studio", "MCP protocol"]

# GitHub 新库搜索：每个 topic 出一组「近 N 天创建、星数达标」的新库
SEARCH_TOPICS = ["agent", "llm"]     # 换成你领域的 GitHub topic（可增删）
NEW_REPO_WINDOW_DAYS = 7             # 只看近 N 天创建的库
MIN_STARS = 50                       # 新库最低星数（滤掉噪声）
RESULTS_PER_TOPIC = 15               # 每个 topic 取前几名

# HN Algolia 提及监控阈值
HN_WINDOW_DAYS = 7                   # 只看近 N 天的讨论
HN_MIN_POINTS = 10                   # 帖子最低热度（分数）
HN_HITS_PER_SAMPLE = 5               # 每个样本最多留几条
# ──────────────────────────────────────────────────────────────────────

UA = {"User-Agent": "signal-pipeline/0.1 (personal research pipeline)"}
TODAY = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def fetch(url: str, timeout: int = 20) -> str:
    """GET 一个 URL，返回解码后的文本。

    命中 api.github.com 且环境里存在 GITHUB_TOKEN 时自动带上认证头
    （把未认证限流 60 次/时 提到 5000 次/时）——本地不设也能跑，
    GitHub Actions 里由 workflow 注入。token 只从环境读，绝不写进代码。
    """
    headers = dict(UA)
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def github_trending(since: str = "daily") -> list[dict]:
    """抓 github.com/trending 页面（无官方 API，HTML 解析，字段尽量防御性提取）。

    仓库名只认标题 <h2> 里的那条链接：同一行里还有 /sponsors/... 和
    /login?return_to=... 等同形链接，若取块内第一个 href 会误采成赞助页。
    """
    try:
        html = fetch(f"https://github.com/trending?since={since}")
    except Exception as e:
        print(f"[warn] trending 抓取失败: {e}", file=sys.stderr)
        return []
    repos = []
    for block in re.findall(r'<article class="Box-row".*?</article>', html, re.S):
        name_m = re.search(r'<h2\b[^>]*>.*?href="/([^"/]+/[^"/#?]+)"', block, re.S)
        if not name_m:
            continue
        name = name_m.group(1)
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


def github_search(query: str, per_page: int = RESULTS_PER_TOPIC) -> list[dict]:
    """GitHub Search API（未认证限流 10 次/分钟，够用；设 GITHUB_TOKEN 可提速）。"""
    url = ("https://api.github.com/search/repositories?q="
           + quote(query) + f"&sort=stars&order=desc&per_page={per_page}")
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


def hn_mentions(names: list[str], days: int = HN_WINDOW_DAYS) -> dict[str, list]:
    """HN Algolia API：公开样本近 N 天的讨论（可靠 JSON，无需认证）。"""
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    out: dict[str, list] = {}
    for name in names:
        url = ("https://hn.algolia.com/api/v1/search_by_date?query="
               + quote(f'"{name}"')
               + f"&tags=story&numericFilters=created_at_i>{cutoff},points>{HN_MIN_POINTS}")
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
        } for h in hits[:HN_HITS_PER_SAMPLE]]
    return out


def main() -> None:
    window = (datetime.now(timezone.utc)
              - timedelta(days=NEW_REPO_WINDOW_DAYS)).strftime("%Y-%m-%d")
    data = {
        "date": TODAY,
        "trending_daily": github_trending("daily"),
        # 每个配置 topic 一组新库，key 就是 topic 名（改 SEARCH_TOPICS 即改这里）
        "new_repos": {
            topic: github_search(f"created:>{window} topic:{topic} stars:>{MIN_STARS}")
            for topic in SEARCH_TOPICS
        },
        "sample_mentions": hn_mentions(PUBLIC_SAMPLES),
    }
    out = Path(__file__).parent / "data" / f"{TODAY}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    new_counts = " ".join(f"new-{t}={len(v)}" for t, v in data["new_repos"].items())
    hn_total = sum(len(v) for v in data["sample_mentions"].values())
    print(f"[ok] {out.name}: trending={len(data['trending_daily'])} "
          f"{new_counts} hn-mentions={hn_total}")


if __name__ == "__main__":
    main()
