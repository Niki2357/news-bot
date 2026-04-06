#!/usr/bin/env python3
"""
Render Discord messages from merged JSON — fixed section order:
  1. 🌍 Global & Political News  (topic: global-news,   top 5)
  2. 🔥 Hacker News              (topic: hacker-news,   top 5)
  3. 🐙 Trending on GitHub       (source_type: github_trending, top 5)
  4. 💻 Tech News                (all remaining articles, top 5)

Each article is its own Discord message.
Section header is prepended to the first article of each section.
A divider is appended after the last article of each section.
URLs are wrapped in <> to suppress Discord embed popups.
Output: JSON array of strings — one element per Discord message.

Usage:
    python3 render-discord.py --input /tmp/td-merged.json --output /tmp/td-discord.json
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

DIVIDER = "──────────────────────────────"

# URL patterns that indicate a video (not summarizable)
_VIDEO_URL_RE = re.compile(
    r"(youtube\.com/watch|youtu\.be/|/news/videos/|vimeo\.com/\d)",
    re.IGNORECASE,
)
_VIDEO_TITLE_RE = re.compile(
    r"\[video\]|\(video\)|^watch\s*:|^\s*video\s*:", re.IGNORECASE
)
# Reddit-style tag prefixes: [D], [P], [R], [N], [Q], [Project], etc.
_REDDIT_TAG_RE = re.compile(r"^\[[A-Za-z]{1,10}\]\s*")


def _fmt_date(date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    return dt.strftime(f"%A, %B {dt.day}, %Y")


def _suppress(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    # Decode HTML entities that leak through RSS feeds
    url = (url.replace("&amp;", "&").replace("&apos;", "'")
              .replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    if url.startswith("<") and url.endswith(">"):
        return url
    return f"<{url}>"


def _get_link(a: dict) -> str:
    return (a.get("link") or a.get("external_url") or a.get("reddit_url") or "").strip()


def _is_video(a: dict) -> bool:
    """Return True if the article is a video and should be skipped."""
    link = _get_link(a)
    title = (a.get("title") or "").strip()
    if link and _VIDEO_URL_RE.search(link):
        return True
    if title and _VIDEO_TITLE_RE.search(title):
        return True
    return False


def _clean_title(title: str) -> str:
    """Strip Reddit [D], [P] etc. prefixes and HTML entities."""
    title = _REDDIT_TAG_RE.sub("", title).strip()
    # Unescape common HTML entities that leak through RSS
    title = (title
             .replace("&amp;", "&")
             .replace("&apos;", "'")
             .replace("&quot;", '"')
             .replace("&lt;", "<")
             .replace("&gt;", ">"))
    return title


def _article_block(a: dict) -> str:
    """
    **title**
    summary (or "No summary available.")
    <url>
    """
    title = _clean_title(a.get("title") or "")
    desc = (a.get("snippet") or a.get("summary") or "").strip()
    link = _get_link(a)

    parts = [f"**{title}**", desc if desc else "No summary available."]
    if link:
        parts.append(_suppress(link))
    return "\n".join(parts)


def _trending_block(a: dict) -> str:
    """
    **owner/repo**
    description — ⭐ stars · Language
    <url>
    """
    raw = (a.get("title") or a.get("source_name") or "").strip()
    repo = raw.split(":")[0].strip() if ":" in raw else raw

    desc = (a.get("snippet") or a.get("description") or "").strip()
    stars = a.get("stars", 0)
    lang = (a.get("language") or "").strip()
    link = _get_link(a)

    meta_parts = []
    if stars:
        meta_parts.append(f"⭐ {stars:,}")
    if lang:
        meta_parts.append(lang)
    meta = " · ".join(meta_parts)

    summary = f"{desc} — {meta}" if (desc and meta) else (desc or meta)

    parts = [f"**{repo}**"]
    if summary:
        parts.append(summary)
    if link:
        parts.append(_suppress(link))
    return "\n".join(parts)


def _emit_section(messages: list[str], header: str, blocks: list[str]) -> None:
    """
    One Discord message per article.
    Section header prepended to the first article.
    Divider appended as a standalone message after the last article.
    """
    for i, block in enumerate(blocks):
        if i == 0:
            messages.append(f"{header}\n{block}")
        else:
            messages.append(block)
    messages.append(DIVIDER)


def _market_message(market: dict) -> str:
    """
    📈 Market Summary
    **S&P 500**   6,582  🟢 +0.11%
    **NASDAQ**   21,879  🟢 +0.18%
    ...
    """
    lines = ["📈 Market Summary"]
    for idx in market.get("indices", []):
        name = idx.get("name", idx.get("symbol", "?"))
        price = idx.get("price", 0)
        pct = idx.get("change_pct", 0)
        currency = idx.get("currency", "USD")

        arrow = "🟢" if pct >= 0 else "🔴"
        sign = "+" if pct >= 0 else ""

        # Format price: crypto/BTC shown with $, indices without currency symbol
        if currency == "USD" and idx.get("symbol", "").endswith("-USD"):
            price_str = f"${price:,.0f}"
        else:
            price_str = f"{price:,.2f}"

        lines.append(f"**{name}**   {price_str}   {arrow} {sign}{pct:.2f}%")

    return "\n".join(lines)


def render_messages(
    data: dict,
    top_n_news: int = 5,
    top_n_tech: int = 5,
    date: str = None,
    market: dict = None,
) -> list[str]:
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    topics = data.get("topics", {})

    global_articles: list[dict] = []
    hn_articles: list[dict] = []
    trending_articles: list[dict] = []
    tech_articles: list[dict] = []
    tech_topic_ids = {"llm", "ai-agent", "ai_agent", "frontier-tech", "frontier_tech",
                      "crypto", "tech-news", "uncategorized"}

    for topic_id, topic_data in topics.items():
        for a in topic_data.get("articles", []):
            if not isinstance(a, dict):
                continue
            if _is_video(a):
                continue
            st = a.get("source_type", "")

            if st == "github_trending":
                trending_articles.append(a)
            elif topic_id == "global-news":
                global_articles.append(a)
            elif topic_id == "hacker-news":
                hn_articles.append(a)
            elif topic_id in tech_topic_ids or st in ("rss", "web"):
                tech_articles.append(a)

    def by_score(lst):
        return sorted(lst, key=lambda a: a.get("quality_score", 0), reverse=True)

    global_articles = by_score(global_articles)[:top_n_news]
    hn_articles = by_score(hn_articles)[:top_n_news]
    trending_articles = sorted(
        trending_articles,
        key=lambda a: a.get("daily_stars_est", a.get("stars", 0)),
        reverse=True,
    )[:top_n_news]

    seen_links: set[str] = set()
    unique_tech: list[dict] = []
    for a in by_score(tech_articles):
        link = _get_link(a)
        if link and link in seen_links:
            continue
        seen_links.add(link)
        unique_tech.append(a)
    tech_articles = unique_tech[:top_n_tech]

    messages: list[str] = []

    messages.append(f"🐾 Daily Digest\n{_fmt_date(date)}")

    if global_articles:
        _emit_section(messages, "🌍 Global & Political News",
                      [_article_block(a) for a in global_articles])

    if hn_articles:
        _emit_section(messages, "🔥 Hacker News",
                      [_article_block(a) for a in hn_articles])

    if trending_articles:
        _emit_section(messages, "🐙 Trending on GitHub",
                      [_trending_block(a) for a in trending_articles])

    if tech_articles:
        _emit_section(messages, "💻 Tech News",
                      [_article_block(a) for a in tech_articles])

    # ── Market Summary (standalone, no per-article split needed) ─────────
    if market and market.get("indices"):
        messages.append(_market_message(market))
        messages.append(DIVIDER)

    return messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Discord messages from merged JSON")
    parser.add_argument("--input", "-i", type=Path, default=Path("/tmp/td-merged.json"))
    parser.add_argument("--output", "-o", type=Path, default=Path("/tmp/td-discord.json"))
    parser.add_argument("--top-news", type=int, default=5)
    parser.add_argument("--top-tech", type=int, default=5)
    parser.add_argument("--date", type=str, default=None)
    parser.add_argument("--market", type=Path, default=None,
                        help="Market data JSON from fetch-market.py")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.input.exists():
        logging.error(f"Input not found: {args.input}")
        return 1

    with open(args.input) as f:
        data = json.load(f)

    market = None
    if args.market and args.market.exists():
        with open(args.market) as f:
            market = json.load(f)

    messages = render_messages(
        data, top_n_news=args.top_news, top_n_tech=args.top_tech,
        date=args.date, market=market,
    )
    args.output.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    total_chars = sum(len(m) for m in messages)
    logging.info(f"✅ {len(messages)} messages → {args.output} ({total_chars:,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
