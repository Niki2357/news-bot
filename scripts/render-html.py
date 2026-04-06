#!/usr/bin/env python3
"""
Render HTML email from merged JSON — no LLM required.
Uses title + snippet directly from merged JSON, sorted by quality_score.

Usage:
    python3 render-html.py --input /tmp/td-merged.json --output /tmp/td-email.html
    python3 render-html.py --input /tmp/td-merged.json --output /tmp/td-email.html --top 5 --date 2026-04-05
"""

import argparse
import html
import json
import logging
import sys
from datetime import datetime
from pathlib import Path


def esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def shorten_url(url: str, max_len: int = 65) -> str:
    if len(url) <= max_len:
        return url
    return url[:max_len] + "…"


def _load_topic_meta(defaults_dir: Path) -> dict:
    """Load emoji + label from topics.json, fall back to built-in defaults."""
    builtin = {
        "llm":           {"emoji": "🧠", "label": "LLM / Large Models"},
        "ai-agent":      {"emoji": "🤖", "label": "AI Agent"},
        "ai_agent":      {"emoji": "🤖", "label": "AI Agent"},
        "crypto":        {"emoji": "💰", "label": "Cryptocurrency"},
        "frontier-tech": {"emoji": "🔬", "label": "Frontier Tech"},
        "frontier_tech": {"emoji": "🔬", "label": "Frontier Tech"},
        "github":        {"emoji": "📦", "label": "GitHub Releases"},
        "trending":      {"emoji": "🐙", "label": "GitHub Trending"},
        "uncategorized": {"emoji": "📰", "label": "Tech News"},
    }
    if defaults_dir:
        topics_file = defaults_dir / "topics.json"
        if topics_file.exists():
            try:
                with open(topics_file) as f:
                    data = json.load(f)
                for t in data.get("topics", []):
                    tid = t.get("id", "")
                    builtin[tid] = {"emoji": t.get("emoji", "📰"), "label": t.get("label", tid)}
            except Exception:
                pass
    return builtin


def _source_counts(topics: dict) -> dict:
    counts = {}
    for td in topics.values():
        for a in td.get("articles", []):
            st = a.get("source_type", "other")
            counts[st] = counts.get(st, 0) + 1
    return counts


def render_article_items(articles: list, top_n: int, min_score: float = 5.0) -> str:
    filtered = sorted(
        [a for a in articles if isinstance(a, dict)
         and a.get("quality_score", 0) >= min_score
         and a.get("source_type") not in ("github", "github_trending")],
        key=lambda a: a.get("quality_score", 0),
        reverse=True,
    )[:top_n]

    items = []
    for a in filtered:
        title = esc(a.get("title", "?"))
        score = a.get("quality_score", 0)
        link = a.get("link") or a.get("external_url") or a.get("reddit_url") or ""
        snippet = (a.get("snippet") or a.get("summary") or "")[:200]
        multi = a.get("source_count", 1) or 1

        multi_html = (
            f' <span style="font-size:11px;color:#888">[{multi} sources]</span>'
            if multi > 1 else ""
        )
        snippet_html = (
            f'<br><span style="font-size:13px;color:#666">{esc(snippet)}</span>'
            if snippet else ""
        )
        link_html = (
            f'<br><a href="{esc(link)}" style="color:#0969da;font-size:13px">'
            f'{esc(shorten_url(link))}</a>'
            if link else ""
        )

        items.append(
            f'    <li style="margin-bottom:12px">\n'
            f'      <strong>🔥{score:.0f}</strong> {title}{multi_html}'
            f'{snippet_html}{link_html}\n'
            f'    </li>'
        )
    return "\n".join(items)


