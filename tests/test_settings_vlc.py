"""VLC compatibility options: command building, presets, failure reporting, the settings screen."""

import asyncio
import os
import stat
from pathlib import Path

import pytest

from nyrun import player
from nyrun.app import NyRunApp, ScreenApp
from nyrun.config import Settings, State
from nyrun.settings_screens import PRESETS, SettingsScreen, ThemePickerScreen

SIZE = (150, 50)


def run(coro):
    return asyncio.run(coro)


# ------------------------------ command building ----------------------------

def test_defaults_are_minimal():
    assert player.vlc_args(Settings()) == ["--fullscreen", "--play-and-exit"]
    assert player.vlc_env(Settings()) == {}


def test_every_option_maps_to_a_flag():
    s = Settings(
        fullscreen=False, vlc_play_and_exit=False, vlc_loop=True, vlc_start_paused=True, vlc_on_top=True,
        vlc_title_osd=False, vlc_quiet=True, vlc_hwdec="none", vlc_vout="gl", vlc_aout="pulse",
        vlc_one_instance="off", vlc_sub_lang="en", vlc_audio_lang="ja", vlc_sub_autodetect=False,
        vlc_continue="always", vlc_rate="1.25", extra_vlc_args=["--deinterlace=1"],
    )
    args = player.vlc_args(s)
    for flag in ("--loop", "--start-paused", "--video-on-top", "--no-video-title-show", "--no-qt-privacy-ask",
                 "--no-qt-recentplay", "--avcodec-hw=none", "--vout=gl", "--aout=pulse", "--no-one-instance",
                 "--sub-language=en", "--audio-language=ja", "--no-sub-autodetect-file", "--qt-continue=2",
                 "--rate=1.25", "--deinterlace=1"):
        assert flag in args, flag
    assert "--fullscreen" not in args and "--play-and-exit" not in args


@pytest.mark.parametrize("value,expected", [("never", "--qt-continue=0"), ("ask", "--qt-continue=1"), ("always", "--qt-continue=2")])
def test_continue_values(value, expected):
    assert expected in player.vlc_args(Settings(vlc_continue=value))


def test_bad_rate_is_ignored_not_passed():
    for bad in ("fast", "0", "100", "-1"):
        assert not any(a.startswith("--rate") for a in player.vlc_args(Settings(vlc_rate=bad)))
    assert not any(a.startswith("--rate") for a in player.vlc_args(Settings(vlc_rate="1.0")))


def test_one_instance_on():
    assert "--one-instance" in player.vlc_args(Settings(vlc_one_instance="on"))


def test_qt_platform_env():
    assert player.vlc_env(Settings(vlc_qt_platform="xcb")) == {"QT_QPA_PLATFORM": "xcb"}
    assert player.vlc_env(Settings(vlc_qt_platform="wayland")) == {"QT_QPA_PLATFORM": "wayland"}
    assert player.vlc_env(Settings(vlc_qt_platform="bogus")) == {}


def test_flatpak_gets_env_through_flag():
    cmd = player.build_command([Path("/x.mkv")], ["flatpak", "run", "org.videolan.VLC"], Settings(vlc_qt_platform="xcb"))
    assert cmd[:4] == ["flatpak", "run", "--env=QT_QPA_PLATFORM=xcb", "org.videolan.VLC"]
    assert cmd[-2:] == ["--", "/x.mkv"]


def test_preview_command_text(monkeypatch):
    monkeypatch.setenv("NYRUN_VLC", "/bin/sh")
    text = player.preview_command(Settings(vlc_qt_platform="xcb", vlc_hwdec="none", vlc_sub_lang="en"))
    assert text.startswith("QT_QPA_PLATFORM=xcb ") and "--avcodec-hw=none" in text and text.endswith("<your file>")


def test_forced_flatpak_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *_: None)
    with pytest.raises(player.VLCNotFound, match="Flatpak"):
        player.find_vlc("", "flatpak")


def test_native_launcher_skips_flatpak(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda *_: None)
    monkeypatch.setattr(player, "_flatpak_vlc", lambda: ["flatpak", "run", "org.videolan.VLC"])
    monkeypatch.setenv("PATH", "")
    with pytest.raises(player.VLCNotFound):
        player.find_vlc("", "native")
    assert player.find_vlc("", "auto")[0] == "flatpak"


