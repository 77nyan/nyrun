"""Music: scanning, tags and covers, import, the VLC session, the Now Playing screen, the disc, the logo line."""

import asyncio
import stat
import time
from pathlib import Path

import pytest
from PIL import Image

from nyrun import audio, player
from nyrun.app import NyRunApp
from nyrun.config import Settings, State
from nyrun.disc import FRAMES, render_frames
from nyrun.library import ImportError_, import_into_library, item_duration, scan_category, sniff_kind
from nyrun.music import MusicSession, mmss
from nyrun.music_screen import NowPlayingScreen
from nyrun.screens import LOGO, HomeScreen, ImportScreen, LibraryScreen
from nyrun.settings_screens import SettingsScreen
from nyrun.widgets import Rainbow

from conftest import make_poster, make_wav

SIZE = (150, 48)


def run(coro):
    return asyncio.run(coro)


def make_app(library, **kw):
    return NyRunApp(Settings(default_folder=str(library), **kw), State())


def album(library, title="Moon Songs"):
    return next(i for i in scan_category(library, "Music") if i.title == title)


def log_lines(log: Path) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


def wait_for(pred, timeout=6.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def make_tagged_mp3(path: Path, *, title="", artist="", cover: bytes | None = None) -> None:
    from mutagen.id3 import APIC, ID3, TIT2, TPE1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    tag = ID3()
    if title:
        tag.add(TIT2(encoding=3, text=title))
    if artist:
        tag.add(TPE1(encoding=3, text=artist))
    if cover:
        tag.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=cover))
    tag.save(str(path))


def png_bytes(color=(10, 200, 90)) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ scanning

def test_music_albums_are_scanned(library):
    items = scan_category(library, "Music")
    assert [i.title for i in items] == ["Moon Songs", "No Cover Album", "Single Song"]
    moon = items[0]
    assert moon.is_music and not moon.is_collection and moon.year == "2020"
    assert [e.label for e in moon.episodes] == ["01 - Track 1", "02 - Track 2", "10 - Track 10"]  # natural order
    assert moon.poster and moon.poster.name == "cover.png"
    assert items[1].poster is None
    assert items[2].poster and items[2].poster.name == "Single Song.png"  # loose file + sibling image


def test_video_files_are_not_music_and_vice_versa(tmp_path):
    root = tmp_path / "R"
    (root / "Music" / "A").mkdir(parents=True)
    (root / "Music" / "A" / "film.mp4").write_bytes(b"0")
    (root / "Movies" / "B").mkdir(parents=True)
    (root / "Movies" / "B" / "song.mp3").write_bytes(b"0")
    assert scan_category(root, "Music") == [] and scan_category(root, "Movies") == []


def test_embedded_cover_becomes_the_poster(tmp_path):
    root = tmp_path / "R"
    make_tagged_mp3(root / "Music" / "Green Album" / "01.mp3", title="Hi", cover=png_bytes())
    (item,) = scan_category(root, "Music")
    assert item.poster is not None and item.poster.exists()
    assert item.poster.parent.name == "covers"
    with Image.open(item.poster) as im:
        assert im.size == (64, 64) and im.getpixel((5, 5)) == (10, 200, 90)
    # a folder image wins over embedded art
    make_poster(root / "Music" / "Green Album" / "cover.jpg")
    (item,) = scan_category(root, "Music")
    assert item.poster.name == "cover.jpg"


def test_tags_and_durations(tmp_path):
    wav = tmp_path / "a.wav"
    make_wav(wav, 2.0)
    assert audio.duration(wav) == pytest.approx(2.0, abs=0.05)
    mp3 = tmp_path / "t.mp3"
    make_tagged_mp3(mp3, title="Moonlight", artist="Nyan")
    tags = audio.read_tags(mp3)
    assert tags.title == "Moonlight" and tags.artist == "Nyan"
    assert audio.read_tags(tmp_path / "missing.mp3") == audio.Tags()
    junk = tmp_path / "junk.flac"
    junk.write_bytes(b"not audio")
    assert audio.read_tags(junk).title == ""  # damaged files never raise