def render_github_items(articles: list) -> str:
    items = []
    for a in [a for a in articles if isinstance(a, dict) and a.get("source_type") == "github"]:
        repo = esc(a.get("source_name") or a.get("title", "?"))
        version = esc(a.get("version") or "")
        desc = esc((a.get("snippet") or a.get("summary") or "")[:150])
        link = a.get("link") or a.get("external_url") or ""

        ver_html = f' <code style="font-size:12px">{version}</code>' if version else ""
        desc_html = f" — {desc}" if desc else ""
        link_html = (
            f'<br><a href="{esc(link)}" style="color:#0969da;font-size:13px">'
            f'{esc(shorten_url(link))}</a>'
            if link else ""
        )
        items.append(
            f'    <li style="margin-bottom:10px">\n'
            f'      <strong>{repo}</strong>{ver_html}{desc_html}{link_html}\n'
            f'    </li>'
        )
    return "\n".join(items)


def render_trending_items(articles: list, top_n: int) -> str:
    filtered = sorted(
        [a for a in articles if isinstance(a, dict) and a.get("source_type") == "github_trending"],
        key=lambda a: a.get("daily_stars_est", a.get("stars", 0)),
        reverse=True,
    )[:top_n]

    items = []
    for a in filtered:
        repo = esc(a.get("title") or a.get("source_name", "?"))
        stars = a.get("stars", 0)
        daily = a.get("daily_stars_est", 0)
        lang = esc(a.get("language") or "")
        desc = esc((a.get("snippet") or a.get("description") or "")[:120])
        link = a.get("link") or a.get("external_url") or ""

        stars_str = f"⭐ {stars:,}" if stars else ""
        daily_str = f" (+{daily}/day)" if daily else ""
        lang_str = f" | {lang}" if lang else ""
        meta = (
            f'<code style="font-size:12px;color:#888">'
            f'{stars_str}{daily_str}{lang_str}</code>'
        )
        link_html = (
            f'<br><a href="{esc(link)}" style="color:#0969da;font-size:13px">'
            f'{esc(shorten_url(link))}</a>'
            if link else ""
        )
        items.append(
            f'    <li style="margin-bottom:10px">\n'
            f'      <strong>{repo}</strong> {meta} — {desc}{link_html}\n'
            f'    </li>'
        )
    return "\n".join(items)


def render_twitter_items(articles: list, top_n: int) -> str:
    filtered = [a for a in articles if isinstance(a, dict) and a.get("source_type") == "twitter"]
    filtered.sort(key=lambda a: a.get("quality_score", 0), reverse=True)
    filtered = filtered[:top_n]

    items = []
    for a in filtered:
        display = esc(a.get("display_name") or a.get("source_name") or "?")
        handle = esc(a.get("handle") or a.get("screen_name") or "")
        snippet = esc((a.get("snippet") or a.get("title") or "")[:200])
        link = a.get("link") or a.get("external_url") or ""
        metrics = a.get("metrics") or {}

        handle_html = f' (@{handle})' if handle else ""
        m_parts = []
        for key, icon in [("impression_count", "👁"), ("reply_count", "💬"),
                           ("retweet_count", "🔁"), ("like_count", "❤️")]:
            v = metrics.get(key, 0) or 0
            if v >= 1000:
                m_parts.append(f"{icon} {v/1000:.1f}K")
            elif v > 0:
                m_parts.append(f"{icon} {v}")
        metrics_html = (
            f'<br><code style="font-size:12px;color:#888;background:#f4f4f4;'
            f'padding:2px 6px;border-radius:3px">{" | ".join(m_parts)}</code>'
            if m_parts else ""
        )
        link_html = (
            f'<br><a href="{esc(link)}" style="color:#0969da;font-size:13px">'
            f'{esc(shorten_url(link))}</a>'
            if link else ""
        )
        items.append(
            f'    <li style="margin-bottom:10px">\n'
            f'      <strong>{display}</strong>{handle_html} — {snippet}'
            f'{metrics_html}{link_html}\n'
            f'    </li>'
        )
    return "\n".join(items)


