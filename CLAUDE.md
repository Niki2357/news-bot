# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Full local run (fetch → summarize → render → deliver)
python3 scripts/digest.py

# Dry run (fetch + render, no sending)
python3 scripts/digest.py --dry-run

# Skip fetch, re-render + deliver using last merged JSON
python3 scripts/digest.py --skip-fetch

# Set up daily scheduled job (macOS launchd, 7:30am)
./scripts/schedule.sh install --time 07:30
./scripts/schedule.sh status
./scripts/schedule.sh uninstall
```

## Environment

All credentials live in `.env` (auto-loaded by `digest.py` — no `source .env` needed):

```
DISCORD_WEBHOOK_URL=    # Discord webhook for delivery
EMAIL_TO=               # Recipient address(es), comma-separated
EMAIL_FROM=             # Sender display name + address
SMTP_HOST/PORT/USER/PASS  # Gmail, Fastmail, etc.
NVIDIA_API_KEY=         # Llama 3.1 summarization (build.nvidia.com)
GETX_API_KEY=           # Twitter via GetXAPI
TWITTERAPI_IO_KEY=      # Twitter via twitterapi.io
X_BEARER_TOKEN=         # Official X API v2
GITHUB_TOKEN=           # GitHub API
BRAVE_API_KEY(S)=       # Brave Search
TAVILY_API_KEY=         # Tavily Search
```

## Architecture

### Pipeline entry point: `scripts/digest.py`

Orchestrates all phases in order. All other scripts can also be run standalone.

```
Phase 1 — Fetch + merge     run-pipeline.py
Phase 2 — Summarize         summarize-articles.py   (NVIDIA Llama 3.1, skipped if no key)
Phase 3 — Render            fetch-market.py
                            render-html.py
                            render-discord.py
Phase 4 — Deliver           send-email.py
                            send-discord.py