def test_album_duration_is_the_sum(library):
    assert item_duration(album(library)) == pytest.approx(60.0, abs=0.5)


def test_mmss():
    assert mmss(None) == "--:--" and mmss(5) == "0:05" and mmss(65) == "1:05" and mmss(3725) == "1:02:05"


# -------------------------------------------------------------------- import

def test_sniff_kind(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.mp3").write_bytes(b"0")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x.mkv").write_bytes(b"0")
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "x.mkv").write_bytes(b"0")
    (tmp_path / "c" / "y.mp3").write_bytes(b"0")
    assert sniff_kind(tmp_path / "a") == "audio" and sniff_kind(tmp_path / "b") == "video"
    assert sniff_kind(tmp_path / "c") is None and sniff_kind(tmp_path / "a" / "x.mp3") == "audio"


def test_import_single_song_and_album(library, isolated_env):
    dl = isolated_env / "Downloads"
    make_wav(dl / "Cool Song.wav")
    make_poster(dl / "Cool Song.png", size=(100, 100))
    dest = import_into_library(dl / "Cool Song.wav", library, "Music", title="Cool Song")
    assert dest == library / "Music" / "Cool Song"
    assert (dest / "Cool Song.wav").exists() and (dest / "Cool Song.png").exists()
    assert not (dl / "Cool Song.wav").exists()
    make_wav(dl / "Big Album" / "01.wav")
    dest = import_into_library(dl / "Big Album", library, "Music", move=False)
    assert (dest / "01.wav").exists() and (dl / "Big Album" / "01.wav").exists()  # copy keeps the original
    assert any(i.title == "Big Album" for i in scan_category(library, "Music"))


def test_import_refuses_the_wrong_category(library, isolated_env):
    dl = isolated_env / "Downloads"
    make_wav(dl / "Song.wav")
    with pytest.raises(ImportError_, match="music"):
        import_into_library(dl / "Song.wav", library, "Movies")
    (dl / "Film").mkdir()
    (dl / "Film" / "f.mkv").write_bytes(b"0")
    with pytest.raises(ImportError_, match="video"):
        import_into_library(dl / "Film", library, "Music")
    make_wav(dl / "Album" / "1.wav")
    with pytest.raises(ImportError_, match="music"):
        import_into_library(dl / "Album", library, "Shows")
    assert (dl / "Album" / "1.wav").exists()  # nothing was moved


# ------------------------------------------------------------------- command

def test_audio_command_is_headless_and_has_no_video_flags(tmp_path):
    s = Settings(fullscreen=True, vlc_hwdec="none", vlc_vout="gl", vlc_aout="pulse", vlc_title_osd=False)
    args = player.audio_args(s, controlled=True, volume=80)
    assert args[:4] == ["-I", "rc", "--rc-fake-tty", "--no-video"]
    assert "--no-one-instance" in args and "--volume=205" in args and "--aout=pulse" in args
    for video_only in ("--fullscreen", "--vout=gl", "--avcodec-hw=none", "--no-video-title-show"):
        assert video_only not in args
    assert "--repeat" in player.audio_args(s, controlled=True, repeat_track=True)
    window = player.audio_args(s, controlled=False)
    assert "-I" not in window and "--no-video" not in window and "--play-and-exit" in window
    cmd = player.build_audio_command([Path("/m/a.mp3")], ["vlc"], s, controlled=True)
    assert cmd[0] == "vlc" and cmd[-2:] == ["--", "/m/a.mp3"]


# ------------------------------------------------------------------- session

@pytest.fixture
def session(library, fake_music_vlc):
    made = []

    def make(title="Moon Songs", **settings):
        s = MusicSession(album(library, title), Settings(default_folder=str(library), **settings))
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def args_lines(log):
    return [ln for ln in log_lines(log) if ln.startswith("ARGS")]


