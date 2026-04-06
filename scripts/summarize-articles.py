#!/usr/bin/env python3
"""
Summarize articles using NVIDIA API (Llama 3.1).

Reads the merged JSON, calls NVIDIA's OpenAI-compatible API to generate
a 1-2 sentence summary for each article that lacks one, then writes back
the updated JSON with summaries in the `snippet` field.

Requires: NVIDIA_API_KEY env var (or in .env)

Usage:
    python3 summarize-articles.py --input /tmp/td-merged.json --output /tmp/td-merged.json
    python3 summarize-articles.py --input /tmp/td-merged.json --only-missing
    python3 summarize-articles.py --input /tmp/td-merged.json --top 10 --verbose
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "meta/llama-3.1-8b-instruct"
ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_API_VERSION = "2023-06-01"
MAX_TITLE_LEN = 200
MAX_SNIPPET_INPUT = 400
SUMMARY_MAX_TOKENS = 150  # ~100 words
RATE_DELAY = 0.3  # seconds between API calls

# Skip these — videos have no text to summarize
import re as _re
_VIDEO_URL_RE = _re.compile(
    r"(youtube\.com/watch|youtu\.be/|/news/videos/|vimeo\.com/\d)", _re.IGNORECASE
)
_VIDEO_TITLE_RE = _re.compile(
    r"\[video\]|\(video\)|^watch\s*:|^\s*video\s*:", _re.IGNORECASE
)


def _load_dotenv(env_file: Path) -> None:
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value


_SYSTEM_PROMPT = (
    "You are a news summarizer. "
    "Write a factual summary of 50-100 words. "
    "Start directly with the key facts — never begin with phrases like "
    "'This article', 'The article discusses', 'This piece', or 'In this article'. "
    "Write in plain declarative sentences. "
    "If only a headline is provided, infer what the story is about from it. "
    "Never ask for more information."
)


def _build_user_content(title: str, existing_text: str) -> str:
    title = title[:MAX_TITLE_LEN]
    if existing_text:
        return f"Title: {title}\nContent: {existing_text[:MAX_SNIPPET_INPUT]}"
    return title


def summarize_nvidia(title: str, existing_text: str, api_key: str) -> str | None:
    """Call NVIDIA Llama 3.1 API. Returns summary string or None on failure."""
    payload = json.dumps({
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_content(title, existing_text)},
        ],
        "max_tokens": SUMMARY_MAX_TOKENS,
        "temperature": 0.3,
        "stream": False,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{NVIDIA_API_BASE}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        logging.warning(f"NVIDIA API HTTP {e.code}: {body}")
        return None
    except Exception as e:
        logging.warning(f"NVIDIA API error: {e}")
        return None


def summarize_haiku(title: str, existing_text: str, api_key: str) -> str | None:
    """Call Anthropic Claude Haiku API as fallback. Returns summary string or None on failure."""
    payload = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": SUMMARY_MAX_TOKENS,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": _build_user_content(title, existing_text)},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ANTHROPIC_API_BASE}/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["content"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        logging.warning(f"Anthropic API HTTP {e.code}: {body}")
        return None
    except Exception as e:
        logging.warning(f"Anthropic API error: {e}")
        return None


def summarize_one(
    title: str,
    existing_text: str,
    nvidia_key: str,
    anthropic_key: str = "",
) -> str | None:
    """Try NVIDIA first; fall back to Anthropic Haiku if NVIDIA fails or is unavailable."""
    if nvidia_key:
        result = summarize_nvidia(title, existing_text, nvidia_key)
        if result:
            return result
        logging.debug("  NVIDIA failed — trying Anthropic Haiku fallback")

    if anthropic_key:
        result = summarize_haiku(title, existing_text, anthropic_key)
        if result:
            return result
        logging.debug("  Anthropic Haiku also failed")
    elif not nvidia_key:
        logging.warning("  No summarization API keys available (NVIDIA_API_KEY or ANTHROPIC_API_KEY)")

    return None


def _is_video(a: dict) -> bool:
    link = (a.get("link") or a.get("external_url") or "").strip()
    title = (a.get("title") or "").strip()
    if link and _VIDEO_URL_RE.search(link):
        return True
    if title and _VIDEO_TITLE_RE.search(title):
        return True
    return False


def collect_articles(topics: dict, only_missing: bool, top_n_per_topic: int = 10) -> list[dict]:
    """
    Collect top N articles per topic for summarization.
    Per-topic selection ensures every display section (global-news, hacker-news,
    tech-news, etc.) gets coverage regardless of cross-topic quality score ranking.
    """
    seen_links: set[str] = set()
    result: list[dict] = []

    for topic_data in topics.values():
        bucket: list[dict] = []
        for a in topic_data.get("articles", []):
            if not isinstance(a, dict):
                continue
            # Skip github/trending — their titles are self-explanatory
            if a.get("source_type") in ("github", "github_trending"):
                continue
            # Skip videos — nothing useful to summarize from title alone
            if _is_video(a):
                continue
            if only_missing and (a.get("snippet") or "").strip():
                continue
            bucket.append(a)

        # Take top N for this topic by quality score
        bucket.sort(key=lambda a: a.get("quality_score", 0), reverse=True)
        for a in bucket[:top_n_per_topic]:
            link = (a.get("link") or a.get("external_url") or "").strip()
            if link and link in seen_links:
                continue
            if link:
                seen_links.add(link)
            result.append(a)

    return result


def main() -> int:
    _script_dir = Path(__file__).resolve().parent
    _load_dotenv(_script_dir.parent / ".env")

    parser = argparse.ArgumentParser(
        description="Summarize articles using NVIDIA Llama 3.1 API"
    )
    parser.add_argument("--input", "-i", type=Path, default=Path("/tmp/td-merged.json"))
    parser.add_argument("--output", "-o", type=Path, default=None,
                        help="Output path (default: overwrite input)")
    parser.add_argument("--only-missing", action="store_true",
                        help="Only summarize articles that have no snippet (default: all)")
    parser.add_argument("--top", type=int, default=10,
                        help="Top N articles per topic to summarize (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    nvidia_key = os.getenv("NVIDIA_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    if not nvidia_key and not anthropic_key:
        logging.error("No API keys found — set NVIDIA_API_KEY or ANTHROPIC_API_KEY in .env")
        return 1

    if nvidia_key:
        logging.debug("Using NVIDIA Llama 3.1 (Anthropic Haiku fallback enabled)" if anthropic_key
                      else "Using NVIDIA Llama 3.1 (no fallback configured)")
    else:
        logging.info("NVIDIA_API_KEY not set — using Anthropic Haiku only")

    if not args.input.exists():
        logging.error(f"Input not found: {args.input}")
        return 1

    with open(args.input) as f:
        data = json.load(f)

    topics = data.get("topics", {})
    articles = collect_articles(topics, only_missing=args.only_missing, top_n_per_topic=args.top)

    if not articles:
        logging.info("No articles to summarize")
    else:
        providers = []
        if nvidia_key:
            providers.append("NVIDIA Llama 3.1")
        if anthropic_key:
            providers.append("Anthropic Haiku (fallback)" if nvidia_key else "Anthropic Haiku")
        logging.info(
            f"Summarizing {len(articles)} articles "
            f"({'missing snippets only' if args.only_missing else 'all'}, top {args.top}/topic) "
            f"via {' → '.join(providers)}"
        )

    ok = 0
    fail = 0
    for i, article in enumerate(articles, 1):
        title = (article.get("title") or "").strip()
        existing = (article.get("snippet") or article.get("summary") or "").strip()
        if not title:
            continue

        logging.debug(f"  [{i}/{len(articles)}] {title[:70]}")
        summary = summarize_one(title, existing, nvidia_key, anthropic_key)

        if summary:
            article["snippet"] = summary
            ok += 1
            logging.debug(f"    → {summary[:100]}")
        else:
            fail += 1

        if i < len(articles):
            time.sleep(RATE_DELAY)

    out_path = args.output or args.input
    with open(out_path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logging.info(
        f"✅ Summarized {ok} articles "
        f"({fail} failed) → {out_path}"
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