def test_env_reaches_vlc_process(tmp_path, monkeypatch):
    log = tmp_path / "env.log"
    script = tmp_path / "vlc"
    script.write_text(f'#!/bin/sh\necho "QT=$QT_QPA_PLATFORM args=$*" >> "{log}"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    f = tmp_path / "a.mkv"
    f.write_bytes(b"0")
    player.launch([f], Settings(vlc_qt_platform="xcb", vlc_hwdec="none")).wait(timeout=5)
    out = log.read_text()
    assert "QT=xcb" in out and "--avcodec-hw=none" in out


def test_version_and_failure_log(tmp_path, monkeypatch):
    script = tmp_path / "vlc"
    script.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "VLC media player 3.0.21 Vetinari"; exit 0; fi\n'
                      'echo "vlc: unknown option or missing mandatory argument" >&2\nexit 1\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    assert player.version(Settings()).startswith("VLC media player 3.0.21")
    f = tmp_path / "a.mkv"
    f.write_bytes(b"0")
    assert player.launch([f], Settings()).wait(timeout=5) == 1
    assert "unknown option" in player.last_error_lines()


# ---------------------------------- TUI -------------------------------------

def make_app(library, **kw):
    return NyRunApp(Settings(default_folder=str(library), **kw), State())


def test_failed_vlc_is_reported_to_the_user(library, tmp_path, monkeypatch):
    script = tmp_path / "vlc"
    script.write_text('#!/bin/sh\necho "vlc: bad option --nope" >&2\nexit 1\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    seen = []

    async def go():
        app = make_app(library)
        orig = app.notify
        app.notify = lambda msg, **kw: (seen.append((str(msg), kw.get("severity"))), orig(msg, **kw))[1]
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("1", "enter")
            for _ in range(60):
                await pilot.pause(0.1)
                if any(sev == "error" for _, sev in seen):
                    break

    run(go())
    errors = [m for m, sev in seen if sev == "error"]
    assert errors and "code 1" in errors[0] and "bad option --nope" in errors[0]


def test_settings_screen_changes_are_saved_and_used(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("comma")
            await pilot.pause(0.5)
            scr = app.screen
            assert isinstance(scr, SettingsScreen)
            # toggle a switch, edit text fields, change a select - all through the real widgets
            scr.query_one("#f-vlc_loop").value = True
            scr.query_one("#f-vlc_sub_lang").value = "pl,en"
            scr.query_one("#f-extra_vlc_args").value = '--deinterlace=1 --video-title="a b"'
            scr.query_one("#f-vlc_hwdec").value = "none"
            await pilot.pause(0.4)
            assert "--loop" in scr.query_one("#st-preview").render().plain if hasattr(scr.query_one("#st-preview").render(), "plain") else True
            await pilot.press("escape")
            await pilot.pause(0.3)
            await pilot.press("1", "enter")
            await pilot.pause(0.5)

    run(go())
    line = fake_vlc.read_text().splitlines()[0]
    for part in ("--loop", "--sub-language=pl,en", "--avcodec-hw=none", "--deinterlace=1", "--video-title=a b"):
        assert part in line
    saved = Settings.load()
    assert saved.vlc_loop and saved.vlc_sub_lang == "pl,en" and saved.extra_vlc_args == ["--deinterlace=1", "--video-title=a b"]


def test_presets_apply_and_reset(library):
    async def go():
        app = make_app(library, vlc_vout="gl", vlc_title_osd=True)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("comma")
            await pilot.pause(0.5)
            scr = app.screen
            scr._apply_preset("wayland")
            assert app.settings.vlc_qt_platform == "xcb" and app.settings.vlc_vout == ""  # preset resets compat fields first
            scr._apply_preset("quiet")
            assert app.settings.vlc_qt_platform == "auto" and app.settings.vlc_quiet and not app.settings.vlc_title_osd
            scr._apply_preset("safe")
            assert app.settings.vlc_hwdec == "none" and app.settings.vlc_title_osd
            await pilot.pause(0.3)
            # widgets follow the settings
            assert scr.query_one("#f-vlc_hwdec").value == "none"
            scr._apply_preset("default")
            assert app.settings.vlc_hwdec == "auto"

    run(go())
    assert Settings.load().vlc_hwdec == "auto"
    assert set(PRESETS) == {"default", "wayland", "safe", "quiet"}


def test_reset_button_restores_defaults(library):
    async def go():
        app = make_app(library, vlc_loop=True, vlc_hwdec="none", vlc_sub_lang="en", extra_vlc_args=["--x"], fullscreen=False)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("comma")
            await pilot.pause(0.5)
            app.screen.query_one("#st-reset").press()
            await pilot.pause(0.4)
            s = app.settings
            assert not s.vlc_loop and s.vlc_hwdec == "auto" and s.vlc_sub_lang == "" and s.extra_vlc_args == [] and s.fullscreen

    run(go())


def test_settings_warnings(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("comma")
            await pilot.pause(0.4)
            scr = app.screen
            scr.query_one("#f-vlc_path").value = "/nonexistent/vlc"
            scr.query_one("#f-vlc_rate").value = "fast"
            await pilot.pause(0.3)
            warns = scr._warnings()
            assert any("does not exist" in w for w in warns) and any("speed" in w for w in warns)

    run(go())


def test_test_button_reports_version(library, tmp_path, monkeypatch):
    script = tmp_path / "vlc"
    script.write_text('#!/bin/sh\necho "VLC media player 3.0.21 Vetinari"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    seen = []

    async def go():
        app = make_app(library)
        orig = app.notify
        app.notify = lambda msg, **kw: (seen.append(str(msg)), orig(msg, **kw))[1]
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("comma")
            await pilot.pause(0.4)
            app.screen.query_one("#st-test").press()
            for _ in range(50):
                await pilot.pause(0.1)
                if any("3.0.21" in m for m in seen):
                    break

    run(go())
    assert any(m.startswith("OK: VLC media player 3.0.21") for m in seen)


def test_settings_reachable_from_home_tile_and_s_key(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("s")
            await pilot.pause(0.4)
            assert isinstance(app.screen, SettingsScreen)
            await pilot.press("escape")
            await pilot.pause(0.2)
            await pilot.press("1")  # library
            await pilot.pause(0.4)
            await pilot.press("comma")  # global key works there too
            await pilot.pause(0.4)
            assert isinstance(app.screen, SettingsScreen)

    run(go())


# --------------------------- theme picker + live follow ---------------------

def test_theme_picker_previews_saves_and_cancels(library):
    async def go():
        app = make_app(library, theme="midnight")
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            assert app.theme == "midnight"
            await pilot.press("T")
            await pilot.pause(0.5)
            assert isinstance(app.screen, ThemePickerScreen)
            names = [e for e in app.screen._entries]
            await pilot.press("down")  # next theme previews live
            await pilot.pause(0.3)
            assert app.theme != "midnight"
            await pilot.press("escape")  # cancel restores
            await pilot.pause(0.3)
            assert app.theme == "midnight" and app.settings.theme == "midnight"
            assert not app.theme_locked
            await pilot.press("T")
            await pilot.pause(0.4)
            lst = app.screen.query_one("#tp-list")
            lst.highlighted = names.index("nord")
            await pilot.pause(0.3)
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert app.theme == "nord" and app.settings.theme == "nord"

    run(go())
    assert Settings.load().theme == "nord"


def test_standalone_theme_picker_exits_after_choice(library):
    async def go():
        app = ScreenApp(Settings(), ThemePickerScreen)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.5)
            assert isinstance(app.screen, ThemePickerScreen)
            await pilot.press("down", "down", "enter")
            await pilot.pause(0.5)
        return app

    app = run(go())
    assert Settings.load().theme != "auto"


def test_terminal_theme_selectable(library):
    async def go():
        app = make_app(library, theme="terminal")
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.4)
            assert app.theme == "ansi-dark"
            await pilot.press("1")
            await pilot.pause(0.4)  # screens must still render with ANSI colours

    run(go())


def test_app_follows_omarchy_live(library, tmp_path, monkeypatch):
    state = tmp_path / "state"
    cur = state / "omarchy" / "current"
    (cur / "theme").mkdir(parents=True)
    (cur / "theme.name").write_text("one\n")
    (cur / "theme" / "colors.toml").write_text('background="#101820"\nforeground="#e0e0e0"\naccent="#ff8800"\n')
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    async def go():
        NyRunApp.OMARCHY_POLL_SECONDS = 0.2
        app = make_app(library)  # theme = auto
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.5)
            first = app.current_theme
            assert first.background.lower() == "#101820" and first.accent.lower() == "#ff8800"
            # the user switches Omarchy theme...
            (cur / "theme.name").write_text("two\n")
            (cur / "theme" / "colors.toml").write_text('background="#fdf6e3"\nforeground="#222222"\naccent="#0066cc"\nmode="light"\n')
            for _ in range(30):
                await pilot.pause(0.1)
                if app.current_theme.background.lower() == "#fdf6e3":
                    break
            now = app.current_theme
            assert now.background.lower() == "#fdf6e3" and now.accent.lower() == "#0066cc" and not now.dark
            # a fixed theme is NOT touched by Omarchy changes
            app.settings.theme = "nord"
            app.apply_theme_choice("nord")
            (cur / "theme.name").write_text("three\n")
            await pilot.pause(0.6)
            assert app.theme == "nord"

    try:
        run(go())
    finally:
        NyRunApp.OMARCHY_POLL_SECONDS = 2.0
