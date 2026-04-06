#!/usr/bin/env python3
"""
Send a text file to a Discord webhook, splitting at the 2000-char limit.

Reads DISCORD_WEBHOOK_URL from environment (or --webhook flag).

Usage:
    DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... \\
        python3 send-discord.py --file /tmp/td-discord.txt

    python3 send-discord.py --file /tmp/td-discord.txt \\
        --webhook https://discord.com/api/webhooks/...

    # Pipe text directly
    echo "hello" | python3 send-discord.py --stdin --webhook https://...
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

DISCORD_CHAR_LIMIT = 1990  # leave a small buffer below 2000


def split_message(text: str, limit: int = DISCORD_CHAR_LIMIT) -> list[str]:
    """Split text into chunks ≤ limit, preferring newline boundaries."""
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Find last newline within limit
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")

    return chunks


def send_chunk(webhook_url: str, content: str, retry: int = 3) -> bool:
    """POST a single chunk to the webhook. Returns True on success."""
    payload = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        },
        method="POST",
    )
    for attempt in range(1, retry + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                # 204 No Content is Discord's success response
                if resp.status in (200, 204):
                    return True
                logging.warning(f"Unexpected status {resp.status} on attempt {attempt}")
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429:  # rate limited
                retry_after = 1.0
                try:
                    retry_after = json.loads(body).get("retry_after", 1.0)
                except Exception:
                    pass
                logging.warning(f"Rate limited, sleeping {retry_after}s")
                time.sleep(retry_after)
            else:
                logging.error(f"HTTP {e.code}: {body[:200]}")
                return False
        except Exception as e:
            logging.error(f"Request error (attempt {attempt}): {e}")
            if attempt < retry:
                time.sleep(2)
    return False


def _load_messages(file: Path) -> list[str]:
    """
    Load messages from file. Supports two formats:
    - JSON array of strings (from render-discord.py) → one Discord message per element
    - Plain text → split into 2000-char chunks
    """
    raw = file.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            # Flatten: each element may still exceed 2000 chars
            result = []
            for item in parsed:
                result.extend(split_message(str(item)))
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    # Plain text fallback
    return split_message(raw.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Send text to a Discord webhook")
    parser.add_argument("--file", "-f", type=Path, default=None,
                        help="JSON array or plain text file to send")
    parser.add_argument("--stdin", action="store_true", help="Read message from stdin")
    parser.add_argument(
        "--webhook", "-w",
        default=os.getenv("DISCORD_WEBHOOK_URL"),
        help="Discord webhook URL (or set DISCORD_WEBHOOK_URL env var)",
    )
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Seconds between messages (default: 0.5)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if not args.webhook:
        logging.error("No webhook URL — set DISCORD_WEBHOOK_URL or pass --webhook")
        return 1

    if args.stdin:
        messages = split_message(sys.stdin.read().strip())
    elif args.file:
        if not args.file.exists():
            logging.error(f"File not found: {args.file}")
            return 1
        messages = _load_messages(args.file)
    else:
        logging.error("Provide --file <path> or --stdin")
        return 1

    messages = [m for m in messages if m.strip()]
    if not messages:
        logging.warning("Empty message, nothing to send")
        return 0

    total_chars = sum(len(m) for m in messages)
    logging.info(f"Sending {len(messages)} message(s) ({total_chars:,} chars total)")

    for i, msg in enumerate(messages, 1):
        logging.debug(f"  message {i}/{len(messages)} ({len(msg)} chars)")
        if not send_chunk(args.webhook, msg):
            logging.error(f"❌ Failed to send message {i}/{len(messages)}")
            return 1
        if i < len(messages):
            time.sleep(args.delay)

    logging.info("✅ Discord messages sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
