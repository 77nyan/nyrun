"""Reading the active Omarchy theme.

Omarchy keeps the active theme in a folder and the name of the theme next to it:

    ~/.local/state/omarchy/current/theme/        (newer Omarchy)
    ~/.local/state/omarchy/current/theme.name
    ~/.config/omarchy/current/theme/             (older Omarchy)

Inside the theme folder the colours live in ``colors.toml`` (key names differ a
little between Omarchy versions, so several spellings are understood). Older or
hand-made themes only have terminal configs, so ``alacritty.toml`` and
``kitty.conf`` are used as fallbacks.

Override the location with the NYRUN_OMARCHY_DIR environment variable
(point it at the ``current`` folder).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - python 3.10
    import tomli as tomllib

_HEX = re.compile(r"^(?:#|0x)?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")

_ANSI_NAMES = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]


def parse_hex(value: object) -> str | None:
    """Normalise '#RRGGBB', '0xRRGGBB', 'RRGGBB' or '#RGB' to '#rrggbb'."""
    if not isinstance(value, str):
        return None
    m = _HEX.match(value.strip().strip("\"'"))
    if not m:
        return None
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


@dataclass
class RawColors:
    """Colours as found on disk, before they are turned into a NyRun palette."""

    values: dict[str, str] = field(default_factory=dict)
    light: bool | None = None  # explicit light/dark marker, None = unknown
    source: str = ""


def _candidate_roots() -> list[Path]:
    env = os.environ.get("NYRUN_OMARCHY_DIR")
    if env:
        return [Path(env).expanduser()]
    home = Path.home()
    state = Path(os.environ.get("XDG_STATE_HOME", home / ".local" / "state"))
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [state / "omarchy" / "current", config / "omarchy" / "current"]


def current_root() -> Path | None:
    """The ``.../omarchy/current`` folder, if Omarchy is installed."""
    for root in _candidate_roots():
        try:
            if (root / "theme").is_dir():
                return root
        except OSError:
            continue
    return None


def detected() -> bool:
    return current_root() is not None


def theme_name() -> str | None:
    root = current_root()
    if not root:
        return None
    try:
        name = (root / "theme.name").read_text(encoding="utf-8").strip()
        if name:
            return name
    except OSError:
        pass
    try:
        return (root / "theme").resolve().name
    except OSError:
        return None


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _from_colors_toml(path: Path) -> RawColors | None:
    data = _read_toml(path)
    if not data:
        return None
    values: dict[str, str] = {}
    for key, val in data.items():
        hx = parse_hex(val)
        if hx:
            values[key.lower()] = hx
    light = None
    mode = data.get("mode")
    if isinstance(mode, str):
        light = mode.strip().lower() == "light"
    if "background" not in values and "bg" not in values:
        return None
    return RawColors(values, light, "colors.toml")


def _from_alacritty(path: Path) -> RawColors | None:
    colors = _read_toml(path).get("colors")
    if not isinstance(colors, dict):
        return None
    values: dict[str, str] = {}
    primary = colors.get("primary", {})
    for key in ("background", "foreground"):
        hx = parse_hex(primary.get(key)) if isinstance(primary, dict) else None
        if hx:
            values[key] = hx
    for group, offset in (("normal", 0), ("bright", 8)):
        table = colors.get(group, {})
        if not isinstance(table, dict):
            continue
        for i, name in enumerate(_ANSI_NAMES):
            hx = parse_hex(table.get(name))
            if hx:
                values[f"color{offset + i}"] = hx
    cursor = colors.get("cursor", {})
    if isinstance(cursor, dict):
        hx = parse_hex(cursor.get("cursor"))
        if hx:
            values["cursor"] = hx
    if "background" not in values:
        return None
    return RawColors(values, None, "alacritty.toml")


_KITTY_LINE = re.compile(r"^\s*(\w+)\s+(#[0-9a-fA-F]{6}|0x[0-9a-fA-F]{6})\s*(?:#.*)?$")


def _from_kitty(path: Path) -> RawColors | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, str] = {}
    for line in text.splitlines():
        m = _KITTY_LINE.match(line)
        if not m:
            continue
        key, hx = m.group(1), parse_hex(m.group(2))
        if hx and (key in ("background", "foreground", "cursor") or re.fullmatch(r"color\d{1,2}", key)):
            values[key] = hx
    return RawColors(values, None, "kitty.conf") if "background" in values else None


def read_colors(theme_dir: Path) -> RawColors | None:
    """Read colours from a theme folder (colors.toml, then terminal configs)."""
    raw: RawColors | None = None
    for fname, reader in (
        ("colors.toml", _from_colors_toml),
        ("alacritty.toml", _from_alacritty),
        ("kitty.conf", _from_kitty),
    ):
        p = theme_dir / fname
        if p.is_file():
            raw = reader(p)
            if raw:
                break
    if raw is None:
        return None
    if raw.light is None and (theme_dir / "light.mode").exists():
        raw.light = True
    return raw


def current_colors() -> RawColors | None:
    root = current_root()
    return read_colors(root / "theme") if root else None


def signature() -> tuple | None:
    """Changes whenever the user switches theme (or edits the theme's colours)."""
    root = current_root()
    if not root:
        return None
    theme = root / "theme"
    try:
        real = str(theme.resolve())
    except OSError:
        real = str(theme)
    mt = []
    for fname in ("colors.toml", "alacritty.toml", "kitty.conf", "light.mode"):
        try:
            mt.append((fname, (theme / fname).stat().st_mtime_ns))
        except OSError:
            continue
    try:
        name = (root / "theme.name").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    return (real, name, tuple(mt))