```

### Phase 1 — Fetch (`scripts/run-pipeline.py`)

Runs all fetch steps in parallel via `ThreadPoolExecutor`, then merges:

| Script | Source | Topic tag |
|--------|--------|-----------|
| `fetch-rss.py` | BBC World, Guardian, NPR, Reuters | `global-news` |
| `fetch-rss.py` | Hacker News | `hacker-news` |
| `fetch-rss.py` | The Verge, Ars Technica, TechCrunch, MIT Tech Review | `tech-news` |
| `fetch-rss.py` | AI/ML blogs, Substack, YouTube | `llm`, `ai-agent`, `frontier-tech` |
| `fetch-twitter.py` | KOLs (backends: getxapi → twitterapiio → official) | `llm`, `ai-agent` |
| `fetch-github.py` | GitHub releases | `github` |
| `fetch-github.py --trending` | GitHub trending repos | `github_trending` |
| `fetch-reddit.py` | r/MachineLearning, r/Bitcoin, etc. | `llm`, `crypto` |
| `fetch-web.py` | Brave or Tavily search | various |

Each fetch script outputs `{sources: [{articles: [...]}]}`. Output goes to `/tmp/td-merged.json`.

**Merge** (`scripts/merge-sources.py`):
1. Attaches `source_type`, `source_name`, `quality_score` to each article
2. Quality scoring: priority source +3, recency +2, engagement +1–5, multi-source cross-ref +5, already-reported −5
3. Deduplicates: URL normalization → title similarity (SequenceMatcher ≥ 0.75)
4. Groups by topic (priority: `llm > ai-agent > crypto > github > trending > global-news > hacker-news > tech-news > uncategorized`)
5. Domain limits: max 3 articles/domain per topic (github.com, reddit.com, x.com exempt)

### Phase 2 — Summarize (`scripts/summarize-articles.py`)

Calls NVIDIA `meta/llama-3.1-8b-instruct` to generate 50–100 word summaries for articles missing a snippet. Skips videos and GitHub items. Non-fatal — digest continues without summaries if the API is unavailable.

### Phase 3 — Render

**`scripts/fetch-market.py`** — fetches previous day's close/change for S&P 500, NASDAQ, Dow Jones, FTSE 100, Nikkei 225, Bitcoin via Yahoo Finance public API (no key required). Outputs `/tmp/td-market.json`.

**`scripts/render-discord.py`** — produces a JSON array of Discord message strings (`/tmp/td-discord.json`). Each article is its own message. Fixed section order:

1. 🌍 Global & Political News (top 5)
2. 🔥 Hacker News (top 5)
3. 🐙 Trending on GitHub (top 5, by daily star growth)
4. 💻 Tech News (top 5)
5. 📈 Market Summary (one message, all indices)

Format rules: bold titles (`**title**`), `──────────────────────────────` divider after each section, URLs wrapped in `<url>` to suppress Discord embeds, `[D]`/`[P]` Reddit prefixes stripped, videos skipped.

**`scripts/render-html.py`** — produces inline-styled HTML email (`/tmp/td-email.html`). Same article selection as Discord.

### Phase 4 — Deliver

**`scripts/send-discord.py`** — reads the JSON array and POSTs each element as a separate webhook call. Handles rate limiting and 2000-char splitting.

**`scripts/send-email.py`** — sends HTML via SMTP (tries `SMTP_*` env vars → msmtp → sendmail). Optional PDF attachment with `--pdf` flag (requires `weasyprint` + `libgobject`).

## Configuration

- `config/defaults/sources.json` — 175 built-in sources (id, type, enabled, url, topics, priority)
- `config/defaults/topics.json` — 7 topic definitions: `llm`, `ai-agent`, `crypto`, `frontier-tech`, `global-news`, `hacker-news`, `tech-news`
- `scripts/config_loader.py` — user overlay at `workspace/config/tech-news-digest-sources.json` overrides defaults by `id`; set `"enabled": false` to disable a source

## Scheduling (macOS)

`scripts/schedule.sh` installs a launchd plist that runs `digest.py` on a schedule. Env vars from `.env` are snapshotted into the plist at install time — re-run `install` after changing credentials.

```bash
./scripts/schedule.sh install --time 07:30           # daily
./scripts/schedule.sh install --time 09:00 --mode weekly  # weekly on Sunday
./scripts/schedule.sh status    # show plist + last 20 log lines
./scripts/schedule.sh uninstall
```

Logs: `workspace/logs/digest.log`

## Running Individual Scripts

```bash
# Fetch only RSS (26h window)
python3 scripts/run-pipeline.py --only rss --hours 26 --output /tmp/td-merged.json

# Summarize missing snippets (top 30 by quality score)
python3 scripts/summarize-articles.py --input /tmp/td-merged.json --only-missing --top 30

# Fetch market data
python3 scripts/fetch-market.py --output /tmp/td-market.json

# Render Discord messages
python3 scripts/render-discord.py --input /tmp/td-merged.json --market /tmp/td-market.json

# Send to Discord
python3 scripts/send-discord.py --file /tmp/td-discord.json

# Render HTML email
python3 scripts/render-html.py --input /tmp/td-merged.json --output /tmp/td-email.html

# Send email
python3 scripts/send-email.py --to you@example.com --subject "Digest" --html /tmp/td-email.html

# Validate config
python3 scripts/validate-config.py --defaults config/defaults
```

## Tests

```bash
python3 -m pytest tests/ -v
python3 -m pytest tests/test_merge.py
python3 -m pytest tests/ -k "TestDeduplication"
```

`tests/fixtures/` contains captured real-world JSON responses. `TestIntegration` in `test_merge.py` runs a full merge over fixture data. Tests import scripts via `importlib.util` (hyphenated filenames).

## digest.py Flags Reference

```
--mode daily|weekly       Time window: 48h (daily) or 168h (weekly)
--skip-fetch              Use existing /tmp/td-merged.json, skip Phase 1
--skip-summarize          Skip NVIDIA summarization even if key is set
--dry-run                 Fetch + render but do not send
--enrich                  Fetch full article text for top items (slow)
--force                   Re-fetch ignoring caches
--email <addr>            Override EMAIL_TO
--no-email                Disable email delivery
--no-discord              Disable Discord delivery
--pdf                     Attach PDF to email (requires weasyprint + libgobject)
--top N                   Articles per section (default: 5)
--hours N                 Lookback window override
--verbose                 Debug logging
```
