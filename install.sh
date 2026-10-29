#!/usr/bin/env bash
# NyRun installer - install / update / uninstall from one small menu.
#
#     sudo ./install.sh                  open the menu
#     sudo ./install.sh install          (or: update | uninstall) run one action, no menu
#
# This is the only supported way to install NyRun. It puts NyRun in /opt/nyrun
# and adds the `nyrun` command to /usr/local/bin, for everybody on the computer.

# ---------------------------------------------------------------------------
# Guard. Plain POSIX on purpose: this part must also run (and refuse) under `sh`.
# ---------------------------------------------------------------------------
if [ -z "${BASH_VERSION:-}" ] || [ "$(id -u)" != "0" ] || [ "${0#*/}" = "$0" ]; then
  if [ -t 2 ]; then P=$(printf '\033[38;5;213m'); R=$(printf '\033[38;5;203m'); D=$(printf '\033[38;5;245m'); N=$(printf '\033[0m'); B=$(printf '\033[1m'); else P=; R=; D=; N=; B=; fi
  {
    printf '\n  %s✗ NyRun'"'"'s installer cannot be started this way.%s\n\n' "$R$B" "$N"
    printf '  The only way to install, update or remove NyRun is:\n\n'
    printf '      %ssudo ./install.sh%s\n\n' "$P$B" "$N"
    printf '  %sRun it from the NyRun folder, as written. Not with sh, bash or without sudo.%s\n' "$D" "$N"
    printf '  %sIf it says "permission denied", run  chmod +x install.sh  once.%s\n\n' "$D" "$N"
  } >&2
  exit 1
fi

# Box-drawing characters and string lengths need a UTF-8 locale.
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
  *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) ;;
  *) export LC_ALL=C.UTF-8 ;;
esac

# ---------------------------------------------------------------------------
# Settings (the NYRUN_* variables exist for testing; normal users never need them)
# ---------------------------------------------------------------------------
REPO_URL="https://github.com/77nyan/nyrun"
PREFIX="${NYRUN_PREFIX:-/opt/nyrun}"
BINDIR="${NYRUN_BINDIR:-/usr/local/bin}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
umask 022

if [ -f "$HERE/pyproject.toml" ] && grep -q '^name = "nyrun"' "$HERE/pyproject.toml" 2>/dev/null; then
  LOCAL_SRC="$HERE"
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$HERE/pyproject.toml" | head -1)"
else
  LOCAL_SRC=""
  VERSION="latest"
fi
[ -n "${NYRUN_SOURCE:-}" ] && { LOCAL_SRC=""; SOURCE_OVERRIDE="$NYRUN_SOURCE"; } || SOURCE_OVERRIDE=""

# The person who ran sudo: their old pipx install and their settings are theirs, not root's.
RUN_USER="${SUDO_USER:-root}"
RUN_HOME="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6)"
[ -n "$RUN_HOME" ] || RUN_HOME="$HOME"
[ -n "${NYRUN_USER_HOME:-}" ] && RUN_HOME="$NYRUN_USER_HOME"   # testing only

LOG="$(mktemp /tmp/nyrun-install.XXXXXX.log)"
WORK=""

# ---------------------------------------------------------------------------
# Terminal: colours, size, plain mode
# ---------------------------------------------------------------------------
TUI=0
if [ -t 0 ] && [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ] && [ -z "${NYRUN_PLAIN:-}" ]; then TUI=1; fi
ROWS=24; COLS=80
measure() {
  local s
  s="$(stty size </dev/tty 2>/dev/null)" && { ROWS="${s% *}"; COLS="${s#* }"; }
  [[ "$ROWS" =~ ^[0-9]+$ ]] || ROWS=24
  [[ "$COLS" =~ ^[0-9]+$ ]] || COLS=80
}
if [ "$TUI" = 1 ]; then measure; { [ "$COLS" -lt 62 ] || [ "$ROWS" -lt 24 ]; } && TUI=0; fi

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
  E=$'\e'
  C_PINK="${E}[38;5;218m"; C_ACC="${E}[38;5;213m"; C_LAV="${E}[38;5;183m"; C_TXT="${E}[38;5;253m"
  C_DIM="${E}[38;5;245m"; C_OK="${E}[38;5;114m"; C_ERR="${E}[38;5;203m"; C_WARN="${E}[38;5;222m"
  C_BAR="${E}[48;5;53m${E}[38;5;225m"; C_SEL="${E}[48;5;54m"; C_B="${E}[1m"; C_0="${E}[0m"
else
  E=$'\e'; C_PINK=; C_ACC=; C_LAV=; C_TXT=; C_DIM=; C_OK=; C_ERR=; C_WARN=; C_BAR=; C_SEL=; C_B=; C_0=
fi

