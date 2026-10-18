"""Colour themes: built-ins, your own, and live Omarchy matching.

A *choice* is what you pick in the theme picker and what ``config.json`` stores:

    auto       Omarchy's current theme if Omarchy is installed, otherwise sakura
    omarchy    always follow Omarchy (live: switching Omarchy themes recolours NyRun)
    terminal   use your terminal's own ANSI colours
    <name>     a built-in theme, or a ``<name>.toml`` in ~/.config/nyrun/themes/
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.theme import Theme

from . import omarchy
from .config import config_dir

DEFAULT_CHOICE = "auto"
FALLBACK_THEME = "sakura"
TERMINAL_THEME = "ansi-dark"  # built into Textual: uses the terminal's own palette


# --------------------------------------------------------------------------- #
# Colour maths
# --------------------------------------------------------------------------- #

def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(r: float, g: float, b: float) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(v))) for v in (r, g, b)))


def mix(a: str, b: str, t: float) -> str:
    """Blend colour a towards colour b by fraction t (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(*(x + (y - x) * t for x, y in zip(ra, rb)))


def luminance(h: str) -> float:
    def chan(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(color: str, bg: str, fg: str, ratio: float = 3.0) -> str:
    """Nudge `color` towards the foreground until it is readable on `bg`."""
    for _ in range(12):
        if contrast(color, bg) >= ratio:
            return color
        color = mix(color, fg, 0.18)
    return color


def _distance(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_rgb(a), _rgb(b))) ** 0.5


# --------------------------------------------------------------------------- #
# Palettes
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Palette:
    name: str
    dark: bool
    background: str
    surface: str
    panel: str
    foreground: str
    primary: str
    secondary: str
    accent: str
    success: str
    warning: str
    error: str

    def to_theme(self, name: str | None = None) -> Theme:
        return Theme(
            name=name or self.name,
            primary=self.primary,
            secondary=self.secondary,
            accent=self.accent,
            foreground=self.foreground,
            background=self.background,
            surface=self.surface,
            panel=self.panel,
            success=self.success,
            warning=self.warning,
            error=self.error,
            dark=self.dark,
        )

    @property
    def swatch(self) -> list[str]:
        return [self.background, self.panel, self.primary, self.secondary, self.accent,
                self.success, self.warning, self.error]


def _p(name, dark, bg, surface, panel, fg, primary, secondary, accent, success, warning, error) -> Palette:
    return Palette(name, dark, bg, surface, panel, fg, primary, secondary, accent, success, warning, error)


BUILTIN: dict[str, Palette] = {
    p.name: p
    for p in [
        _p("sakura", True, "#1a1320", "#251a2e", "#3a2a47", "#f3e5f5", "#f48fb1", "#ce93d8", "#ffb7d5", "#a5d6a7", "#ffcc80", "#ef9a9a"),
        _p("midnight", True, "#0f111a", "#171a27", "#292d3e", "#d6deeb", "#82aaff", "#c792ea", "#89ddff", "#c3e88d", "#ffcb6b", "#f07178"),
        _p("matcha", True, "#14191a", "#1c2425", "#2e3b3c", "#e5eae0", "#a3be8c", "#88c0d0", "#ebcb8b", "#a3be8c", "#ebcb8b", "#bf616a"),
        _p("mono", True, "#0d0d0d", "#171717", "#2a2a2a", "#eeeeee", "#e0e0e0", "#bdbdbd", "#ffffff", "#cfcfcf", "#d0d0d0", "#ffffff"),
        _p("catppuccin", True, "#1e1e2e", "#181825", "#313244", "#cdd6f4", "#cba6f7", "#89b4fa", "#f5c2e7", "#a6e3a1", "#f9e2af", "#f38ba8"),
        _p("catppuccin-latte", False, "#eff1f5", "#e6e9ef", "#ccd0da", "#4c4f69", "#1e66f5", "#ea76cb", "#8839ef", "#2f8a1f", "#b86e00", "#d20f39"),
        _p("tokyo-night", True, "#1a1b26", "#16161e", "#292e42", "#c0caf5", "#7aa2f7", "#bb9af7", "#7dcfff", "#9ece6a", "#e0af68", "#f7768e"),
        _p("gruvbox", True, "#282828", "#32302f", "#504945", "#ebdbb2", "#fabd2f", "#d3869b", "#fe8019", "#b8bb26", "#fabd2f", "#fb4934"),
        _p("nord", True, "#2e3440", "#3b4252", "#434c5e", "#d8dee9", "#88c0d0", "#b48ead", "#81a1c1", "#a3be8c", "#ebcb8b", "#bf616a"),
        _p("rose-pine", True, "#191724", "#1f1d2e", "#26233a", "#e0def4", "#c4a7e7", "#9ccfd8", "#ebbcba", "#9ccfd8", "#f6c177", "#eb6f92"),
        _p("dracula", True, "#282a36", "#21222c", "#44475a", "#f8f8f2", "#bd93f9", "#ff79c6", "#8be9fd", "#50fa7b", "#f1fa8c", "#ff5555"),
        _p("kanagawa", True, "#1f1f28", "#2a2a37", "#363646", "#dcd7ba", "#7e9cd8", "#957fb8", "#d27e99", "#98bb6c", "#e6c384", "#e46876"),
        _p("everforest", True, "#2d353b", "#343f44", "#3d484d", "#d3c6aa", "#a7c080", "#7fbbb3", "#e69875", "#a7c080", "#dbbc7f", "#e67e80"),
        _p("one-dark", True, "#282c34", "#21252b", "#2c313a", "#abb2bf", "#61afef", "#c678dd", "#56b6c2", "#98c379", "#e5c07b", "#e06c75"),
        _p("solarized-dark", True, "#002b36", "#073642", "#08394a", "#93a1a1", "#268bd2", "#6c71c4", "#2aa198", "#859900", "#b58900", "#dc322f"),
        _p("flexoki-light", False, "#fffcf0", "#f2f0e5", "#e6e4d9", "#100f0f", "#205ea6", "#5e409d", "#bc5215", "#66800b", "#ad8301", "#af3029"),
    ]
}


def palette_from_colors(name: str, raw: omarchy.RawColors) -> Palette:
    """Turn Omarchy-style colours (colors.toml and friends) into a NyRun palette."""
    v = raw.values

    def get(*keys: str) -> str | None:
        for k in keys:
            if k in v:
                return v[k]
        return None

    bg = get("background", "bg") or "#101010"
    fg = get("foreground", "fg") or "#e0e0e0"
    dark = (not raw.light) if raw.light is not None else luminance(bg) < 0.4
    # a "light" theme must have a dark foreground; guard against odd files
    if dark and luminance(fg) < luminance(bg):
        fg = mix(bg, "#ffffff", 0.85)
    if not dark and luminance(fg) > luminance(bg):
        fg = mix(bg, "#000000", 0.85)

    # surfaces: a gentle step away from the background, in the foreground's direction
    surface = mix(bg, fg, 0.06)
    lighter = get("lighter_background", "lighter_bg")
    if lighter and 0 < _distance(lighter, bg) < 70:
        surface = lighter
    panel = mix(bg, fg, 0.16)

    accent = get("accent") or get("color4", "blue", "color12") or fg
    accent = ensure_contrast(accent, bg, fg)
    primary = accent

    def semantic(names: tuple[str, ...], fallback: str) -> str:
        return ensure_contrast(get(*names) or fallback, bg, fg)

    red = semantic(("red", "color1", "color9"), "#e06c75")
    green = semantic(("green", "color2", "color10"), "#98c379")
    yellow = semantic(("yellow", "color3", "color11"), "#e5c07b")

    secondary = None
    for key in ("color5", "color13", "color6", "color14", "blue", "color4", "color12"):
        cand = get(key)
        if cand and contrast(cand, bg) >= 2.5 and _distance(cand, accent) > 60:
            secondary = cand
            break
    if secondary is None:
        secondary = mix(accent, fg, 0.4)

    return Palette(name, dark, bg, surface, panel, fg, primary, ensure_contrast(secondary, bg, fg, 2.5),
                   accent, green, yellow, red)


# --------------------------------------------------------------------------- #
# User themes + catalog
# --------------------------------------------------------------------------- #

def user_theme_dir() -> Path:
    return config_dir() / "themes"


def load_user_themes() -> dict[str, Palette]:
    """Any ``*.toml`` in the themes folder, in Omarchy's colors.toml format."""
    out: dict[str, Palette] = {}
    folder = user_theme_dir()
    try:
        files = sorted(folder.glob("*.toml"))
    except OSError:
        return out
    for f in files:
        raw = omarchy._from_colors_toml(f)
        if raw is None:
            raw = omarchy._from_alacritty(f)
        if raw:
            out[f.stem] = palette_from_colors(f.stem, raw)
    return out


def omarchy_palette() -> Palette | None:
    raw = omarchy.current_colors()
    if raw is None:
        return None
    return palette_from_colors("omarchy", raw)


@dataclass(frozen=True)
class Entry:
    """One row in the theme picker."""

    choice: str
    label: str
    note: str
    palette: Palette | None  # None for the terminal-colours entry


def catalog() -> list[Entry]:
    entries: list[Entry] = []
    om = omarchy_palette()
    om_name = omarchy.theme_name()
    if om:
        entries.append(Entry("auto", "Auto", f"follows Omarchy ({om_name})", om))
        entries.append(Entry("omarchy", f"Omarchy: {om_name}", "live - recolours when you switch Omarchy themes", om))
    else:
        entries.append(Entry("auto", "Auto", f"uses {FALLBACK_THEME} (Omarchy not found)", BUILTIN[FALLBACK_THEME]))
    entries.append(Entry("terminal", "Terminal colours", "your terminal's own palette (ANSI)", None))
    for name, pal in BUILTIN.items():
        entries.append(Entry(name, name, "light" if not pal.dark else "built-in", pal))
    for name, pal in load_user_themes().items():
        if name not in BUILTIN:
            entries.append(Entry(name, name, "yours", pal))
    return entries


def choice_names() -> list[str]:
    return [e.choice for e in catalog()]


@dataclass(frozen=True)
class Resolved:
    """What a choice currently means on this machine."""

    palette: Palette | None  # None => terminal ANSI theme
    follows_omarchy: bool
    label: str


def resolve(choice: str) -> Resolved:
    choice = (choice or DEFAULT_CHOICE).strip()
    if choice in ("auto", "omarchy"):
        pal = omarchy_palette()
        if pal:
            return Resolved(pal, True, f"Omarchy: {omarchy.theme_name()}")
        return Resolved(BUILTIN[FALLBACK_THEME], False, FALLBACK_THEME)
    if choice == "terminal":
        return Resolved(None, False, "terminal colours")
    user = load_user_themes()
    if choice in user:  # your own file wins over a built-in of the same name
        return Resolved(user[choice], False, choice)
    if choice in BUILTIN:
        return Resolved(BUILTIN[choice], False, choice)
    return Resolved(BUILTIN[FALLBACK_THEME], False, FALLBACK_THEME)


# kept for older imports
THEMES = {name: pal.to_theme() for name, pal in BUILTIN.items()}
