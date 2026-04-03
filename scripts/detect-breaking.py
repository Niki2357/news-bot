#!/usr/bin/env python3
"""
Detect breaking news stories from the merged pipeline output.

A story is flagged as breaking when ANY of these are true:
  - source_count >= --min-sources (3+ independent sources covered it)
  - quality_score >= --min-score

Generates a focused HTML alert email and sends it.

Usage:
    python3 detect-breaking.py \
        --input /tmp/td-merged.json \
        --to user@example.com \
        [--subject "🚨 Breaking Tech News"] \
        [--min-sources 3] [--min-score 15] \
        [--dry-run] [--verbose]
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional


MULTI_SOURCE_THRESHOLD = 3    # 3+ sources covering the same story
HIGH_SCORE_THRESHOLD = 15     # score >= 15 regardless of source count


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s",
                        datefmt="%H:%M:%S")
    return logging.getLogger(__name__)


def find_breaking_stories(
    data: Dict[str, Any],
    min_sources: int,
    min_score: float,
) -> List[Dict[str, Any]]:
    """Return articles that meet the breaking news threshold, deduplicated."""
    breaking = []
    for topic_id, topic_data in data.get("topics", {}).items():
        articles = topic_data.get("articles", []) if isinstance(topic_data, dict) else []
        for article in articles:
            source_count = article.get("source_count", 1)
            score = article.get("quality_score", 0)
            if source_count >= min_sources or score >= min_score:
                a = article.copy()
                a["_topic_id"] = topic_id
                breaking.append(a)

    # Sort by score desc then source_count desc
    breaking.sort(key=lambda a: (-a.get("quality_score", 0), -a.get("source_count", 1)))

    # Deduplicate: same title prefix across topics
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for a in breaking:
        key = a.get("title", "")[:60].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(a)

    return unique


def _esc(text: str) -> str:
    """Minimal HTML escape for inline content."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def render_html(
    breaking: List[Dict[str, Any]],
    generated: str,
    input_stats: Dict[str, Any],
) -> str:
    """Build the full HTML email body for the breaking news alert."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_input = input_stats.get("total_input", "?")
    total_merged = input_stats.get("total_articles", "?")

    items_html = ""
    for a in breaking:
        title = _esc(a.get("title", ""))
        link = _esc(a.get("link", ""))
        score = a.get("quality_score", 0)
        source_count = a.get("source_count", 1)
        all_sources = a.get("all_sources", [])
        snippet = _esc((a.get("snippet") or a.get("summary") or "")[:220])
        topic_label = a.get("_topic_id", "").replace("_", " ").title()
        developing = a.get("developing_story")

        # Multi-source badge
        multi_badge = ""
        if source_count >= 3:
            multi_badge = (
                f'<span style="background:#ef4444;color:white;font-size:11px;'
                f'padding:2px 7px;border-radius:3px;margin-right:6px;font-weight:600">'
                f'×{source_count} sources</span>'
            )
        elif source_count == 2:
            multi_badge = (
                f'<span style="background:#f97316;color:white;font-size:11px;'
                f'padding:2px 7px;border-radius:3px;margin-right:6px">'
                f'×2 sources</span>'
            )

        # Developing story callout
        developing_html = ""
        if developing:
            first_seen = _esc(developing.get("first_seen_date", ""))
            prev_title = _esc(developing.get("prev_title", "")[:80])
            developing_html = (
                f'<div style="font-size:12px;color:#6b7280;margin-top:5px">'
                f'📌 <em>Developing</em> — first reported {first_seen}: '
                f'"{prev_title}"</div>'
            )

        sources_html = ""
        if all_sources:
            sources_html = (
                f'<div style="font-size:11px;color:#9ca3af;margin-top:3px">'
                f'Via: {_esc(", ".join(all_sources[:3]))}</div>'
            )

        snippet_html = (
            f'<div style="font-size:13px;color:#4b5563;margin-top:5px">{snippet}</div>'
            if snippet else ""
        )

        items_html += f"""
  <li style="margin-bottom:20px;padding-bottom:20px;border-bottom:1px solid #f3f4f6">
    <div style="margin-bottom:4px">
      {multi_badge}<span style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.04em">{_esc(topic_label)}</span>
    </div>
    <a href="{link}" style="font-size:15px;font-weight:600;color:#111827;text-decoration:none;line-height:1.4;display:block">{title}</a>
    {snippet_html}
    {developing_html}
    {sources_html}
    <div style="margin-top:7px">
      <a href="{link}" style="color:#2563eb;font-size:12px">{link[:80]}</a>
      <span style="color:#e5e7eb;margin:0 6px">·</span>
      <span style="font-size:11px;color:#9ca3af">🔥{score:.0f} pts</span>
    </div>
  </li>"""

    count_label = f"{len(breaking)} breaking {'story' if len(breaking) == 1 else 'stories'}"

    return f"""<div style="max-width:620px;margin:0 auto;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a1a;line-height:1.6">

  <div style="background:#dc2626;color:white;padding:14px 20px;border-radius:8px 8px 0 0;display:flex;justify-content:space-between;align-items:center">
    <strong style="font-size:17px">🚨 Breaking Tech News</strong>
    <span style="font-size:12px;opacity:0.85">{now}</span>
  </div>

  <div style="border:1px solid #fca5a5;border-top:none;border-radius:0 0 8px 8px;padding:18px 22px">
    <p style="color:#6b7280;font-size:13px;margin:0 0 18px 0">
      <strong style="color:#1f2937">{count_label}</strong> flagged
      from {total_input} articles collected ({total_merged} after dedup).
      Criteria: 3+ independent sources <em>or</em> quality score ≥ {HIGH_SCORE_THRESHOLD}.
    </p>
    <ul style="list-style:none;padding:0;margin:0">
      {items_html}
    </ul>
  </div>

  <p style="font-size:11px;color:#d1d5db;margin-top:10px;text-align:center">
    tech-news-digest breaking detector ·
    <a href="https://github.com/draco-agent/tech-news-digest" style="color:#9ca3af">github</a>
  </p>