PL=0   # printable length of the last string given to plain_len
plain_len() {
  local s="$1" re=$'\e\\[[0-9;?]*[a-zA-Z]'
  while [[ $s =~ $re ]]; do s="${s/"${BASH_REMATCH[0]}"/}"; done
  PL=${#s}
}
repeat() { local n=$1 ch="$2" out=""; while [ "$n" -gt 0 ]; do out+="$ch"; n=$((n-1)); done; printf '%s' "$out"; }
trunc() { local s="$1" n=$2; if [ "${#s}" -gt "$n" ]; then printf '%s…' "${s:0:$((n-1))}"; else printf '%s' "$s"; fi; }

# ---------------------------------------------------------------------------
# What is on this computer
# ---------------------------------------------------------------------------
SYS_NAME="Linux"
if [ -r /etc/os-release ]; then SYS_NAME="$(. /etc/os-release; printf '%s' "${PRETTY_NAME:-Linux}")"; fi
PY=""; PY_VER=""
find_python() {
  local c v
  for c in python3.13 python3.12 python3 python3.14; do
    command -v "$c" >/dev/null 2>&1 || continue
    v="$("$c" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3]) if sys.version_info >= (3, 12) else sys.exit(1)' 2>/dev/null)" || continue
    PY="$(command -v "$c")"; PY_VER="$v"; return 0
  done
  return 1
}
PM=""
for _pm in pacman apt-get dnf zypper; do command -v "$_pm" >/dev/null 2>&1 && { PM="$_pm"; break; }; done

INSTALLED_VER=""; HAVE_VLC=0; HAVE_FFMPEG=0; LEGACY=""
detect() {
  INSTALLED_VER=""
  if [ -x "$PREFIX/bin/nyrun" ]; then
    INSTALLED_VER="$("$PREFIX/bin/nyrun" -version 2>/dev/null | sed -n 's/[^0-9]*\([0-9][0-9.]*\).*/\1/p' | head -1)"
    [ -n "$INSTALLED_VER" ] || INSTALLED_VER="unknown"
  fi
  HAVE_VLC=0; HAVE_FFMPEG=0
  command -v vlc >/dev/null 2>&1 && HAVE_VLC=1
  [ "$HAVE_VLC" = 0 ] && command -v flatpak >/dev/null 2>&1 && flatpak info org.videolan.VLC >/dev/null 2>&1 && HAVE_VLC=1
  command -v ffmpeg >/dev/null 2>&1 && HAVE_FFMPEG=1
  # an older install made with pipx for the person who ran sudo
  LEGACY=""
  [ -d "$RUN_HOME/.local/share/pipx/venvs/nyrun" ] && LEGACY="$RUN_HOME/.local/share/pipx/venvs/nyrun"
  [ -z "$LEGACY" ] && [ -d "$RUN_HOME/.local/pipx/venvs/nyrun" ] && LEGACY="$RUN_HOME/.local/pipx/venvs/nyrun"
  find_python || { PY=""; PY_VER=""; }
}

# ---------------------------------------------------------------------------
# Full-screen drawing (TUI mode)
# ---------------------------------------------------------------------------
SPIN_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
SPIN="⠋"
FRAME=()        # lines of the panel, already coloured
HINT=""         # footer text, already coloured
PANEL_W=70

tui_start() {
  [ "$TUI" = 1 ] || return 0
  printf '%s[?1049h%s[?25l%s[2J' "$E" "$E" "$E"
}
tui_stop() {
  [ "$TUI" = 1 ] || return 0
  printf '%s[?25h%s[?1049l' "$E" "$E"
}

# a panel row: colour string in, padded to the panel width
FR() {
  local s="$1" inner=$((PANEL_W - 4)) pad
  plain_len "$s"
  pad=$((inner - PL)); [ "$pad" -lt 0 ] && pad=0
  FRAME+=("${C_ACC}│${C_0} ${s}$(repeat "$pad" ' ')${C_0} ${C_ACC}│${C_0}")
}
FR_GAP() { FR ""; }
FR_RULE() { FRAME+=("${C_ACC}├$(repeat $((PANEL_W - 2)) '─')┤${C_0}"); }

LOGO_ROWS=()
declare -A GLYPH=(
  [N]="X..X XX.X X.XX X..X X..X ...."
  [Y]="X..X X..X .XX. .XX. .XX. ...."
  [R]="XXX. X..X XXX. X.X. X..X ...."
  [U]="X..X X..X X..X X..X .XX. ...."
)
build_logo() {
  # NYRUN in a tiny pixel font, two pixel rows per text row (half blocks), pink to lavender
  local pal=(218 218 217 217 216 183 183 177 177 171 171 135 135 99 99 99 99 99 99 99)
  local pair line col letter x top bot ch
  local -a rows
  LOGO_ROWS=()
  for pair in 0 2 4; do
    line=""; col=0
    for letter in N Y R U N; do
      read -ra rows <<<"${GLYPH[$letter]}"
      for x in 0 1 2 3; do
        top="${rows[$pair]:$x:1}"; bot="${rows[$((pair+1))]:$x:1}"
        if   [ "$top" = X ] && [ "$bot" = X ]; then ch="█"
        elif [ "$top" = X ]; then ch="▀"
        elif [ "$bot" = X ]; then ch="▄"
        else ch=" "; fi
        line+="${E}[38;5;${pal[$col]}m${ch}"
        col=$((col+1))
      done
      line+=" "; col=$((col+1))
    done
    LOGO_ROWS+=("${line% }${C_0}")
  done
}

rainbow_line() {
  local n=$1 i out="" cols=(196 202 208 214 220 190 154 118 84 49 51 45 39 63 99 135 171 207) k
  for ((i=0; i<n; i++)); do k=$(( i * ${#cols[@]} / n )); out+="${E}[38;5;${cols[$k]}m━"; done
  printf '%s%s' "$out" "$C_0"
}

begin_frame() {  # measure the terminal, then start an empty panel
  measure
  PANEL_W=$((COLS - 6)); [ "$PANEL_W" -gt 74 ] && PANEL_W=74
  FRAME=()
}

draw() {
  # FRAME and HINT are filled in by the caller (after begin_frame)
  [ "$TUI" = 1 ] || return 0
  local h=${#FRAME[@]} top i bar_l bar_r gap line
  top=$(( (ROWS - 2 - (h + 2)) / 2 )); [ "$top" -lt 0 ] && top=0
  local left=$(( (COLS - PANEL_W) / 2 )); [ "$left" -lt 0 ] && left=0
  local lp; lp="$(repeat "$left" ' ')"
  local out="${E}[H"
  # top bar
  bar_l=" NyRun installer"; bar_r="v$VERSION "
  gap=$((COLS - ${#bar_l} - ${#bar_r})); [ "$gap" -lt 1 ] && gap=1
  out+="${C_BAR}${C_B}${bar_l}$(repeat "$gap" ' ')${bar_r}${C_0}${E}[K"$'\n'
  for ((i=0; i<top; i++)); do out+="${E}[K"$'\n'; done
  out+="${lp}${C_ACC}╭$(repeat $((PANEL_W - 2)) '─')╮${C_0}${E}[K"$'\n'
  for line in "${FRAME[@]}"; do out+="${lp}${line}${E}[K"$'\n'; done
  out+="${lp}${C_ACC}╰$(repeat $((PANEL_W - 2)) '─')╯${C_0}${E}[K"$'\n'
  # footer, pinned to the last row
  plain_len "$HINT"
  out+="${E}[J${E}[${ROWS};1H${HINT}${C_BAR}$(repeat $((COLS - PL)) ' ')${C_0}"
  printf '%s' "$out"
}

hint() {  # hint "key:label" ...
  local out=" " kv
  for kv in "$@"; do out+="${C_B}${C_ACC}${kv%%:*}${C_0}${C_BAR} ${kv#*:}   "; done
  HINT="${C_BAR}${out}"
}

status_rows() {
  local nyrun_txt vlc_txt ff_txt py_txt
  if [ -n "$INSTALLED_VER" ]; then nyrun_txt="${C_OK}installed${C_0}  ${C_DIM}version ${INSTALLED_VER}${C_0}"; else nyrun_txt="${C_WARN}not installed${C_0}"; fi
  if [ "$HAVE_VLC" = 1 ]; then vlc_txt="${C_OK}found${C_0}"; else vlc_txt="${C_ERR}missing${C_0}  ${C_DIM}(needed to play anything)${C_0}"; fi
  if [ "$HAVE_FFMPEG" = 1 ]; then ff_txt="${C_OK}found${C_0}"; else ff_txt="${C_WARN}missing${C_0}  ${C_DIM}(optional: saving streams)${C_0}"; fi
  if [ -n "$PY_VER" ]; then py_txt="${C_OK}${PY_VER}${C_0}"; else py_txt="${C_ERR}3.12 or newer needed${C_0}"; fi
  FR "${C_DIM}System ${C_0}  ${C_TXT}$(trunc "$SYS_NAME" 30)${C_0}  ${C_DIM}·  Python${C_0} ${py_txt}"
  FR "${C_DIM}NyRun  ${C_0}  ${nyrun_txt}"
  FR "${C_DIM}VLC    ${C_0}  ${vlc_txt}"
  FR "${C_DIM}ffmpeg ${C_0}  ${ff_txt}"
}

banner_rows() {
  build_logo
  local r pad
  for r in "${LOGO_ROWS[@]}"; do
    pad=$(( (PANEL_W - 4 - 24) / 2 )); [ "$pad" -lt 0 ] && pad=0
    FR "$(repeat "$pad" ' ')${r}"
  done
  pad=$(( (PANEL_W - 4 - 22) / 2 )); [ "$pad" -lt 0 ] && pad=0
  FR "$(repeat "$pad" ' ')$(rainbow_line 22)"
  pad=$(( (PANEL_W - 4 - 40) / 2 )); [ "$pad" -lt 0 ] && pad=0
  FR "$(repeat "$pad" ' ')${C_DIM}your cozy video & music selector${C_0}"
}

# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
KEY=""
read_key() {
  local k rest
  IFS= read -rsn1 k </dev/tty || { KEY="quit"; return; }
  case "$k" in
    "") KEY="enter" ;;
    $'\e')
      rest=""
      IFS= read -rsn2 -t 0.05 rest </dev/tty
      case "$rest" in
        "[A") KEY="up" ;; "[B") KEY="down" ;;
        "") KEY="esc" ;;
        *) KEY="other" ;;
      esac ;;
    k|K) KEY="up" ;; j|J) KEY="down" ;;
    q|Q) KEY="quit" ;;
    y|Y) KEY="y" ;; n|N) KEY="n" ;;
    [0-9]) KEY="$k" ;;
    $'\x03') KEY="quit" ;;
    *) KEY="other" ;;
  esac
}

