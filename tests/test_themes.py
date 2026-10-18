import json
import os
import time
from pathlib import Path

import pytest

from nyrun import omarchy, themes
from nyrun.config import Settings

NEW_STYLE = """\
mode = "dark"
accent = "#7aa2f7"
background = "#1a1b26"
foreground = "#c0caf5"
lighter_background = "#24283b"
red = "#f7768e"
green = "#9ece6a"
yellow = "#e0af68"
blue = "#7aa2f7"
"""

OLD_STYLE = """\
accent = "#FF40A3"
cursor = "#F0F8FF"
foreground = "#F0F8FF"
background = "#0A1428"
selection_foreground = "#0A1428"
selection_background = "#F0F8FF"
color0 = "#0A1428"
color1 = "#FF40A3"
color2 = "#00BFFF"
color3 = "#5076B2"
color4 = "#F0F8FF"
color5 = "#FF40A3"
color6 = "#F0F8FF"
color7 = "#F0F8FF"
color8 = "#F0F8FF"
color9 = "#FF40A3"
color10 = "#00BFFF"
color11 = "#6A90D2"
color12 = "#00BFFF"
color13 = "#FF40A3"
color14 = "#00BFFF"
color15 = "#00BFFF"
"""

ALACRITTY = """\
[colors.primary]
background = "#1e1e2e"
foreground = "#cdd6f4"
[colors.cursor]
cursor = "#f5e0dc"
[colors.normal]
black = "#45475a"
red = "#f38ba8"
green = "#a6e3a1"
yellow = "#f9e2af"
blue = "#89b4fa"
magenta = "#f5c2e7"
cyan = "#94e2d5"
white = "#bac2de"
[colors.bright]
black = "#585b70"
red = "#f38ba8"
green = "#a6e3a1"
yellow = "#f9e2af"
blue = "#89b4fa"
magenta = "#f5c2e7"
cyan = "#94e2d5"
white = "#a6adc8"
"""

KITTY = """\
# comment
foreground #c0caf5
background #1a1b26
cursor #c0caf5
color0 #15161e
color1 #f7768e
color4 #7aa2f7
"""


@pytest.fixture
def omarchy_dir(tmp_path, monkeypatch):
    """A fake ~/.local/state/omarchy/current with an active theme."""
    cur = tmp_path / "state" / "omarchy" / "current"
    (cur / "theme").mkdir(parents=True)
    (cur / "theme.name").write_text("tokyo-night\n")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return cur


def test_no_omarchy_means_fallback():
    assert not omarchy.detected()
    assert omarchy.current_colors() is None
    res = themes.resolve("auto")
    assert res.palette.name == themes.FALLBACK_THEME and not res.follows_omarchy
    # "omarchy" without Omarchy installed must not crash either
    assert themes.resolve("omarchy").palette.name == themes.FALLBACK_THEME


def test_detect_and_name(omarchy_dir):
    assert omarchy.detected() and omarchy.theme_name() == "tokyo-night"


def test_older_config_location(tmp_path, monkeypatch):
    cur = tmp_path / "cfg" / "omarchy" / "current"
    (cur / "theme").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "nothing"))
    assert omarchy.detected()
    assert omarchy.theme_name() == "theme"  # no theme.name -> folder name


def test_new_style_colors(omarchy_dir):
    (omarchy_dir / "theme" / "colors.toml").write_text(NEW_STYLE)
    res = themes.resolve("auto")
    p = res.palette
    assert res.follows_omarchy and p.dark
    assert p.background == "#1a1b26" and p.foreground == "#c0caf5"
    assert p.accent == "#7aa2f7" and p.primary == "#7aa2f7"
    assert p.surface == "#24283b"  # lighter_background honoured
    assert p.error == "#f7768e" and p.success == "#9ece6a"


def test_old_style_colors_and_readable_accent(omarchy_dir):
    (omarchy_dir / "theme" / "colors.toml").write_text(OLD_STYLE)
    p = themes.resolve("omarchy").palette
    assert p.background == "#0a1428" and p.accent == "#ff40a3"
    # every accent colour must be readable on the background
    for col in (p.primary, p.secondary, p.accent, p.success, p.warning, p.error):
        assert themes.contrast(col, p.background) >= 2.4
    # a theme whose selection colour equals the text colour must not make panels unreadable
    assert themes.contrast(p.foreground, p.panel) >= 4


def test_alias_keys_bg_fg(omarchy_dir):
    (omarchy_dir / "theme" / "colors.toml").write_text('bg = "#101010"\nfg = "#eeeeee"\naccent = "#ff8800"\n')
    p = themes.resolve("auto").palette
    assert p.background == "#101010" and p.foreground == "#eeeeee"


def test_alacritty_fallback(omarchy_dir):
    (omarchy_dir / "theme" / "alacritty.toml").write_text(ALACRITTY)
    p = themes.resolve("auto").palette
    assert p.background == "#1e1e2e" and p.foreground == "#cdd6f4"
    assert p.error == "#f38ba8"