</div>"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Detect and email breaking tech news from merged pipeline output.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
    # Dry run — see what would be flagged without sending
    python3 detect-breaking.py --input /tmp/td-merged.json --to me@example.com --dry-run

    # Send real alert
    python3 detect-breaking.py --input /tmp/td-merged.json --to me@example.com

    # Lower threshold during breaking news event
    python3 detect-breaking.py --input /tmp/td-merged.json --to me@example.com --min-sources 2 --min-score 12
""",
    )
    parser.add_argument("--input", "-i", type=Path, default=Path("/tmp/td-merged.json"),
                        help="Merged pipeline JSON (default: /tmp/td-merged.json)")
    parser.add_argument("--to", action="append", required=True,
                        help="Recipient email address (repeatable)")
    parser.add_argument("--subject", "-s", default="🚨 Breaking Tech News",
                        help="Email subject line")
    parser.add_argument("--from", dest="from_addr", default=None,
                        help="From address (optional)")
    parser.add_argument("--min-sources", type=int, default=MULTI_SOURCE_THRESHOLD,
                        help=f"Min source count to flag as breaking (default: {MULTI_SOURCE_THRESHOLD})")
    parser.add_argument("--min-score", type=float, default=HIGH_SCORE_THRESHOLD,
                        help=f"Min quality score to flag as breaking (default: {HIGH_SCORE_THRESHOLD})")
    parser.add_argument("--html-out", type=Path, default=None,
                        help="Save generated HTML to this path (useful for inspection)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate HTML but do not send email")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    if not args.input.exists():
        logger.error(f"Input not found: {args.input} — run the pipeline first.")
        return 1

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    breaking = find_breaking_stories(data, args.min_sources, args.min_score)

    if not breaking:
        logger.info(
            f"No breaking stories found "
            f"(min_sources={args.min_sources}, min_score={args.min_score})"
        )
        return 0

    logger.info(f"Found {len(breaking)} breaking stories:")
    for a in breaking:
        logger.info(
            f"  [{a.get('quality_score', 0):.0f}pts / ×{a.get('source_count', 1)}src] "
            f"{a.get('title', '')[:70]}"
        )

    input_stats = {
        **data.get("input_sources", {}),
        "total_articles": data.get("output_stats", {}).get("total_articles", "?"),
    }
    html_body = render_html(breaking, data.get("generated", ""), input_stats)

    html_path = args.html_out or Path(
        tempfile.mktemp(prefix="td-breaking-", suffix=".html")
    )
    html_path.write_text(html_body, encoding="utf-8")
    logger.info(f"HTML saved: {html_path}")

    if args.dry_run:
        logger.info("--dry-run: skipping send. Open the HTML file to preview.")
        return 0

    # Delegate to send-email.py (handles msmtp / sendmail fallback)
    scripts_dir = Path(__file__).parent
    send_cmd = [
        sys.executable, str(scripts_dir / "send-email.py"),
        "--subject", args.subject,
        "--html", str(html_path),
    ]
    for addr in args.to:
        send_cmd += ["--to", addr]
    if args.from_addr:
        send_cmd += ["--from", args.from_addr]
    if args.verbose:
        send_cmd.append("--verbose")

    result = subprocess.run(send_cmd, capture_output=not args.verbose)
    if result.returncode == 0:
        logger.info(f"✅ Alert sent to {', '.join(args.to)}")
        return 0

    logger.error("❌ Email send failed")
    if not args.verbose and result.stderr:
        logger.error(result.stderr.decode().strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
