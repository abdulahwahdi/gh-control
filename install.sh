#!/bin/sh
# gh-control installer: https://github.com/abdulahwahdi/gh-control
#
#   curl -fsSL https://raw.githubusercontent.com/abdulahwahdi/gh-control/main/install.sh | sh
#   curl -fsSL .../install.sh | sh -s -- --uninstall
#
# Installs the gh-control CLI for the current user (no root needed):
#   1. pipx install           (preferred)
#   2. python3 -m pip --user  (if pipx is missing)
#   3. plain copy into ~/.local/share/gh-control + ~/.local/bin/gh-control
# then runs `gh-control install` (top-bar plugin) and `gh-control doctor`.
# Running it again upgrades / repairs the install.
#
# Options:
#   --uninstall        remove the plugin and the CLI (your config is kept)
#   --dry-run          print what would be done, change nothing
#   --ref <tag>        install this tag/branch (default: main)
#   --method <m>       auto | pipx | pip | source (default: auto)
#   --frontend <f>     passed to `gh-control install` (auto, swiftbar, xbar, argos, tray)
#   -h, --help         show this help
#
# Environment: GH_CONTROL_REPO (owner/name), GH_CONTROL_REF.

set -eu

DEFAULT_REPO="abdulahwahdi/gh-control"
REPO="${GH_CONTROL_REPO:-$DEFAULT_REPO}"
REF="${GH_CONTROL_REF:-main}"
METHOD="auto"
FRONTEND="auto"
ACTION="install"
DRY_RUN=0

SHARE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gh-control"
BIN_DIR="$HOME/.local/bin"

if [ -t 1 ]; then
  C_BOLD="$(printf '\033[1m')"; C_GREEN="$(printf '\033[32m')"
  C_YELLOW="$(printf '\033[33m')"; C_RED="$(printf '\033[31m')"
  C_RESET="$(printf '\033[0m')"
else
  C_BOLD=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_RESET=""
fi

say() { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s%s\n' "$C_BOLD" "$C_RESET$C_BOLD" "$*" "$C_RESET"; }
ok() { printf '%s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die() { printf '%s✗%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# Run a command, or only print it with --dry-run.
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    say "[dry-run] $*"
  else
    "$@"
  fi
}

usage() {
  if [ -f "$0" ]; then
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  else
    say "Usage: install.sh [--uninstall] [--dry-run] [--ref <tag>] [--method auto|pipx|pip|source] [--frontend <f>]"
  fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) ACTION="uninstall" ;;
    --dry-run) DRY_RUN=1 ;;
    --ref) [ $# -ge 2 ] || die "--ref needs a value"; REF="$2"; shift ;;
    --ref=*) REF="${1#--ref=}" ;;
    --method) [ $# -ge 2 ] || die "--method needs a value"; METHOD="$2"; shift ;;
    --method=*) METHOD="${1#--method=}" ;;
    --frontend) [ $# -ge 2 ] || die "--frontend needs a value"; FRONTEND="$2"; shift ;;
    --frontend=*) FRONTEND="${1#--frontend=}" ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
  shift
done

case "$METHOD" in
  auto|pipx|pip|source) ;;
  *) die "Unknown --method: $METHOD (auto, pipx, pip, source)" ;;
esac

TARBALL="https://github.com/$REPO/archive/$REF.tar.gz"

# When run from a checkout (sh install.sh), install that checkout instead of
# downloading. Piped from curl, $0 is the shell, so this never matches.
LOCAL_SRC=""
script_dir=""
if [ -f "$0" ]; then
  script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || script_dir=""
fi
if [ -n "$script_dir" ] && [ -f "$script_dir/gh_control/__init__.py" ] && [ -f "$script_dir/bin/gh-control" ]; then
  LOCAL_SRC="$script_dir"
fi
SRC_SPEC="${LOCAL_SRC:-$TARBALL}"

have() { command -v "$1" >/dev/null 2>&1; }

