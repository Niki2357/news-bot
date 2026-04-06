#!/usr/bin/env python3
"""
Master entry point — fetch, render, and deliver the tech digest.

Replaces the OpenClaw scheduled job with a fully local pipeline:
  1. Fetch + merge (run-pipeline.py)
  2. Render HTML email (render-html.py)
  3. Render Discord message (render-discord.py)
  4. Generate PDF (generate-pdf.py, optional)
  5. Send email (send-email.py, optional)
  6. Send Discord (send-discord.py, optional)
  7. Archive report to workspace/archive/

All delivery flags default to "send if credentials are present".

Usage:
    # Full run with all configured destinations
    python3 scripts/digest.py

    # Skip fetching (use existing merged JSON from last run)
    python3 scripts/digest.py --skip-fetch

    # Override recipients / webhook at runtime
    python3 scripts/digest.py --email you@example.com
    python3 scripts/digest.py --discord-webhook https://discord.com/api/webhooks/...

    # Dry-run: fetch + render but don't send anything
    python3 scripts/digest.py --dry-run

    # Weekly mode (wider time window, more articles)
    python3 scripts/digest.py --mode weekly

Environment variables (all optional — missing ones just skip that delivery):
    SMTP_HOST, SMTP_USER, SMTP_PASS, SMTP_PORT   — email via SMTP
    DISCORD_WEBHOOK_URL                           — Discord delivery
    EMAIL_TO                                      — default recipient(s), comma-separated
    EMAIL_FROM                                    — sender address
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent


def _load_dotenv(env_file: Path = REPO_ROOT / ".env") -> None:
    """Load key=value pairs from .env into os.environ (existing vars win)."""
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


_load_dotenv()


def setup_logging(verbose: bool) -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(__name__)


def run(label: str, cmd: list, logger: logging.Logger, timeout: int = 300) -> bool:
    """Run a subprocess, stream stderr on failure. Returns True on success."""
    logger.info(f"  → {label}")
    logger.debug(f"    cmd: {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ,
    )
    if result.returncode != 0:
        logger.error(f"  ❌ {label} failed (exit {result.returncode})")
        for line in (result.stderr or "").strip().splitlines()[-10:]:
            logger.error(f"     {line}")
        return False
    return True


def archive_report(discord_json: Path, date: str, mode: str, archive_dir: Path,
                   logger: logging.Logger) -> None:
    """Save the rendered messages as a readable markdown archive."""
    if not discord_json.exists():
        return
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{mode}-{date}.md"
    try:
        messages = json.loads(discord_json.read_text(encoding="utf-8"))
        if isinstance(messages, list):
            dest.write_text("\n\n".join(messages), encoding="utf-8")
        else:
            dest.write_text(str(messages), encoding="utf-8")
    except (json.JSONDecodeError, TypeError):
        dest.write_text(discord_json.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info(f"  📁 Archived → {dest}")

    # Prune reports older than 90 days
    cutoff = datetime.now().timestamp() - 90 * 86400
    for f in archive_dir.glob("*.md"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            logger.debug(f"  🗑  Pruned old archive: {f.name}")


def main() -> int:
    _default_defaults = REPO_ROOT / "config" / "defaults"
    _default_archive = REPO_ROOT / "workspace" / "archive" / "tech-news-digest"

    parser = argparse.ArgumentParser(
        description="Full tech-news-digest delivery pipeline (local, no OpenClaw required)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Pipeline control
    parser.add_argument("--mode", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip fetch/merge phase, use --input directly")
    parser.add_argument("--input", type=Path, default=Path("/tmp/td-merged.json"),
                        help="Path to merged JSON (default: /tmp/td-merged.json)")
    parser.add_argument("--defaults", type=Path, default=_default_defaults)
    parser.add_argument("--config", type=Path, default=None,
                        help="User config overlay dir (workspace/config)")
    parser.add_argument("--archive-dir", type=Path, default=_default_archive)
    parser.add_argument("--hours", type=int, default=None,
                        help="Lookback hours (default: 48 daily, 168 weekly)")
    parser.add_argument("--top", type=int, default=5,
                        help="Top N articles per topic section (default: 5)")
    parser.add_argument("--date", type=str, default=None,
                        help="Report date YYYY-MM-DD (default: today)")
    parser.add_argument("--enrich", action="store_true",
                        help="Fetch full text for top articles (slower)")
    parser.add_argument("--force", action="store_true",
                        help="Force re-fetch ignoring caches")
    parser.add_argument("--skip-summarize", action="store_true",
                        help="Skip NVIDIA summarization even if NVIDIA_API_KEY is set")

    # Delivery flags
    parser.add_argument("--email", dest="email_to", default=None,
                        help="Recipient email (overrides EMAIL_TO env var)")
    parser.add_argument("--email-from", default=None,
                        help="Sender address (overrides EMAIL_FROM env var)")
    parser.add_argument("--no-email", action="store_true", help="Disable email delivery")
    parser.add_argument("--discord-webhook", default=None,
                        help="Discord webhook URL (overrides DISCORD_WEBHOOK_URL env var)")
    parser.add_argument("--no-discord", action="store_true", help="Disable Discord delivery")
    parser.add_argument("--pdf", action="store_true", help="Attach PDF to email (requires weasyprint + libgobject)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + render but do not send anything")

    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    date = args.date or datetime.now().strftime("%Y-%m-%d")
    hours = args.hours or (168 if args.mode == "weekly" else 48)
    mode_label = "Weekly" if args.mode == "weekly" else "Daily"
    top_n = args.top

    # Resolve delivery credentials
    email_to_raw = args.email_to or os.getenv("EMAIL_TO", "")
    email_to = [e.strip() for e in email_to_raw.split(",") if e.strip()]
    email_from = args.email_from or os.getenv("EMAIL_FROM", "")
    discord_webhook = args.discord_webhook or os.getenv("DISCORD_WEBHOOK_URL", "")

    want_email = bool(email_to) and not args.no_email and not args.dry_run
    want_discord = bool(discord_webhook) and not args.no_discord and not args.dry_run

    # Temp output paths
    merged_json = args.input
    email_html = Path("/tmp/td-email.html")
    discord_txt = Path("/tmp/td-discord.json")
    market_json = Path("/tmp/td-market.json")
    pdf_path = Path(f"/tmp/td-digest-{date}.pdf")

    logger.info(f"🐉 {mode_label} Tech Digest — {date}")
    logger.info(f"   Email: {'→ ' + ', '.join(email_to) if want_email else 'skip'}")
    logger.info(f"   Discord: {'webhook set' if want_discord else 'skip'}")
    t0 = time.time()

    # ── Phase 1: Fetch + merge ────────────────────────────────────────────
    if not args.skip_fetch:
        logger.info("📡 Phase 1: Fetch + merge")
        pipeline_cmd = [
            sys.executable, str(SCRIPTS_DIR / "run-pipeline.py"),
            "--defaults", str(args.defaults),
            "--hours", str(hours),
            "--output", str(merged_json),
        ]
        if args.config:
            pipeline_cmd += ["--config", str(args.config)]
        if args.archive_dir:
            pipeline_cmd += ["--archive-dir", str(args.archive_dir)]
        if args.enrich:
            pipeline_cmd.append("--enrich")
        if args.force:
            pipeline_cmd.append("--force")
        if args.verbose:
            pipeline_cmd.append("--verbose")

        if not run("run-pipeline.py", pipeline_cmd, logger, timeout=600):
            return 1
    else:
        if not merged_json.exists():
            logger.error(f"--skip-fetch set but {merged_json} not found")
            return 1
        logger.info(f"♻️  Using existing merged JSON: {merged_json}")

    # ── Phase 2: Summarize (NVIDIA API) ──────────────────────────────────
    nvidia_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if nvidia_key and not args.skip_summarize:
        logger.info("🤖 Phase 2: Summarize (NVIDIA Llama 3.1)")
        summarize_cmd = [
            sys.executable, str(SCRIPTS_DIR / "summarize-articles.py"),
            "--input", str(merged_json),
            "--output", str(merged_json),
            "--only-missing",
        ]
        # No --top cap: summarize all articles so every section gets summaries
        if args.verbose:
            summarize_cmd.append("--verbose")
        # Non-fatal: summarization failure doesn't stop delivery
        if not run("summarize-articles.py", summarize_cmd, logger, timeout=600):
            logger.warning("  ⚠️  Summarization failed, continuing without summaries")
    elif not nvidia_key:
        logger.info("⏭️  Summarization skipped (NVIDIA_API_KEY not set)")

    # ── Phase 3: Render ─────────────────────────────────────────────────��─
    logger.info("🎨 Phase 3: Render")

    verbose_flag = ["--verbose"] if args.verbose else []

    if not run("render-html.py", [
        sys.executable, str(SCRIPTS_DIR / "render-html.py"),
        "--input", str(merged_json),
        "--output", str(email_html),
        "--defaults", str(args.defaults),
        "--top", str(top_n),
        "--date", date,
    ] + verbose_flag, logger):
        return 1

    # Fetch market data (non-fatal)
    run("fetch-market.py", [
        sys.executable, str(SCRIPTS_DIR / "fetch-market.py"),
        "--output", str(market_json),
    ] + verbose_flag, logger, timeout=60)

    discord_cmd = [
        sys.executable, str(SCRIPTS_DIR / "render-discord.py"),
        "--input", str(merged_json),
        "--output", str(discord_txt),
        "--date", date,
        "--top-news", str(top_n),
        "--top-tech", str(top_n),
    ]
    if market_json.exists():
        discord_cmd += ["--market", str(market_json)]
    discord_cmd += verbose_flag

    if not run("render-discord.py", discord_cmd, logger):
        return 1

    # Archive the discord/markdown report regardless of delivery
    archive_report(discord_txt, date, args.mode, args.archive_dir, logger)

    # ── Phase 3: PDF (optional, requires --pdf flag) ──────────────────────
    pdf_ok = False
    if args.pdf and want_email:
        pdf_ok = run(
            "generate-pdf.py",
            [sys.executable, str(SCRIPTS_DIR / "generate-pdf.py"),
             "--input", str(discord_txt), "--output", str(pdf_path)],
            logger,
            timeout=120,
        )
        if not pdf_ok:
            logger.warning("  ⚠️  PDF generation failed — install weasyprint + libgobject to enable")

    # ── Phase 4: Deliver ──────────────────────────────────────────────────
    if args.dry_run:
        logger.info("🧪 Dry-run: skipping all delivery steps")
        logger.info(f"   HTML email: {email_html}")
        logger.info(f"   Discord txt: {discord_txt}")
        logger.info(f"✅ Done ({time.time() - t0:.1f}s)")
        return 0

    logger.info("📬 Phase 4: Deliver")
    errors = 0

    if want_email:
        email_cmd = [
            sys.executable, str(SCRIPTS_DIR / "send-email.py"),
            "--subject", f"{mode_label} Tech Digest - {date}",
            "--html", str(email_html),
        ]
        for addr in email_to:
            email_cmd += ["--to", addr]
        if email_from:
            email_cmd += ["--from", email_from]
        if pdf_ok and pdf_path.exists():
            email_cmd += ["--attach", str(pdf_path)]
        if args.verbose:
            email_cmd.append("--verbose")

        if not run("send-email.py", email_cmd, logger, timeout=60):
            errors += 1
            logger.warning("  ⚠️  Email delivery failed, continuing")

    if want_discord:
        discord_cmd = [
            sys.executable, str(SCRIPTS_DIR / "send-discord.py"),
            "--file", str(discord_txt),
            "--webhook", discord_webhook,
        ]
        if args.verbose:
            discord_cmd.append("--verbose")

        if not run("send-discord.py", discord_cmd, logger, timeout=60):
            errors += 1
            logger.warning("  ⚠️  Discord delivery failed")

    elapsed = time.time() - t0
    if errors:
        logger.warning(f"⚠️  Done with {errors} delivery error(s) ({elapsed:.1f}s)")
        return 1

    logger.info(f"✅ Done ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