def render_html(data: dict, top_n: int = 5, date: str = None, defaults_dir: Path = None) -> str:
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    topic_meta = _load_topic_meta(defaults_dir)
    topics = data.get("topics", {})
    stats = data.get("output_stats", {})
    source_counts = _source_counts(topics)

    sections = []

    # Regular topic sections
    for topic_id, topic_data in topics.items():
        articles = topic_data.get("articles", [])
        if not articles:
            continue

        meta = topic_meta.get(topic_id, {"emoji": "📰", "label": topic_id})
        emoji = meta["emoji"]
        label = meta["label"]

        article_items = render_article_items(articles, top_n)
        github_items = render_github_items(articles)
        trending_items = render_trending_items(articles, top_n)
        twitter_items = render_twitter_items(articles, top_n)

        section_parts = []
        if article_items:
            section_parts.append(
                f'  <h2 style="font-size:17px;margin-top:24px;color:#333">'
                f'{emoji} {esc(label)}</h2>\n'
                f'  <ul style="padding-left:20px">\n{article_items}\n  </ul>'
            )
        if github_items:
            section_parts.append(
                f'  <h2 style="font-size:17px;margin-top:24px;color:#333">'
                f'📦 GitHub Releases</h2>\n'
                f'  <ul style="padding-left:20px">\n{github_items}\n  </ul>'
            )
        if trending_items:
            section_parts.append(
                f'  <h2 style="font-size:17px;margin-top:24px;color:#333">'
                f'🐙 GitHub Trending</h2>\n'
                f'  <ul style="padding-left:20px">\n{trending_items}\n  </ul>'
            )
        if twitter_items:
            section_parts.append(
                f'  <h2 style="font-size:17px;margin-top:24px;color:#333">'
                f'📢 KOL Updates</h2>\n'
                f'  <ul style="padding-left:20px">\n{twitter_items}\n  </ul>'
            )

        sections.extend(section_parts)

    sections_html = "\n\n".join(sections)

    # Build stats footer
    rss = source_counts.get("rss", "?")
    twitter = source_counts.get("twitter", "?")
    reddit = source_counts.get("reddit", "?")
    web = source_counts.get("web", "?")
    github = source_counts.get("github", "?")
    trending = source_counts.get("github_trending", "?")
    merged = stats.get("total_articles", "?")

    return (
        '<div style="max-width:640px;margin:0 auto;font-family:-apple-system,'
        "BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a1a;line-height:1.6\">\n\n"
        f'  <h1 style="font-size:22px;border-bottom:2px solid #e5e5e5;padding-bottom:8px">\n'
        f"    🐉 Daily Tech Digest — {esc(date)}\n"
        "  </h1>\n\n"
        f"{sections_html}\n\n"
        '  <hr style="border:none;border-top:1px solid #e5e5e5;margin:24px 0">\n'
        '  <p style="font-size:12px;color:#888">\n'
        f"    📊 Data Sources: RSS {rss} | Twitter {twitter} | Reddit {reddit} | "
        f"Web {web} | GitHub {github} releases + {trending} trending | After dedup: {merged} articles"
        '<br>\n'
        '    🤖 Generated by <a href="https://github.com/draco-agent/tech-news-digest" '
        'style="color:#0969da">tech-news-digest</a>\n'
        "  </p>\n\n"
        "</div>"
    )


def main() -> int:
    _script_dir = Path(__file__).resolve().parent
    _default_defaults = _script_dir.parent / "config" / "defaults"

    parser = argparse.ArgumentParser(description="Render HTML email from merged JSON")
    parser.add_argument("--input", "-i", type=Path, default=Path("/tmp/td-merged.json"))
    parser.add_argument("--output", "-o", type=Path, default=Path("/tmp/td-email.html"))
    parser.add_argument("--top", "-n", type=int, default=5, help="Top N articles per topic")
    parser.add_argument("--date", type=str, default=None, help="Report date YYYY-MM-DD (default: today)")
    parser.add_argument("--defaults", type=Path, default=_default_defaults, help="Config defaults dir for topic metadata")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.input.exists():
        logging.error(f"Input not found: {args.input} — run run-pipeline.py first")
        return 1

    with open(args.input) as f:
        data = json.load(f)

    content = render_html(data, top_n=args.top, date=args.date, defaults_dir=args.defaults)
    args.output.write_text(content, encoding="utf-8")
    logging.info(f"✅ HTML written → {args.output} ({len(content):,} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