# ---------------------------------------------------------------------------
# One UI layer, two modes (full screen / plain text)
# ---------------------------------------------------------------------------
STEPS=(); STEP_STATE=(); STEP_NOTE=(); STEP_TITLE=""
steps_begin() {  # steps_begin "Title" "step one" "step two" ...
  STEP_TITLE="$1"; shift
  STEPS=("$@"); STEP_STATE=(); STEP_NOTE=()
  local i; for i in "${!STEPS[@]}"; do STEP_STATE[$i]=wait; STEP_NOTE[$i]=""; done
  if [ "$TUI" = 0 ]; then printf '\n%s%s%s\n' "$C_B" "$STEP_TITLE" "$C_0"; fi
}
STEP_FOOT=""
draw_steps() {
  [ "$TUI" = 1 ] || return 0
  begin_frame
  FR_GAP
  FR "${C_B}${C_ACC}${STEP_TITLE}${C_0}"
  FR "${C_DIM}$(repeat $((PANEL_W - 4)) '─')${C_0}"
  local i icon label note w=$((PANEL_W - 4))
  for i in "${!STEPS[@]}"; do
    case "${STEP_STATE[$i]}" in
      wait) icon="${C_DIM}○${C_0}"; label="${C_DIM}${STEPS[$i]}${C_0}" ;;
      run)  icon="${C_ACC}${SPIN}${C_0}"; label="${C_TXT}${STEPS[$i]}${C_0}" ;;
      ok)   icon="${C_OK}✓${C_0}"; label="${C_TXT}${STEPS[$i]}${C_0}" ;;
      warn) icon="${C_WARN}!${C_0}"; label="${C_TXT}${STEPS[$i]}${C_0}" ;;
      fail) icon="${C_ERR}✗${C_0}"; label="${C_ERR}${STEPS[$i]}${C_0}" ;;
      skip) icon="${C_DIM}–${C_0}"; label="${C_DIM}${STEPS[$i]}${C_0}" ;;
    esac
    note="$(trunc "${STEP_NOTE[$i]}" $((w - ${#STEPS[$i]} - 6)))"
    FR "${icon}  ${label}  ${C_DIM}${note}${C_0}"
  done
  FR_GAP
  [ -n "$STEP_FOOT" ] && FR "$STEP_FOOT"
  draw
}
step_set() {  # step_set index state "note"
  STEP_STATE[$1]="$2"; STEP_NOTE[$1]="${3:-}"
  if [ "$TUI" = 1 ]; then draw_steps; else
    case "$2" in
      ok)   printf '  %s✓%s %s%s%s\n' "$C_OK" "$C_0" "${STEPS[$1]}" "$C_DIM" "${3:+  ($3)}$C_0" ;;
      warn) printf '  %s!%s %s%s%s\n' "$C_WARN" "$C_0" "${STEPS[$1]}" "$C_DIM" "${3:+  ($3)}$C_0" ;;
      fail) printf '  %s✗%s %s%s%s\n' "$C_ERR" "$C_0" "${STEPS[$1]}" "$C_DIM" "${3:+  ($3)}$C_0" ;;
      skip) printf '  - %s%s%s\n' "${STEPS[$1]}" "$C_DIM" "${3:+  ($3)}$C_0" ;;
    esac
  fi
}
CHILD=0
run_cmd() {  # run_cmd step_index command...   (output goes to the log; returns the command's status)
  local idx=$1; shift
  step_set "$idx" run "${STEP_NOTE[$idx]}"
  "$@" >>"$LOG" 2>&1 &
  CHILD=$!
  local f=0 rc
  if [ "$TUI" = 1 ]; then
    while kill -0 "$CHILD" 2>/dev/null; do SPIN="${SPIN_FRAMES[$((f % 10))]}"; f=$((f+1)); draw_steps; sleep 0.09; done
  fi
  wait "$CHILD"; rc=$?
  CHILD=0
  return $rc
}