case ":$PATH:" in
  *":$BIN_DIR:"*) BIN_ON_PATH=1 ;;
  *) BIN_ON_PATH=0 ;;
esac

# --------------------------------------------------------------------------
# Prerequisites

check_python() {
  have python3 || die "python3 is required. macOS: xcode-select --install (or brew install python); Linux: install python3 with your package manager."
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' \
    || die "Python 3.8 or newer is required (found $(python3 -V 2>&1))."
}

check_gh() {
  if ! have gh; then
    warn "GitHub CLI (gh) not found. Install it: brew install gh  (or https://cli.github.com)"
    return 0
  fi
  ver="$(gh --version 2>/dev/null | sed -n 's/^gh version \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n 1)"
  major="${ver%%.*}"; minor="${ver#*.}"
  if [ -n "$ver" ] && { [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 40 ]; }; }; then
    warn "gh $ver is too old; account switching needs gh >= 2.40. Upgrade gh."
  fi
}

# --------------------------------------------------------------------------
# Install methods. Each returns non-zero to fall through to the next one.

install_pipx() {
  have pipx || return 1
  step "Installing with pipx"
  run pipx install --force "$SRC_SPEC"
}

pip_usable() {
  python3 -m pip --version >/dev/null 2>&1 || return 1
  # Older pip (notably Debian/Ubuntu's 22.0) builds pyproject-only packages
  # as "UNKNOWN"; skip it and fall back to the source install.
  pip_major="$(python3 -m pip --version 2>/dev/null | sed -n 's/^pip \([0-9][0-9]*\).*/\1/p')"
  [ -n "$pip_major" ] && [ "$pip_major" -ge 23 ]
}

install_pip() {
  pip_usable || return 1
  step "Installing with pip --user"
  # Fails on "externally managed" Pythons (PEP 668); we then fall back.
  run python3 -m pip install --user --upgrade --quiet "$SRC_SPEC"
}

fetch_source() {
  # $1: destination directory (fresh, empty)
  if [ -n "$LOCAL_SRC" ]; then
    run cp -R "$LOCAL_SRC/bin" "$LOCAL_SRC/gh_control" "$1/"
    for f in README.md LICENSE pyproject.toml; do
      if [ -f "$LOCAL_SRC/$f" ]; then run cp "$LOCAL_SRC/$f" "$1/"; fi
    done
    return 0
  fi
  tmp_tar="$1/src.tar.gz"
  if have curl; then
    run curl -fsSL -o "$tmp_tar" "$TARBALL"
  elif have wget; then
    run wget -q -O "$tmp_tar" "$TARBALL"
  else
    die "Need curl or wget to download $TARBALL"
  fi
  run tar -xzf "$tmp_tar" -C "$1" --strip-components 1
  run rm -f "$tmp_tar"
}

install_source() {
  step "Installing into $SHARE_DIR"
  new_dir="$SHARE_DIR.new"
  run rm -rf "$new_dir"
  run mkdir -p "$new_dir"
  # Remove the half-finished copy if the download or copy fails.
  [ "$DRY_RUN" -eq 1 ] || trap 'rm -rf "$new_dir"' EXIT
  fetch_source "$new_dir"
  run rm -rf "$SHARE_DIR"
  run mv "$new_dir" "$SHARE_DIR"
  trap - EXIT
  run mkdir -p "$BIN_DIR"
  if [ -e "$BIN_DIR/gh-control" ] && [ ! -L "$BIN_DIR/gh-control" ]; then
    die "$BIN_DIR/gh-control exists and is not a symlink; remove it and re-run."
  fi
  run ln -sfn "$SHARE_DIR/bin/gh-control" "$BIN_DIR/gh-control"
  run chmod +x "$SHARE_DIR/bin/gh-control"
}