def test_kitty_fallback(omarchy_dir):
    (omarchy_dir / "theme" / "kitty.conf").write_text(KITTY)
    raw = omarchy.current_colors()
    assert raw.source == "kitty.conf" and raw.values["color1"] == "#f7768e"


def test_colors_toml_beats_terminal_configs(omarchy_dir):
    (omarchy_dir / "theme" / "alacritty.toml").write_text(ALACRITTY)
    (omarchy_dir / "theme" / "colors.toml").write_text(NEW_STYLE)
    assert omarchy.current_colors().source == "colors.toml"


def test_light_theme_detection(omarchy_dir):
    light = 'background = "#fbf1c7"\nforeground = "#3c3836"\naccent = "#af3a03"\n'
    (omarchy_dir / "theme" / "colors.toml").write_text(light)
    assert themes.resolve("auto").palette.dark is False
    # explicit marker file / mode key wins
    (omarchy_dir / "theme" / "colors.toml").write_text('mode = "light"\n' + NEW_STYLE.replace('mode = "dark"\n', ""))
    assert themes.resolve("auto").palette.dark is False
    (omarchy_dir / "theme" / "colors.toml").write_text(NEW_STYLE)
    assert themes.resolve("auto").palette.dark is True
    (omarchy_dir / "theme" / "light.mode").write_text("")
    assert themes.resolve("auto").palette.dark is True  # mode = "dark" is explicit; marker only fills unknowns


def test_garbage_files_do_not_crash(omarchy_dir):
    (omarchy_dir / "theme" / "colors.toml").write_text("this is [not toml")
    assert omarchy.current_colors() is None
    assert themes.resolve("auto").palette.name == themes.FALLBACK_THEME
    (omarchy_dir / "theme" / "colors.toml").write_text('background = "nonsense"\nforeground = 5\n')
    assert omarchy.current_colors() is None


def test_signature_changes_on_theme_switch(omarchy_dir):
    (omarchy_dir / "theme" / "colors.toml").write_text(NEW_STYLE)
    before = omarchy.signature()
    assert omarchy.signature() == before
    time.sleep(0.01)
    (omarchy_dir / "theme.name").write_text("catppuccin\n")
    assert omarchy.signature() != before
    mid = omarchy.signature()
    (omarchy_dir / "theme" / "colors.toml").write_text(OLD_STYLE)
    assert omarchy.signature() != mid


def test_parse_hex_variants():
    assert omarchy.parse_hex("#ABC") == "#aabbcc"
    assert omarchy.parse_hex("0xFF8800") == "#ff8800"
    assert omarchy.parse_hex('"#112233"') == "#112233"
    assert omarchy.parse_hex("red") is None and omarchy.parse_hex(12) is None


def test_builtin_palettes_are_all_usable():
    assert len(themes.BUILTIN) >= 12
    for name, p in themes.BUILTIN.items():
        assert themes.contrast(p.foreground, p.background) >= 5.5, name
        assert themes.contrast(p.accent, p.background) >= 3, name
        assert themes.contrast(p.foreground, p.panel) >= 4.5, name
        p.to_theme()  # constructs without error


def test_user_theme_file(tmp_path):
    d = themes.user_theme_dir()
    d.mkdir(parents=True)
    (d / "mine.toml").write_text(NEW_STYLE)
    (d / "broken.toml").write_text("nope [")
    names = themes.choice_names()
    assert "mine" in names and "broken" not in names
    assert themes.resolve("mine").palette.accent == "#7aa2f7"


def test_user_theme_overrides_builtin_name(tmp_path):
    d = themes.user_theme_dir()
    d.mkdir(parents=True)
    (d / "nord.toml").write_text(NEW_STYLE)
    assert themes.resolve("nord").palette.accent == "#7aa2f7"


def test_unknown_choice_falls_back():
    assert themes.resolve("does-not-exist").palette.name == themes.FALLBACK_THEME


def test_catalog_shape(omarchy_dir):
    (omarchy_dir / "theme" / "colors.toml").write_text(NEW_STYLE)
    names = themes.choice_names()
    assert names[:3] == ["auto", "omarchy", "terminal"] and "sakura" in names


def test_cli_installs_theme_file(tmp_path, omarchy_dir):
    from nyrun import cli

    src = tmp_path / "cool-theme" / "colors.toml"
    src.parent.mkdir()
    src.write_text(OLD_STYLE)
    assert cli.main(["-theme", str(src)]) == 0
    assert Settings.load().theme == "cool-theme"
    assert (themes.user_theme_dir() / "cool-theme.toml").exists()
    bad = tmp_path / "bad.toml"
    bad.write_text("x = 1")
    assert cli.main(["-theme", str(bad)]) == 1
    assert not (themes.user_theme_dir() / "bad.toml").exists()


def test_cli_theme_list(capsys, omarchy_dir):
    from nyrun import cli

    assert cli.main(["-theme", "list"]) == 0
    out = capsys.readouterr().out
    assert "tokyo-night" in out and "Omarchy detected" in out