def cmds(log):
    return [ln[4:] for ln in log_lines(log) if ln.startswith("CMD")]


def test_session_starts_a_headless_vlc_with_the_album_in_order(session, fake_music_vlc, library):
    s = session()
    s.start()
    assert wait_for(lambda: args_lines(fake_music_vlc))
    line = args_lines(fake_music_vlc)[0]
    assert "-I rc" in line and "--no-video" in line and "--volume=205" in line
    files = line.split(" -- ")[1].split(" ")
    assert [Path(f).name for f in (line.split(" -- ")[1].replace(".wav ", ".wav|").split("|"))] == [
        "01 - Track 1.wav", "02 - Track 2.wav", "10 - Track 10.wav"]
    assert s.state == "playing" and s.alive and files
    assert wait_for(lambda: s.tracks[0].duration is not None)  # tags load in the background
    assert s.tracks[0].duration == pytest.approx(20.0, abs=0.05)


def test_controls_send_the_right_commands(session, fake_music_vlc):
    s = session()
    s.start()
    assert wait_for(lambda: args_lines(fake_music_vlc))
    s.toggle_pause()
    assert s.state == "paused"
    frozen = s.position()
    time.sleep(0.25)
    assert s.position() == frozen  # the clock stops while paused
    s.toggle_pause()
    assert s.state == "playing"
    assert s.next() and s.qpos == 1
    assert s.prev() and s.qpos == 0  # (less than 3 s in: goes to the previous track)
    assert s.set_volume(50) == 50
    assert s.cycle_loop() == "all" and s.cycle_loop() == "track" and s.cycle_loop() == "off"
    assert wait_for(lambda: cmds(fake_music_vlc).count("repeat off") == 2)
    got = cmds(fake_music_vlc)
    assert got[:5] == ["pause", "pause", "next", "prev", "volume 128"]
    assert "repeat on" in got


def test_seek_is_clamped_and_sent(library, fake_music_vlc):
    make_wav(library / "Music" / "Long One" / "a.wav", 40.0, rate=100)
    s = MusicSession(album(library, "Long One"), Settings())
    try:
        s.start()
        assert wait_for(lambda: s.current.duration)
        assert s.seek(10)
        assert 9.5 <= s.position() <= 11
        assert s.seek(1000) and s.position() == pytest.approx(39, abs=0.5)  # never past the end
        assert s.seek(-1000) and s.position() < 0.5
        assert wait_for(lambda: len([c for c in cmds(fake_music_vlc) if c.startswith("seek")]) == 3)
        assert [c for c in cmds(fake_music_vlc) if c.startswith("seek")][:2] == ["seek 10", "seek 39"]
    finally:
        s.close()


def short_album(library):
    for n in (1, 2, 3):
        make_wav(library / "Music" / "Short Songs" / f"{n}.wav", 1.0)
    return "Short Songs"


def test_the_marker_follows_the_tracks_and_finishes(session, fake_music_vlc, library):
    s = session(short_album(library))
    s.start()
    seen = []
    assert wait_for(lambda: (s.tick(), seen.append(s.qpos), s.state == "ended")[2], timeout=8, step=0.1)
    assert s.state == "ended"
    assert sorted(set(seen)) == [0, 1, 2] and seen == sorted(seen)  # 1 -> 2 -> 10, never backwards


def test_repeat_album_starts_over(session, fake_music_vlc):
    s = session("No Cover Album", music_loop="all")
    s.start()
    assert wait_for(lambda: (s.tick(), len(args_lines(fake_music_vlc)) >= 2)[1], timeout=8, step=0.1)
    assert s.state == "playing"


