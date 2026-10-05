#!/usr/bin/env bash
# Argos (GNOME Shell) plugin for gh-control.
# Symlink this file into ~/.config/argos/ ; it refreshes every 30 seconds.

warn() {
  echo "⚠ gh | color=#d29922"
  echo "---"
  echo "$1 | color=#d29922"
  echo "Refresh | refresh=true"
}

if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found"
  exit 0
fi

self="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "$self")/../.." 2>/dev/null && pwd)"

if [ -n "$root" ] && [ -f "$root/bin/gh-control" ]; then
  out="$(python3 "$root/bin/gh-control" menu --format argos 2>/dev/null)"
elif cmd="$(command -v gh-control 2>/dev/null)" || cmd="$HOME/.local/bin/gh-control"; [ -x "$cmd" ]; then
  # gh itself is located by gh-control, which also checks the usual install dirs.
  out="$("$cmd" menu --format argos 2>/dev/null)"
else
  out=""
fi

if [ -n "$out" ]; then
  printf '%s\n' "$out"
else
  warn "gh-control not found or failed"
fi
exit 0