SCREEN_TITLE=""
message_screen() {  # message_screen kind "Title" "line" "line" ...   kind: ok|err|info
  local kind="$1" title="$2"; shift 2
  local icon colr
  case "$kind" in ok) icon="✓"; colr="$C_OK";; err) icon="✗"; colr="$C_ERR";; *) icon="•"; colr="$C_ACC";; esac
  if [ "$TUI" = 1 ]; then
    begin_frame; FR_GAP
    FR "${colr}${C_B}${icon}  ${title}${C_0}"
    FR_GAP
    local l; for l in "$@"; do FR "$l"; done
    FR_GAP
    hint "Enter:continue"
    draw
    while :; do read_key; case "$KEY" in enter|esc|quit|y|n) break;; esac; done
  else
    printf '\n%s%s %s%s\n' "$colr" "$icon" "$title" "$C_0"
    local l; for l in "$@"; do printf '  %s\n' "$(printf '%s' "$l" | sed 's/\x1b\[[0-9;]*m//g')"; done
    printf '\n'
  fi
}

ASSUME_YES=0
confirm() {  # confirm "Question?" default(y|n) ["detail" ...]     (--yes answers "yes" to these)
  local q="$1" def="$2"; shift 2
  if [ "$ASSUME_YES" = 1 ]; then return 0; fi
  ask_user "$q" "$def" "$@"
}
ask_user() {  # like confirm, but --yes never answers it: used where personal data could be lost
  local q="$1" def="$2"; shift 2
  if [ "$TUI" = 1 ]; then
    local sel; [ "$def" = y ] && sel=0 || sel=1
    while :; do
      begin_frame; FR_GAP
      FR "${C_B}${C_ACC}${q}${C_0}"
      FR_GAP
      local l; for l in "$@"; do FR "${C_TXT}${l}${C_0}"; done
      [ "$#" -gt 0 ] && FR_GAP
      if [ "$sel" = 0 ]; then FR "${C_SEL}${C_B}  ❯ Yes  ${C_0}    ${C_DIM}  No  ${C_0}"; else FR "${C_DIM}    Yes  ${C_0}    ${C_SEL}${C_B}  ❯ No  ${C_0}"; fi
      FR_GAP
      hint "←→:choose" "Enter:confirm" "y/n:answer"
      draw
      IFS= read -rsn1 k </dev/tty || return 1
      case "$k" in
        y|Y) return 0 ;; n|N) return 1 ;; "") [ "$sel" = 0 ] && return 0 || return 1 ;;
        $'\e') IFS= read -rsn2 -t 0.05 rest </dev/tty; case "$rest" in "[C"|"[B") sel=1;; "[D"|"[A") sel=0;; "") return 1;; esac ;;
        h|k) sel=0 ;; l|j) sel=1 ;;
        q|Q) return 1 ;;
      esac
    done
  else
    local a prompt; [ "$def" = y ] && prompt="[Y/n]" || prompt="[y/N]"
    printf '\n%s%s%s\n' "$C_B" "$q" "$C_0"
    local l; for l in "$@"; do printf '  %s\n' "$l"; done
    if [ -t 0 ]; then read -r -p "  $prompt " a; else a=""; fi
    case "$a" in y|Y|yes) return 0;; n|N|no) return 1;; *) [ "$def" = y ];; esac
  fi
}

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
cleanup_work() { [ -n "$WORK" ] && [ -d "$WORK" ] && rm -rf "$WORK"; WORK=""; }