def test_repeat_track_keeps_the_same_track(session, fake_music_vlc, library):
    s = session(short_album(library), music_loop="track")
    s.start()
    assert wait_for(lambda: args_lines(fake_music_vlc))
    assert "--repeat" in args_lines(fake_music_vlc)[0]
    time.sleep(1.6)
    s.tick()
    assert s.qpos == 0 and s.state == "playing"


def test_jump_and_prev_before_the_start_of_vlcs_own_queue(session, fake_music_vlc):
    s = session()
    s.start()
    assert wait_for(lambda: len(args_lines(fake_music_vlc)) == 1)
    s.jump(2)
    assert s.qpos == 2
    assert wait_for(lambda: len(args_lines(fake_music_vlc)) == 2)
    assert args_lines(fake_music_vlc)[1].split(" -- ")[1].count(".wav") == 1  # only the last track is queued
    s.prev()  # VLC's queue starts here, so NyRun restarts it one track earlier
    assert s.qpos == 1
    assert wait_for(lambda: len(args_lines(fake_music_vlc)) == 3)
    assert args_lines(fake_music_vlc)[2].split(" -- ")[1].count(".wav") == 2


def test_shuffle_keeps_the_current_track_first_and_resumes(session, fake_music_vlc):
    s = session()
    s.start(1)
    assert s.queue == [0, 1, 2] and s.qpos == 1
    time.sleep(0.3)
    assert s.toggle_shuffle() is True
    assert s.queue[0] == 1 and sorted(s.queue) == [0, 1, 2] and s.qpos == 0 and s.current.index == 1
    assert wait_for(lambda: (s.tick(), any(c.startswith("seek") for c in cmds(fake_music_vlc)))[1])
    assert s.toggle_shuffle() is False
    assert s.queue == [0, 1, 2] and s.current.index == 1


