#!/usr/bin/env bash
# schedule.sh — install or uninstall a local cron job for tech-news-digest.
#
# On macOS, uses launchd (runs even when the terminal is closed, handles wake-from-sleep).
# Falls back to cron if --use-cron is passed.
#
# Usage:
#   ./scripts/schedule.sh install [--time HH:MM] [--mode daily|weekly] [--use-cron]
#   ./scripts/schedule.sh uninstall
#   ./scripts/schedule.sh status
#
# Examples:
#   ./scripts/schedule.sh install --time 07:30
#   ./scripts/schedule.sh install --time 09:00 --mode weekly
#   ./scripts/schedule.sh uninstall
#   ./scripts/schedule.sh status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DIGEST_SCRIPT="$SCRIPT_DIR/digest.py"
PYTHON="${PYTHON:-$(which python3)}"
LAUNCHD_LABEL="com.tech-news-digest.daily"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
CRON_MARKER="# tech-news-digest"
LOG_DIR="$REPO_ROOT/workspace/logs"

# ── helpers ───────────────────────────────────────────────────────────────────

usage() {
  grep '^#' "$0" | grep -v '#!/' | sed 's/^# \?//'
  exit 0
}

die() { echo "❌ $*" >&2; exit 1; }
info() { echo "ℹ️  $*"; }
ok() { echo "✅ $*"; }

# ── subcommands ───────────────────────────────────────────────────────────────

cmd_install() {
  local time_str="07:00"
  local mode="daily"
  local use_cron=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --time)    time_str="$2"; shift 2 ;;
      --mode)    mode="$2"; shift 2 ;;
      --use-cron) use_cron=true; shift ;;
      *) die "Unknown option: $1" ;;
    esac
  done

  local hour="${time_str%%:*}"
  local minute="${time_str##*:}"

  # Validate
  [[ "$mode" =~ ^(daily|weekly)$ ]] || die "--mode must be daily or weekly"
  [[ "$hour" =~ ^[0-9]{1,2}$ ]] && [[ "$minute" =~ ^[0-9]{2}$ ]] \
    || die "--time must be HH:MM (e.g. 07:30)"

  mkdir -p "$LOG_DIR"

  # Build the command that will run
  local cmd="$PYTHON $DIGEST_SCRIPT --mode $mode"

  if [[ "$use_cron" == true ]] || [[ "$(uname)" != "Darwin" ]]; then
    _install_cron "$hour" "$minute" "$cmd"
  else
    _install_launchd "$hour" "$minute" "$mode" "$cmd"
  fi
}

_install_launchd() {
  local hour="$1" minute="$2" mode="$3"
  shift 3
  local cmd="$*"

  local interval_key="StartCalendarInterval"
  local interval_val="<dict><key>Hour</key><integer>${hour}</integer><key>Minute</key><integer>${minute}</integer></dict>"

  # Weekly: run on Sunday (weekday=0)
  if [[ "$mode" == "weekly" ]]; then
    interval_val="<dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>${hour}</integer><key>Minute</key><integer>${minute}</integer></dict>"
  fi

  mkdir -p "$HOME/Library/LaunchAgents"

  cat > "$LAUNCHD_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${DIGEST_SCRIPT}</string>
    <string>--mode</string>
    <string>${mode}</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${REPO_ROOT}</string>

  <key>${interval_key}</key>
  ${interval_val}

  <!-- Re-run immediately if the Mac was asleep at the scheduled time -->
  <key>RunAtLoad</key>
  <false/>

  <key>StandardOutPath</key>
  <string>${LOG_DIR}/digest.log</string>

  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/digest.error.log</string>

  <!-- Pass current environment (env vars like DISCORD_WEBHOOK_URL, SMTP_*) -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>PATH</key>
    <string>${PATH}</string>
    <key>DISCORD_WEBHOOK_URL</key>
    <string>${DISCORD_WEBHOOK_URL:-}</string>
    <key>EMAIL_TO</key>
    <string>${EMAIL_TO:-}</string>
    <key>EMAIL_FROM</key>
    <string>${EMAIL_FROM:-}</string>
    <key>SMTP_HOST</key>
    <string>${SMTP_HOST:-}</string>
    <key>SMTP_USER</key>
    <string>${SMTP_USER:-}</string>
    <key>SMTP_PASS</key>
    <string>${SMTP_PASS:-}</string>
    <key>SMTP_PORT</key>
    <string>${SMTP_PORT:-587}</string>
  </dict>
</dict>
</plist>
EOF

  # Unload existing job if running, then load new one
  launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
  launchctl load -w "$LAUNCHD_PLIST"

  ok "launchd job installed: ${LAUNCHD_LABEL}"
  info "Schedule: ${mode} at $(printf '%02d' "$hour"):${minute}"
  info "Logs: ${LOG_DIR}/digest.log"
  info "Plist: ${LAUNCHD_PLIST}"
  info ""
  info "To run once right now:  launchctl start ${LAUNCHD_LABEL}"
  info "To check status:        ./scripts/schedule.sh status"
}

_install_cron() {
  local hour="$1" minute="$2"
  shift 2
  local cmd="$*"

  # Remove any existing entry
  crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab - || true

  local cron_expr="${minute} ${hour} * * *"
  local log="$LOG_DIR/digest.log"

  # Append new entry
  (crontab -l 2>/dev/null; echo "${cron_expr} cd ${REPO_ROOT} && ${cmd} >> ${log} 2>&1 ${CRON_MARKER}") | crontab -

  ok "cron job installed: ${cron_expr}"
  info "Log: ${log}"
  info "Run 'crontab -l' to verify"
}

cmd_uninstall() {
  local removed=false

  if [[ -f "$LAUNCHD_PLIST" ]]; then
    launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
    rm -f "$LAUNCHD_PLIST"
    ok "launchd job removed: ${LAUNCHD_LABEL}"
    removed=true
  fi

  # Remove cron entry if any
  if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab -
    ok "cron entry removed"
    removed=true
  fi

  if [[ "$removed" == false ]]; then
    info "No scheduled job found to remove"
  fi
}

cmd_status() {
  echo ""
  echo "── launchd ──────────────────────────────────────"
  if [[ -f "$LAUNCHD_PLIST" ]]; then
    echo "  Plist: $LAUNCHD_PLIST ✅"
    launchctl list 2>/dev/null | grep "$LAUNCHD_LABEL" \
      && echo "" || echo "  (not loaded — run: launchctl load -w $LAUNCHD_PLIST)"
  else
    echo "  Not installed"
  fi

  echo ""
  echo "── cron ─────────────────────────────────────────"
  if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
    crontab -l 2>/dev/null | grep "$CRON_MARKER"
  else
    echo "  Not installed"
  fi

  echo ""
  echo "── recent logs ──────────────────────────────────"
  local logfile="$LOG_DIR/digest.log"
  if [[ -f "$logfile" ]]; then
    echo "  $logfile (last 20 lines):"
    tail -20 "$logfile"
  else
    echo "  No log file found at $logfile"
  fi
  echo ""
}

# ── main ──────────────────────────────────────────────────────────────────────

[[ $# -eq 0 ]] && usage

case "$1" in
  install)   shift; cmd_install "$@" ;;
  uninstall) shift; cmd_uninstall ;;
  status)    shift; cmd_status ;;
  help|--help|-h) usage ;;
  *) die "Unknown subcommand '$1'. Use: install | uninstall | status" ;;
esac