pm_install() {  # pm_install pkg...   (run through run_cmd)
  case "$PM" in
    pacman)  pacman -S --needed --noconfirm "$@" ;;
    apt-get) apt-get install -y "$@" ;;
    dnf)     dnf install -y "$@" ;;
    zypper)  zypper --non-interactive install "$@" ;;
    *) return 1 ;;
  esac
}
want_packages() {  # prints the packages to install for what is missing
  local pk=()
  if [ "$HAVE_VLC" = 0 ]; then
    case "$PM" in
      pacman) pk+=(vlc); pacman -Si vlc-plugins-all >/dev/null 2>&1 && pk+=(vlc-plugins-all) || pk+=(vlc-plugin-ffmpeg) ;;
      *) pk+=(vlc) ;;
    esac
  fi
  [ "$HAVE_FFMPEG" = 0 ] && pk+=(ffmpeg)
  printf '%s\n' "${pk[@]}"
}

# a clean copy of the source, so pip never leaves root-owned build files in your folder
prepare_source() {
  SRC=""
  if [ -n "$SOURCE_OVERRIDE" ]; then SRC="$SOURCE_OVERRIDE"; return 0; fi
  if [ -n "$LOCAL_SRC" ]; then
    WORK="$(mktemp -d /tmp/nyrun-src.XXXXXX)" || return 1
    cp -r "$LOCAL_SRC/src" "$LOCAL_SRC/pyproject.toml" "$WORK/" || return 1
    for f in README.md LICENSE; do [ -f "$LOCAL_SRC/$f" ] && cp "$LOCAL_SRC/$f" "$WORK/"; done
    find "$WORK" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
    SRC="$WORK"
  else
    SRC="git+$REPO_URL"
  fi
}