def test_vlc_failure_is_reported(library, tmp_path, monkeypatch):
    script = tmp_path / "badvlc"
    script.write_text('#!/bin/sh\necho "boom: unknown option" >&2\nexit 1\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    s = MusicSession(album(library), Settings())
    s.start()
    assert wait_for(lambda: s.proc is not None and s.proc.poll() is not None)
    s.tick()
    err = s.take_error()
    assert "code 1" in err and "boom" in err and s.state == "stopped"
    assert s.take_error() == ""


def test_window_mode_has_no_remote_control(session, fake_music_vlc):
    s = session(music_controls=False)
    s.start()
    assert wait_for(lambda: args_lines(fake_music_vlc))
    assert "-I rc" not in args_lines(fake_music_vlc)[0]
    assert s.toggle_pause() is False and s.seek(5) is False


def test_close_really_stops_vlc(session):
    s = session()
    s.start()
    proc = s.proc
    assert proc.poll() is None
    s.close()
    assert proc.poll() is not None and s.proc is None and s.state == "stopped"


# ---------------------------------------------------------------------- disc

def test_disc_frames_rotate_and_show_the_cover(tmp_path):
    cover = tmp_path / "c.png"
    Image.new("RGB", (200, 200), (250, 20, 20)).save(cover)
    for path in (None, cover):
        frames = render_frames(path, 24, "#ffb7d5", "#b39ddb")
        assert len(frames) == FRAMES
        for f in frames:
            lines = f.plain.split("\n")
            assert len(lines) == 12 and all(len(ln) == 24 for ln in lines)
        assert len({f.plain + str([sp.style for sp in f.spans][:30]) for f in frames}) > 4  # it really turns
    red = render_frames(cover, 24, None, None)[0]
    def reddish(sp):
        c = sp.style.color.get_truecolor() if sp.style and sp.style.color else None
        return c is not None and c.red > 200 and c.green < 80 and c.blue < 80

    assert any(reddish(sp) for sp in red.spans)  # the cover is the label of the record
    assert render_frames(None, 24, None, None)[0].plain  # theme colours missing -> sensible defaults


# ----------------------------------------------------------------------- TUI

async def settle(pilot, pred, tries=60):
    for _ in range(tries):
        await pilot.pause(0.1)
        if pred():
            return True
    return pred()


def test_home_has_a_music_tile_and_no_surprise(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            scr = app.screen
            assert isinstance(scr, HomeScreen)
            music = scr.query_one("#tile-Music")
            assert "3 albums" in str(music.render()) and "5 tracks" in str(music.render())
            imp = str(scr.query_one("#tile-import").render())
            assert "move / copy from" in imp and "Downloads or any" in imp and "directory" in imp
            await pilot.press("4")
            await pilot.pause(0.5)
            assert isinstance(app.screen, LibraryScreen) and app.screen.category == "Music"
            assert [i.title for i in app.screen.items] == ["Moon Songs", "No Cover Album", "Single Song"]
            assert "albums" in str(app.screen.query_one("#status-bar").render())

    run(go())


def test_play_an_album_with_the_now_playing_screen(library, fake_music_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("4")
            await pilot.pause(0.5)
            await pilot.press("enter")
            assert await settle(pilot, lambda: isinstance(app.screen, NowPlayingScreen))
            scr = app.screen
            m = app.music
            assert m is not None and m.state == "playing" and m.item.title == "Moon Songs"
            disc = scr.query_one("#np-disc")
            assert await settle(pilot, lambda: len(disc.frames) == FRAMES)
            assert disc.spinning
            before = disc._i
            await pilot.pause(0.5)
            assert disc._i != before  # the record turns
            assert await settle(pilot, lambda: scr.query_one("#np-tracks").option_count == 3)
            assert str(scr.query_one("#np-title").render()).startswith("Track 1") or "Track 1" in str(scr.query_one("#np-title").render())
            assert "Moon Songs" in str(scr.query_one("#np-sub").render())

            await pilot.press("space")
            await pilot.pause(0.4)
            assert m.state == "paused" and not disc.spinning
            await pilot.press("space")
            await pilot.press("n")
            await pilot.pause(0.4)
            assert m.qpos == 1
            await pilot.press("plus")
            assert m.volume == 85 and app.settings.music_volume == 85
            await pilot.press("l")
            assert m.loop == "all" and Settings.load().music_loop == "all"

            await pilot.press("escape")  # back to the library - the music keeps going
            await pilot.pause(0.4)
            assert isinstance(app.screen, LibraryScreen) and m.alive and m.state == "playing"
            await pilot.press("escape")
            await pilot.pause(0.4)
            assert isinstance(app.screen, HomeScreen)
            assert "Track 2" in str(app.screen.query_one("#tile-nowplaying").render())
            await pilot.press("n")
            assert await settle(pilot, lambda: isinstance(app.screen, NowPlayingScreen))
            proc = m.proc
            await pilot.press("x")
            await pilot.pause(0.4)
            assert app.music is None and proc.poll() is not None and isinstance(app.screen, HomeScreen)
        assert "Music/Moon Songs (2020)" in State().watched  # auto-watched applies to albums too

    run(go())
    assert {"pause", "next"} <= set(cmds(fake_music_vlc))


def test_quitting_stops_the_music(library, fake_music_vlc):
    holder = {}

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("4", "p")
            assert await settle(pilot, lambda: app.music is not None)
            holder["proc"] = app.music.proc
            assert holder["proc"].poll() is None

    run(go())
    assert wait_for(lambda: holder["proc"].poll() is not None)


def test_selecting_a_track_plays_it(library, fake_music_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("4", "enter")
            assert await settle(pilot, lambda: isinstance(app.screen, NowPlayingScreen))
            ol = app.screen.query_one("#np-tracks")
            assert await settle(pilot, lambda: ol.option_count == 3)
            ol.focus()
            await pilot.press("down", "down", "enter")
            await pilot.pause(0.5)
            assert app.music.qpos == 2 and app.music.current.label.startswith("10")

    run(go())


def test_window_mode_explains_itself(library, fake_music_vlc):
    seen = []

    async def go():
        app = make_app(library, music_controls=False)
        orig = app.notify
        app.notify = lambda msg, **kw: (seen.append(str(msg)), orig(msg, **kw))[1]
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("4", "enter")
            assert await settle(pilot, lambda: isinstance(app.screen, NowPlayingScreen))
            await pilot.press("space")
            await pilot.pause(0.3)

    run(go())
    assert any("window mode" in m for m in seen)


def test_playing_a_film_pauses_the_music(library, fake_music_vlc, tmp_path, monkeypatch):
    import stat as st

    # films go through the same fake; only the music commands are of interest here
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("4", "enter")
            assert await settle(pilot, lambda: isinstance(app.screen, NowPlayingScreen))
            await pilot.press("escape", "escape")
            await pilot.pause(0.3)
            await pilot.press("1", "enter")
            await pilot.pause(0.6)
            assert app.music.state == "paused"

    run(go())


def test_music_import_picks_the_music_category(library, isolated_env):
    dl = isolated_env / "Downloads"
    make_wav(dl / "Fresh Album" / "01.wav")

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("i")
            await pilot.pause(0.5)
            scr = app.screen
            assert isinstance(scr, ImportScreen)
            await settle(pilot, lambda: scr.query_one("#imp-tree").cursor_node is not None)
            for _ in range(10):
                if scr.selected and scr.selected.name == "Fresh Album":
                    break
                await pilot.press("down")
                await pilot.pause(0.1)
            assert scr.selected and scr.selected.name == "Fresh Album"
            assert scr._current_cat() == "Music"
            await pilot.press("ctrl+g")
            assert await settle(pilot, lambda: not isinstance(app.screen, ImportScreen))
            assert any(i.title == "Fresh Album" for i in app.library["Music"])

    run(go())
    assert (library / "Music" / "Fresh Album" / "01.wav").exists()


# ------------------------------------------------------------- the logo line

def test_logo_rows_have_one_width():
    assert len({len(r) for r in LOGO}) == 1


@pytest.mark.parametrize("mode", ["rainbow", "theme"])
def test_line_is_exactly_as_wide_as_the_logo(library, mode):
    async def go():
        app = make_app(library, logo_line=mode)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.4)
            line = app.screen.query_one(Rainbow)
            assert len(line.line().plain) == len(LOGO[0])
            logo = app.screen.query_one("#logo")

            def drawn(widget, y=0):
                text = widget.render_line(y).text  # what is really painted on that row
                return len(text) - len(text.lstrip()), len(text.rstrip())

            assert drawn(line) == drawn(logo, 0)  # same first and last painted column as the logo
            first, last = drawn(line)
            assert last - first == len(LOGO[0])

    run(go())


def test_theme_line_uses_the_theme_colours_and_rainbow_does_not(library):
    from nyrun.themes import BUILTIN

    async def go(mode):
        app = make_app(library, logo_line=mode, theme="nord")
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.4)
            return {str(sp.style) for sp in app.screen.query_one(Rainbow).line().spans}

    nord = BUILTIN["nord"]
    theme_colours = run(go("theme"))
    lo, hi = sorted([int(nord.accent[1:3], 16), int(nord.secondary[1:3], 16)])
    assert all(lo - 1 <= int(c[1:3], 16) <= hi + 1 for c in theme_colours)  # blends accent and secondary only
    assert len(run(go("rainbow"))) > 5 and run(go("rainbow")) != theme_colours


def test_logo_line_setting_is_in_settings_and_saved(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("s")
            await pilot.pause(0.5)
            scr = app.screen
            assert isinstance(scr, SettingsScreen)
            scr.query_one("#f-logo_line").value = "theme"
            scr.query_one("#f-music_controls").value = False
            scr.query_one("#f-music_loop").value = "all"
            scr.query_one("#f-music_shuffle").value = True
            await pilot.pause(0.4)

    run(go())
    s = Settings.load()
    assert s.logo_line == "theme" and not s.music_controls and s.music_loop == "all" and s.music_shuffle


def test_line_follows_omarchy_when_set_to_theme(library, tmp_path, monkeypatch):
    state = tmp_path / "state"
    cur = state / "omarchy" / "current"
    (cur / "theme").mkdir(parents=True)
    (cur / "theme.name").write_text("x\n")
    (cur / "theme" / "colors.toml").write_text('background="#101820"\nforeground="#e0e0e0"\naccent="#ff8800"\ncolor5="#0088ff"\n')
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    async def go():
        app = make_app(library, logo_line="theme")  # theme = auto -> Omarchy
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.5)
            colours = {str(sp.style) for sp in app.screen.query_one(Rainbow).line().spans}
            reds = [int(c[1:3], 16) for c in colours]
            assert max(reds) >= 0xe0 or max(reds) > 200  # orange accent in the blend
            assert min(int(c[5:7], 16) for c in colours) < 0x40  # ... and no pink default

    run(go())


# --------------------------------------------------------------- add a file

def test_add_wizard_creates_folder_with_file_and_image(library, isolated_env):
    from nyrun.add_screen import AddScreen, suggest_category

    dl = isolated_env / "Downloads"
    (dl / "My.Film.2019.mp4").write_bytes(b"0" * 100)
    make_poster(dl / "art.png")
    assert suggest_category(dl / "My.Film.2019.mp4") == "Movies"

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("a")
            await pilot.pause(0.5)
            scr = app.screen
            assert isinstance(scr, AddScreen) and scr.step == "file"
            await settle(pilot, lambda: scr.query_one("#add-tree").cursor_node is not None)
            scr.file = dl / "My.Film.2019.mp4"
            scr._go("where")
            await pilot.press("down", "up", "enter")  # choose Movies
            await pilot.pause(0.2)
            assert scr.step == "name" and scr.category == "Movies"
            assert scr.query_one("#add-name").value == "My Film (2019)"
            scr.query_one("#add-name").value = "My Film"
            await pilot.press("enter")
            assert scr.step == "image"
            scr.query_one("#add-image").value = str(dl / "art.png")
            await pilot.press("enter")
            assert scr.step == "confirm"
            await pilot.press("enter")
            assert await settle(pilot, lambda: not isinstance(app.screen, AddScreen))
            assert any(i.title == "My Film" and i.poster for i in app.library["Movies"])

    run(go())
    d = library / "Movies" / "My Film"
    assert (d / "My.Film.2019.mp4").exists() and (d / "poster.png").exists()
    assert not (isolated_env / "Downloads" / "My.Film.2019.mp4").exists()


def test_add_wizard_image_is_optional_and_validated(library, isolated_env):
    from nyrun.add_screen import AddScreen

    dl = isolated_env / "Downloads"
    (dl / "Show.S01E01.mkv").write_bytes(b"0")

    async def go():
        app = make_app(library)
        app.open_add = dl / "Show.S01E01.mkv"
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.6)
            scr = app.screen
            assert isinstance(scr, AddScreen) and scr.step == "where"
            assert scr.query_one("#add-cat").highlighted == 1  # looks like a series
            await pilot.press("enter")
            scr.query_one("#add-name").value = "Some Show"
            await pilot.press("enter")
            scr.query_one("#add-image").value = "/nope.png"
            await pilot.press("enter")
            assert scr.step == "image"  # bad path is refused
            scr.query_one("#add-image").value = ""
            await pilot.press("enter")
            await pilot.press("down", "space", "enter")  # copy
            assert await settle(pilot, lambda: not isinstance(app.screen, AddScreen))

    run(go())
    assert (library / "Series" / "Some Show" / "Show.S01E01.mkv").exists()
    assert (dl / "Show.S01E01.mkv").exists()
