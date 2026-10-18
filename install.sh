#!/usr/bin/env bash
# NyRun installer: install / update / uninstall from one small menu.
#   ./install.sh              open the menu
#   ./install.sh install      (or: update | uninstall) run one action directly
set -u

REPO_URL="https://github.com/77nyan/nyrun"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; D=$'\e[2m'; N=$'\e[0m'; else B=; G=; R=; Y=; D=; N=; fi
say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%s✗%s %s\n' "$R" "$N" "$*" >&2; return 1; }
ask()  { local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

# Where to install from: this folder if it is a NyRun checkout, otherwise GitHub.
if [ -f "$HERE/pyproject.toml" ] && grep -q '^name = "nyrun"' "$HERE/pyproject.toml" 2>/dev/null; then
  SOURCE="$HERE"; SOURCE_NAME="this folder"
else
  SOURCE="git+$REPO_URL"; SOURCE_NAME="GitHub"
fi

need_python() {
  command -v python3 >/dev/null || { die "Python 3 is missing. Install Python 3.12 or newer first."; return 1; }
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || { die "NyRun needs Python 3.12+ (you have $(python3 -V 2>&1))."; return 1; }
}

ensure_pipx() {
  command -v pipx >/dev/null && return 0
  warn "pipx is not installed - it keeps NyRun in its own clean environment."
  ask "Install pipx now?" || { die "pipx is required."; return 1; }
  if   command -v pacman  >/dev/null; then sudo pacman -S --needed --noconfirm python-pipx
  elif command -v apt-get >/dev/null; then sudo apt-get install -y pipx
  elif command -v dnf     >/dev/null; then sudo dnf install -y pipx
  elif command -v brew    >/dev/null; then brew install pipx
  else die "Could not detect your package manager. Install pipx yourself, then run this again."; return 1; fi
  command -v pipx >/dev/null || { die "pipx still not found."; return 1; }
  pipx ensurepath >/dev/null 2>&1
}

check_vlc() {
  if command -v vlc >/dev/null || command -v flatpak >/dev/null && flatpak info org.videolan.VLC >/dev/null 2>&1; then
    ok "VLC found"
  else
    warn "VLC was not found - NyRun needs it to play things."
    command -v pacman >/dev/null && say "   install it with: ${B}sudo pacman -S vlc${N}"
  fi
}

do_install() {
  need_python && ensure_pipx || return 1
  say "Installing NyRun from $SOURCE_NAME..."
  pipx install --force "$SOURCE" || { die "Install failed."; return 1; }
  ok "NyRun installed: $(nyrun -version 2>/dev/null || echo 'open a new terminal, then run: nyrun -version')"
  check_vlc
  say "${D}First time? Run:  nyrun -createfolder   then   nyrun${N}"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "Open a new terminal (or run: source ~/.bashrc) so the nyrun command is found." ;; esac
}

do_update() {
  need_python && ensure_pipx || return 1
  if [ "$SOURCE" = "$HERE" ] && [ -d "$HERE/.git" ] && command -v git >/dev/null; then
    say "Fetching the newest version..."
    git -C "$HERE" pull --ff-only || warn "git pull did not work - updating from what is in this folder."
  fi
  local before; before="$(nyrun -version 2>/dev/null || echo 'not installed')"
  pipx install --force "$SOURCE" || { die "Update failed."; return 1; }
  ok "$before  ->  $(nyrun -version 2>/dev/null || echo done)"
}

do_uninstall() {
  command -v pipx >/dev/null || { die "pipx not found - nothing to uninstall."; return 1; }
  pipx uninstall nyrun || { die "NyRun does not seem to be installed."; return 1; }
  ok "NyRun removed."
  say "${D}Your movies, shows and music are NOT touched.${N}"
  if ask "Also delete NyRun's settings, watched marks and poster cache?"; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/nyrun" "${XDG_CACHE_HOME:-$HOME/.cache}/nyrun"
    ok "Settings and cache deleted."
  fi
}

run() {
  case "$1" in
    1|install)   do_install ;;
    2|update)    do_update ;;
    3|uninstall) do_uninstall ;;
    4|q|quit|exit) exit 0 ;;
    *) warn "Choose 1, 2, 3 or 4." ;;
  esac
}

if [ $# -gt 0 ]; then run "$1"; exit $?; fi

while true; do
  say ""
  say "${B}NyRun installer${N}  ${D}(source: $SOURCE_NAME)${N}"
  say "  ${B}1${N}) Install"
  say "  ${B}2${N}) Update"
  say "  ${B}3${N}) Uninstall"
  say "  ${B}4${N}) Quit"
  read -r -p "> " choice || exit 0
  run "$choice"
done