as_user() {  # run a command as the person who ran sudo (so their files do not become root's)
  if [ "$RUN_USER" = root ]; then "$@"
  elif command -v runuser >/dev/null 2>&1; then runuser -u "$RUN_USER" -- "$@"
  else su "$RUN_USER" -s /bin/sh -c "$(printf '%q ' "$@")"
  fi
}

remove_legacy() {
  [ -n "$LEGACY" ] || return 0
  if command -v pipx >/dev/null 2>&1 && [ "$RUN_USER" != root ]; then
    as_user pipx uninstall nyrun >/dev/null 2>&1 && return 0
  fi
  rm -rf "$LEGACY"
  rm -f "$RUN_HOME/.local/bin/nyrun"
}

finish_screen() {  # finish_screen ok|err "Title" lines...
  message_screen "$@"
}

fail_screen() {  # fail_screen "what failed"
  local tail_lines=()
  mapfile -t tail_lines < <(tail -n 6 "$LOG" | sed 's/\x1b\[[0-9;]*m//g' | cut -c1-$((PANEL_W - 8)))
  local lines=("${C_TXT}$1${C_0}" "")
  local l; for l in "${tail_lines[@]}"; do lines+=("${C_DIM}${l}${C_0}"); done
  lines+=("" "${C_DIM}Full log: ${LOG}${C_0}")
  message_screen err "That did not work" "${lines[@]}"
}

do_install() {
  detect
  if [ -n "$INSTALLED_VER" ]; then
    confirm "NyRun ${INSTALLED_VER} is already installed. Reinstall it?" n "Your settings and library are not touched." || return 0
  fi
  steps_begin "Installing NyRun" "Checking this computer" "Checking VLC and ffmpeg" "Creating NyRun's own Python environment" "Installing NyRun and its parts" "Adding the nyrun command" "Tidying up"
  step_set 0 run; sleep 0.2
  if [ -z "$PY" ]; then
    step_set 0 fail "Python 3.12 or newer is needed"
    message_screen err "Python is too old or missing" "${C_TXT}NyRun needs Python 3.12 or newer.${C_0}" "" "${C_DIM}Arch:    sudo pacman -S python${C_0}" "${C_DIM}Debian:  sudo apt install python3 python3-venv${C_0}" "${C_DIM}Fedora:  sudo dnf install python3${C_0}"
    return 1
  fi
  if ! "$PY" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    step_set 0 fail "Python's venv part is missing"
    message_screen err "Python cannot make environments" "${C_TXT}Install it, then run the installer again:${C_0}" "" "${C_DIM}Debian / Ubuntu:  sudo apt install python3-venv${C_0}"
    return 1
  fi
  step_set 0 ok "$SYS_NAME · Python $PY_VER"

  # VLC and ffmpeg
  step_set 1 run
  local pk=(); mapfile -t pk < <(want_packages)
  if [ "${#pk[@]}" -eq 0 ] || [ -z "${pk[0]}" ]; then
    step_set 1 ok "VLC and ffmpeg found"
  elif [ -z "$PM" ]; then
    step_set 1 warn "install ${pk[*]} yourself"
  elif confirm "Install ${pk[*]} now?" y "VLC plays everything. ffmpeg lets NyRun save streams." "Uses: $PM"; then
    STEP_NOTE[1]="installing ${pk[*]}"
    if run_cmd 1 pm_install "${pk[@]}"; then step_set 1 ok "installed ${pk[*]}"; else step_set 1 warn "could not install ${pk[*]} (see the log)"; fi
  else
    step_set 1 warn "skipped (VLC is needed to play anything)"
  fi

  # environment
  rm -rf "$PREFIX"
  if ! run_cmd 2 "$PY" -m venv "$PREFIX"; then step_set 2 fail; fail_screen "Could not create $PREFIX."; return 1; fi
  step_set 2 ok "$PREFIX"

  # install
  prepare_source || { step_set 3 fail; fail_screen "Could not prepare the NyRun files."; return 1; }
  STEP_NOTE[3]="downloading and building, this takes a minute"
  if ! run_cmd 3 "$PREFIX/bin/pip" install --disable-pip-version-check --no-cache-dir "$SRC"; then
    step_set 3 fail "see the log"; cleanup_work; fail_screen "pip could not install NyRun. Are you online?"; return 1
  fi
  cleanup_work
  local got; got="$("$PREFIX/bin/nyrun" -version 2>/dev/null | sed -n 's/[^0-9]*\([0-9][0-9.]*\).*/\1/p' | head -1)"
  step_set 3 ok "NyRun ${got:-?}"

  # command
  mkdir -p "$BINDIR" && ln -sf "$PREFIX/bin/nyrun" "$BINDIR/nyrun" && step_set 4 ok "$BINDIR/nyrun" || { step_set 4 fail; fail_screen "Could not create $BINDIR/nyrun."; return 1; }

  # old pipx copy
  detect
  if [ -n "$LEGACY" ]; then
    if confirm "Remove the old pipx copy of NyRun?" y "Found one for ${RUN_USER}. It would run instead of the new one." "Your library and settings are not touched."; then
      remove_legacy; step_set 5 ok "removed the old pipx copy"
    else step_set 5 warn "old pipx copy kept (it may take priority)"; fi
  else step_set 5 ok; fi

  local lines=("${C_TXT}NyRun ${got:-} is ready.${C_0}" "" "${C_DIM}First time?${C_0}" "  ${C_ACC}nyrun -createfolder${C_0}   ${C_DIM}make your NyRun folder${C_0}" "  ${C_ACC}nyrun${C_0}                 ${C_DIM}open it${C_0}")
  [ "$HAVE_VLC" = 0 ] && ! command -v vlc >/dev/null 2>&1 && lines+=("" "${C_WARN}VLC is still missing - install it to play things.${C_0}")
  message_screen ok "Installed" "${lines[@]}"
}

