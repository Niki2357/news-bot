# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt         # feedparser, jsonschema (optional but recommended)
pip install weasyprint                  # optional, for PDF generation

# Run tests
python3 -m pytest tests/ -v
python3 -m pytest tests/test_merge.py  # single test file
python3 -m pytest tests/ -k "TestDeduplication"  # single test class

# Run full pipeline (outputs to /tmp/td-merged.json)
python3 scripts/run-pipeline.py \
  --defaults config/defaults \
  --hours 48 \
  --output /tmp/td-merged.json \
  --verbose

# Run subset of pipeline (e.g. only RSS and GitHub)
python3 scripts/run-pipeline.py --only rss,github --defaults config/defaults

# Smoke-test against live sources
./scripts/test-pipeline.sh --only rss,github --hours 24 --keep
./scripts/test-pipeline.sh --skip web,twitter  # skip sources needing API keys

# Validate config files
python3 scripts/validate-config.py --defaults config/defaults
```

## Architecture

The pipeline runs in two phases via `scripts/run-pipeline.py`:

**Phase 1 — Parallel fetch** (all 6 steps run concurrently via `ThreadPoolExecutor`):
- `fetch-rss.py` → RSS/Atom feeds
- `fetch-twitter.py` → Twitter/X (backends: `getxapi` > `twitterapiio` > `official`, controlled by `TWITTER_API_BACKEND` env var)
- `fetch-github.py` → GitHub releases (pass `--trending` flag for trending repos)
- `fetch-reddit.py` → Reddit posts
- `fetch-web.py` → Web search (Brave or Tavily, controlled by `WEB_SEARCH_BACKEND` env var)

Each script writes a JSON file with a consistent structure: `{sources: [{articles: [...]}]}` (RSS/Twitter/GitHub) or `{subreddits: [{articles: [...]}]}` (Reddit) or `{topics: [{articles: [...]}]}` (Web).

**Phase 2 — Merge** (`scripts/merge-sources.py`):
1. Collects all articles, attaches `source_type`, `source_name`, `quality_score`
2. Applies quality scoring (priority source +3, recency +2, engagement tiers +1–5, multi-source cross-ref +5, already-reported penalty −5)
3. Deduplicates: URL normalization first, then title similarity (SequenceMatcher ≥0.75) with token-bucket optimization to avoid O(n²)
4. Groups by topic with cross-topic deduplication (each article in exactly one topic by priority: `llm > ai_agent > crypto > github > trending > uncategorized`)
5. Applies per-topic domain limits (max 3 articles/domain; x.com, github.com, reddit.com are exempt)

**Phase 3 — Enrich** (`scripts/enrich-articles.py`, opt-in via `--enrich`): fetches full text for top-scoring articles.

**Output scripts** (`send-email.py`, `generate-pdf.py`, `summarize-merged.py`): consume the merged JSON and produce delivery artifacts.

## Configuration

- `config/defaults/sources.json` — 151 built-in sources (id, type, enabled, url, topics, priority)
- `config/defaults/topics.json` — topic definitions with search queries
- `scripts/config_loader.py` — merge logic: user overlay at `workspace/config/tech-news-digest-sources.json` overrides defaults by matching `id`; set `"enabled": false` to disable a built-in source

All environment variables are optional — the pipeline skips sources with missing credentials. See README.md for the full list of env vars.

## Test Fixtures

`tests/fixtures/` contains captured real-world JSON responses for each source type. Tests import functions directly from scripts using `importlib.util` (since scripts use hyphenated filenames). The `TestIntegration` class in `test_merge.py` runs a full merge pipeline over fixture data.