# Locate the gh-control command after installing.
find_cli() {
  if have gh-control; then command -v gh-control; return 0; fi
  for d in "$BIN_DIR" "$(python3 -c 'import os, sysconfig; print(sysconfig.get_path("scripts", os.name + "_user"))' 2>/dev/null || true)"; do
    if [ -n "$d" ] && [ -x "$d/gh-control" ]; then printf '%s\n' "$d/gh-control"; return 0; fi
  done
  return 1
}

path_hint() {
  cli_dir="$(dirname "$1")"
  case ":$PATH:" in
    *":$cli_dir:"*) ;;
    *)
      warn "$cli_dir is not on your PATH. Add it, e.g.:"
      say "    echo 'export PATH=\"$cli_dir:\$PATH\"' >> ~/.profile   # or ~/.zshrc"
      ;;
  esac
}

do_install() {
  check_python
  check_gh
  if [ -n "$LOCAL_SRC" ]; then
    say "Installing gh-control from local checkout $LOCAL_SRC"
  else
    say "Installing gh-control from $REPO@$REF"
  fi

  installed=""
  case "$METHOD" in
    pipx) install_pipx || die "pipx install failed (is pipx installed?)"; installed=pipx ;;
    pip) install_pip || die "pip install failed"; installed=pip ;;
    source) install_source; installed=source ;;
    auto)
      if install_pipx; then installed=pipx
      elif install_pip; then installed=pip
      else
        if have pipx || have pip3; then warn "pipx/pip install not possible; using a plain copy instead"; fi
        install_source; installed=source
      fi
      ;;
  esac
  ok "gh-control CLI installed ($installed)"

  if [ "$DRY_RUN" -eq 1 ]; then
    cli="gh-control"
    [ "$installed" = source ] && cli="$BIN_DIR/gh-control"
    [ "$installed" = source ] && [ "$BIN_ON_PATH" -eq 0 ] && path_hint "$cli"
    say "[dry-run] $cli install --dry-run --frontend $FRONTEND"
    if [ -n "$LOCAL_SRC" ]; then
      python3 "$LOCAL_SRC/bin/gh-control" install --dry-run --frontend "$FRONTEND" || true
    fi
    say "[dry-run] $cli doctor"
    return 0
  fi

  cli="$(find_cli)" || die "gh-control was installed but cannot be found; check your PATH."
  path_hint "$cli"

  step "Installing the top-bar plugin"
  "$cli" install --frontend "$FRONTEND" || warn "Plugin install reported a problem (see above)."

  step "Checking your setup"
  "$cli" doctor || warn "gh-control doctor found problems; follow the hints above."
  say ""
  ok "Done. Run ${C_BOLD}gh-control --help${C_RESET} to see all commands."
}

do_uninstall() {
  step "Removing the top-bar plugin"
  if cli="$(find_cli)"; then
    if [ "$DRY_RUN" -eq 1 ]; then
      "$cli" uninstall --dry-run || true
    else
      "$cli" uninstall || warn "Plugin removal reported a problem."
    fi
  elif [ -x "$SHARE_DIR/bin/gh-control" ]; then
    run "$SHARE_DIR/bin/gh-control" uninstall
  else
    say "gh-control command not found; skipping plugin removal."
  fi

  step "Removing the gh-control CLI"
  if have pipx && pipx list --short 2>/dev/null | grep -q '^gh-control '; then
    run pipx uninstall gh-control
  fi
  if have python3 && python3 -m pip show gh-control >/dev/null 2>&1; then
    run python3 -m pip uninstall -y gh-control || warn "pip uninstall failed; remove gh-control manually."
  fi
  if [ -L "$BIN_DIR/gh-control" ]; then
    case "$(readlink "$BIN_DIR/gh-control")" in
      "$SHARE_DIR"/*) run rm -f "$BIN_DIR/gh-control" ;;
    esac
  fi
  if [ -d "$SHARE_DIR" ]; then
    run rm -rf "$SHARE_DIR"
  fi
  ok "gh-control removed. Your config (~/.config/gh-control) and gh logins were not touched."
}

if [ "$ACTION" = uninstall ]; then
  do_uninstall
else
  do_install
fi