do_update() {
  detect
  if [ -z "$INSTALLED_VER" ]; then
    message_screen info "NyRun is not installed yet" "${C_TXT}Pick Install from the menu first.${C_0}"
    return 0
  fi
  local before="$INSTALLED_VER"
  steps_begin "Updating NyRun" "Looking for the newest version" "Installing it" "Refreshing yt-dlp (the video-site engine)" "Checking the result"
  # 1: newest code
  step_set 0 run
  if [ -n "$LOCAL_SRC" ] && [ -z "$SOURCE_OVERRIDE" ]; then
    if [ -d "$LOCAL_SRC/.git" ] && command -v git >/dev/null 2>&1; then
      STEP_NOTE[0]="git pull"
      if run_cmd 0 as_user git -C "$LOCAL_SRC" pull --ff-only; then
        VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$LOCAL_SRC/pyproject.toml" | head -1)"
        step_set 0 ok "pulled the newest files · version $VERSION"
      else
        step_set 0 warn "could not pull; using the files in this folder"
      fi
    else
      step_set 0 ok "using the files in this folder · version $VERSION"
    fi
  else
    step_set 0 ok "using the newest version on GitHub"
  fi
  prepare_source || { step_set 1 fail; fail_screen "Could not prepare the NyRun files."; return 1; }
  # 2: install
  STEP_NOTE[1]="building"
  if run_cmd 1 "$PREFIX/bin/pip" install --disable-pip-version-check --no-cache-dir --upgrade --force-reinstall --no-deps "$SRC" \
     && run_cmd 1 "$PREFIX/bin/pip" install --disable-pip-version-check --no-cache-dir --upgrade "$SRC"; then
    step_set 1 ok
  else
    step_set 1 fail "see the log"; cleanup_work; fail_screen "pip could not update NyRun. Are you online?"; return 1
  fi
  cleanup_work
  # 3: yt-dlp
  if run_cmd 2 "$PREFIX/bin/pip" install --disable-pip-version-check --no-cache-dir --upgrade yt-dlp; then
    step_set 2 ok "$("$PREFIX/bin/python" -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null)"
  else step_set 2 warn "could not update yt-dlp"; fi
  mkdir -p "$BINDIR" && ln -sf "$PREFIX/bin/nyrun" "$BINDIR/nyrun"
  detect
  step_set 3 ok "now $INSTALLED_VER"
  if [ "$before" = "$INSTALLED_VER" ]; then
    message_screen ok "Already up to date" "${C_TXT}NyRun ${INSTALLED_VER} is the newest version.${C_0}" "" "${C_DIM}yt-dlp was refreshed anyway.${C_0}"
  else
    message_screen ok "Updated" "${C_TXT}NyRun ${before}  →  ${INSTALLED_VER}${C_0}"
  fi
}

do_uninstall() {
  detect
  if [ -z "$INSTALLED_VER" ] && [ -z "$LEGACY" ] && [ ! -e "$BINDIR/nyrun" ]; then
    message_screen info "NyRun is not installed" "${C_TXT}There is nothing to remove.${C_0}"
    return 0
  fi
  confirm "Uninstall NyRun ${INSTALLED_VER}?" n "Removes the nyrun command and its environment." "Your films, music and settings are NOT deleted." || return 0
  local wipe=0
  ask_user "Also delete NyRun's settings and watch history?" n "Only ~/.config/nyrun and ~/.cache/nyrun for ${RUN_USER}." "Your film library is never touched." && wipe=1
  steps_begin "Removing NyRun" "Removing the nyrun command" "Removing NyRun's environment" "Removing an old pipx copy" "Cleaning settings"
  step_set 0 run
  if [ -L "$BINDIR/nyrun" ] && [ "$(readlink "$BINDIR/nyrun")" = "$PREFIX/bin/nyrun" ]; then rm -f "$BINDIR/nyrun"; step_set 0 ok "$BINDIR/nyrun"
  elif [ -e "$BINDIR/nyrun" ]; then step_set 0 warn "$BINDIR/nyrun is not ours, left alone"
  else step_set 0 skip "not there"; fi
  step_set 1 run
  if [ -d "$PREFIX" ]; then rm -rf "$PREFIX"; step_set 1 ok "$PREFIX"; else step_set 1 skip "not there"; fi
  step_set 2 run
  if [ -n "$LEGACY" ]; then remove_legacy; step_set 2 ok "removed"; else step_set 2 skip "none found"; fi
  step_set 3 run
  if [ "$wipe" = 1 ]; then rm -rf "$RUN_HOME/.config/nyrun" "$RUN_HOME/.cache/nyrun"; step_set 3 ok "settings deleted"; else step_set 3 skip "kept"; fi
  message_screen ok "NyRun was removed" "${C_TXT}Your film library was not touched.${C_0}" "" "${C_DIM}Install it again any time with  sudo ./install.sh${C_0}"
}

# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
MENU_LABEL=("Install" "Update" "Uninstall" "Quit")
MENU_HELP=("Put NyRun on this computer" "Get the newest version, refresh yt-dlp" "Remove NyRun (library & settings stay)" "Leave the installer")

draw_menu() {
  local sel=$1 i num label help line
  begin_frame
  banner_rows
  FR_GAP
  FR_RULE
  status_rows
  FR_RULE
  FR "${C_B}${C_LAV}What would you like to do?${C_0}"
  for i in 0 1 2 3; do
    num=$((i+1)); label="${MENU_LABEL[$i]}"; help="${MENU_HELP[$i]}"
    local room=$((PANEL_W - 4 - 18)); help="$(trunc "$help" "$room")"
    if [ "$i" = "$sel" ]; then
      line="${C_SEL}${C_B}${C_ACC} ❯ ${num}  $(printf '%-11s' "$label")${C_0}${C_SEL}${C_TXT}${help}$(repeat $((room - ${#help})) ' ')${C_0}"
    else
      line="${C_DIM}   ${num}${C_0}  ${C_TXT}$(printf '%-11s' "$label")${C_0}${C_DIM}${help}${C_0}"
    fi
    FR "$line"
  done
  FR_GAP
  hint "↑↓:move" "Enter:select" "1-4:jump" "q:quit"
  draw
}

menu_loop() {
  local sel=0
  while :; do
    detect
    if [ "$TUI" = 1 ]; then
      draw_menu "$sel"
      read_key
      case "$KEY" in
        up) sel=$(( (sel + 3) % 4 )) ;;
        down) sel=$(( (sel + 1) % 4 )) ;;
        1|2|3|4) sel=$((KEY-1)); KEY="enter" ;;
        quit|esc) return 0 ;;
      esac
      if [ "$KEY" = "enter" ]; then
        case "$sel" in 0) do_install ;; 1) do_update ;; 2) do_uninstall ;; 3) return 0 ;; esac
      fi
    else
      printf '\n%s%sNyRun installer%s  %s(v%s)%s\n' "$C_B" "$C_ACC" "$C_0" "$C_DIM" "$VERSION" "$C_0"
      printf '  NyRun: %s | VLC: %s | ffmpeg: %s\n\n' "${INSTALLED_VER:-not installed}" "$([ $HAVE_VLC = 1 ] && echo found || echo missing)" "$([ $HAVE_FFMPEG = 1 ] && echo found || echo missing)"
      printf '  1) Install\n  2) Update\n  3) Uninstall\n  4) Quit\n\n'
      local a; read -r -p "Choose 1-4: " a || return 0
      case "$a" in 1) do_install ;; 2) do_update ;; 3) do_uninstall ;; 4|q|Q|"") return 0 ;; *) printf 'Please type 1, 2, 3 or 4.\n' ;; esac
    fi
  done
}

# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------
on_exit() {
  [ "${CHILD:-0}" != 0 ] && kill "$CHILD" 2>/dev/null
  cleanup_work
  tui_stop
}
trap on_exit EXIT
trap 'exit 130' INT TERM HUP
trap 'draw 2>/dev/null' WINCH

usage() {
  cat <<EOF
NyRun installer v$VERSION

  sudo ./install.sh              open the menu
  sudo ./install.sh install      install NyRun
  sudo ./install.sh update       update NyRun (and yt-dlp)
  sudo ./install.sh uninstall    remove NyRun
  Add --yes to answer every question with the default "yes".
EOF
}

ACTION=""
for arg in "$@"; do
  case "$arg" in
    install|update|uninstall) ACTION="$arg" ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help|help) usage; exit 0 ;;
    -V|--version) printf '%s\n' "$VERSION"; exit 0 ;;
    *) printf 'Unknown option: %s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
  esac
done

detect
RC=0
if [ -n "$ACTION" ]; then
  [ "$TUI" = 1 ] && tui_start
  case "$ACTION" in install) do_install; RC=$? ;; update) do_update; RC=$? ;; uninstall) do_uninstall; RC=$? ;; esac
else
  tui_start
  menu_loop
fi
tui_stop
[ "$TUI" = 1 ] && printf '\n  %sThanks for using NyRun.%s\n\n' "$C_PINK" "$C_0"
exit "$RC"
