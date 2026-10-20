"""The URL screen, its Home tile, the Settings section and the command line, driven like a user."""

from __future__ import annotations

import asyncio
import time

import pytest
from conftest import MP4_BYTES

from nyrun.app import NyRunApp
from nyrun.config import Settings, State
from nyrun.settings_screens import SettingsScreen
from nyrun.url_screen import UrlScreen

SIZE = (150, 48)


def make_app(library, **kw):
    kw.setdefault("url_ytdlp", "never")
    return NyRunApp(Settings(default_folder=str(library), **kw), State())


async def wait_for(pilot, cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause(0.1)
        if cond():
            return True
    return False


def text_of(widget) -> str:
    r = widget.render()
    return getattr(r, "plain", str(r))


async def type_url(pilot, url):
    scr = pilot.app.screen
    box = scr.query_one("#url-input")
    box.value = url
    box.focus()
    await pilot.pause(0.1)


def test_home_has_a_url_tile_and_u_opens_the_screen(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.4)
            tile = app.screen.query_one("#tile-url")
            assert "URL" in text_of(tile)
            await pilot.press("u")
            await pilot.pause(0.4)
            assert isinstance(app.screen, UrlScreen)
            assert "cannot open every" in text_of(app.screen.query_one("#url-info")).replace("can't", "cannot") or "can't open every" in text_of(app.screen.query_one("#url-info"))
            await pilot.press("escape")
            await pilot.pause(0.3)
            assert not isinstance(app.screen, UrlScreen)

    asyncio.run(go())


def test_home_tile_is_clickable(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.4)
            await pilot.click("#tile-url")
            await pilot.pause(0.4)
            assert isinstance(app.screen, UrlScreen)

    asyncio.run(go())


def test_home_with_url_tile_fits_a_normal_terminal(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.4)
            for tid in ("#tile-url", "#tile-add"):
                reg = app.screen.query_one(tid).region
                assert reg.right <= 120 and reg.width > 0

    asyncio.run(go())


def test_enter_plays_a_direct_link_in_vlc(library, web, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=30, speed=40)
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    url = web.url("/clip.mp4")

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, url)
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            line = log.read_text().splitlines()[0]
            assert url in line and "--rc-host=127.0.0.1:" in line and "--meta-title=" in line
            assert "Opened in VLC" in text_of(app.screen.query_one("#url-status"))
            # remembered in Recent, and shown on the screen's list
            assert app.state.url_history[0]["url"] == url
            assert app.screen.query_one("#url-recent").option_count == 1
            # a half-watched link is remembered like a film
            assert app.state.positions["url:" + url][1] == 100

    asyncio.run(go())


def test_page_link_sends_the_referer_to_vlc(library, web, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=20, speed=40)
    web.add("/m.mp4", MP4_BYTES, "video/mp4")
    web.page("/page", '<html><head><title>Nice Page</title><meta property="og:video" content="/m.mp4"></head></html>')

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, web.url("/page"))
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            line = log.read_text().splitlines()[0]
            assert web.url("/m.mp4") in line
            assert f"--http-referrer={web.url('/page')}" in line
            assert "--meta-title=Nice Page" in line

    asyncio.run(go())


def test_bad_input_shows_a_message_and_does_not_start_vlc(library, fake_watch_vlc):
    log = fake_watch_vlc()

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, "hello there")
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert "✗" in text_of(app.screen.query_one("#url-status"))
            await type_url(pilot, "magnet:?xt=urn:btih:abc")
            await pilot.press("enter")
            await pilot.pause(0.4)
            assert "torrent" in text_of(app.screen.query_one("#url-status")).lower()
            assert not log.exists()

    asyncio.run(go())


def test_dead_link_is_explained(library, web, fake_watch_vlc):
    log = fake_watch_vlc()

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, web.url("/nothing-here"))
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: "404" in text_of(app.screen.query_one("#url-status")))
            assert not log.exists()
            assert app.state.url_history == []

    asyncio.run(go())


def test_recent_list_replays_and_forgets(library, web, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=20, speed=40)
    web.add("/a.mp4", MP4_BYTES, "video/mp4")

    async def go():
        app = make_app(library)
        app.state.add_url(web.url("/a.mp4"), "Old One", "video file")
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            ol = app.screen.query_one("#url-recent")
            assert ol.option_count == 1
            await pilot.press("down")  # input -> list
            await pilot.pause(0.1)
            assert app.screen.focused is ol
            await pilot.press("enter")
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            assert web.url("/a.mp4") in log.read_text().splitlines()[0]
            # forget it with Delete
            ol = app.screen.query_one("#url-recent")
            ol.focus()
            ol.highlighted = 0
            await pilot.pause(0.1)
            await pilot.press("delete")
            await pilot.pause(0.2)
            assert app.state.url_history == []

    asyncio.run(go())


def test_ctrl_d_says_downloads_are_off(library, web):
    web.add("/a.mp4", MP4_BYTES, "video/mp4")

    async def go():
        app = make_app(library)  # url_downloads defaults to off
        notes = []
        app.notify = lambda msg, **kw: notes.append(str(msg))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, web.url("/a.mp4"))
            await pilot.press("ctrl+d")
            await pilot.pause(0.3)
            assert any("off" in n for n in notes)
        assert not (library / "URL Downloads").exists()

    asyncio.run(go())


def test_ctrl_d_downloads_into_url_downloads_folder(library, web):
    web.add("/Cool_Clip.mp4", MP4_BYTES, "video/mp4")

    async def go():
        app = make_app(library, url_downloads=True)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, web.url("/Cool_Clip.mp4"))
            await pilot.press("ctrl+d")  # resolves first, then saves
            dest = library / "URL Downloads" / "Cool Clip" / "Cool Clip.mp4"
            assert await wait_for(pilot, dest.is_file)
            assert await wait_for(pilot, lambda: "Saved" in text_of(app.screen.query_one("#url-status")))
            assert dest.read_bytes() == MP4_BYTES

    asyncio.run(go())


def test_download_dir_setting_is_used(library, web, tmp_path):
    web.add("/c.mp4", MP4_BYTES, "video/mp4")
    target = tmp_path / "elsewhere"

    async def go():
        app = make_app(library, url_downloads=True, url_download_dir=str(target))
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, web.url("/c.mp4"))
            await pilot.press("ctrl+d")
            assert await wait_for(pilot, lambda: any(target.rglob("*.mp4")))

    asyncio.run(go())


def test_failed_download_is_reported(library, web):
    async def go():
        app = make_app(library, url_downloads=True)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("u")
            await pilot.pause(0.3)
            await type_url(pilot, web.url("/gone.mp4"))
            await pilot.press("ctrl+d")
            assert await wait_for(pilot, lambda: "404" in text_of(app.screen.query_one("#url-status")))

    asyncio.run(go())


def test_open_url_attribute_opens_screen_and_plays(library, web, fake_watch_vlc):
    """What `nyrun -url LINK` does."""
    log = fake_watch_vlc(length=100, stop=20, speed=40)
    web.add("/z.mp4", MP4_BYTES, "video/mp4")

    async def go():
        app = make_app(library)
        app.open_url = web.url("/z.mp4")
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.5)
            assert isinstance(app.screen, UrlScreen)
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())

    asyncio.run(go())


# ------------------------------------------------------------------ settings

def test_settings_has_web_video_section_with_the_limits_note(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("comma")
            await pilot.pause(0.5)
            scr = app.screen
            assert isinstance(scr, SettingsScreen)
            for wid in ("#f-url_downloads", "#f-url_ytdlp", "#f-url_quality", "#f-url_cookies", "#f-url_download_dir", "#st-url-tools", "#st-url-limits"):
                scr.query_one(wid)
            limits = text_of(scr.query_one("#st-url-limits")).lower()
            assert "cannot" in limits and "drm" in limits
            tools = text_of(scr.query_one("#st-url-tools"))
            assert "ffmpeg" in tools and "yt-dlp" in tools

    asyncio.run(go())


def test_settings_save_url_options(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("comma")
            await pilot.pause(0.5)
            scr = app.screen
            scr.query_one("#f-url_downloads").value = True
            scr.query_one("#f-url_ytdlp").value = "always"
            scr.query_one("#f-url_quality").value = "720"
            scr.query_one("#f-url_download_dir").value = "~/Videos/Saved"
            await pilot.pause(0.3)
            await pilot.press("escape")  # leaving Settings saves them
            await pilot.pause(0.4)

    asyncio.run(go())
    s = Settings.load()
    assert s.url_downloads is True and s.url_ytdlp == "always" and s.url_quality == "720"
    assert s.url_download_dir == "~/Videos/Saved"


def test_settings_tool_detection_reports_missing_ffmpeg(library, monkeypatch):
    from nyrun import settings_screens as ss

    monkeypatch.setattr(ss, "find_ffmpeg", lambda *a: None, raising=False)
    monkeypatch.setattr("nyrun.urlengine.tools.shutil.which", lambda n: None)

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            await pilot.press("comma")
            await pilot.pause(0.5)
            tools = text_of(app.screen.query_one("#st-url-tools"))
            assert "ffmpeg" in tools and ("not found" in tools.lower() or "missing" in tools.lower())

    asyncio.run(go())


# ------------------------------------------------------------------ command line

def test_cli_download_saves_a_file(library, web, tmp_path, capsys):
    from nyrun import cli

    web.add("/cli.mp4", MP4_BYTES, "video/mp4")
    Settings(default_folder=str(library), url_ytdlp="never").save()
    rc = cli.main(["-download", web.url("/cli.mp4")])
    out = capsys.readouterr().out
    assert rc in (0, None)
    assert list((library / "URL Downloads").rglob("*.mp4"))
    assert "Saved" in out or "saved" in out


def test_cli_download_reports_errors(library, web, capsys):
    from nyrun import cli

    Settings(default_folder=str(library), url_ytdlp="never").save()
    rc = cli.main(["-download", web.url("/nope.mp4")])
    out = capsys.readouterr()
    assert rc not in (0, None)
    assert "404" in out.out + out.err


def test_cli_help_and_doctor_mention_the_new_things(library, capsys):
    from nyrun import cli

    cli.main(["-help"])
    helptext = capsys.readouterr().out
    assert "-url" in helptext and "-download" in helptext
    cli.main(["-doctor"])
    doc = capsys.readouterr().out.lower()
    assert "ffmpeg" in doc and "yt-dlp" in doc


# ------------------------------------------------------------------ YouTube-style links (relayed through yt-dlp)

def _youtube_resolved():
    from nyrun.urlengine import Kind
    from nyrun.urlengine.model import Resolved

    return Resolved("https://www.youtube.com/watch?v=abc", "https://rr1---sn.googlevideo.com/videoplayback?x=1", Kind.FILE,
                    "yt-dlp", "A YouTube Video", audio_url="https://rr1---sn.googlevideo.com/videoplayback?a=1", via_ytdlp=True)


def test_only_youtube_style_links_are_relayed():
    from nyrun.urlengine import Kind
    from nyrun.urlengine.model import Resolved

    assert _youtube_resolved().needs_relay
    other = Resolved("https://vimeo.com/1", "https://cdn.vimeo.com/v.mp4", Kind.FILE, "yt-dlp", "V", via_ytdlp=True)
    assert not other.needs_relay
    direct = Resolved("https://youtube.com/x.mp4", "https://youtube.com/x.mp4", Kind.FILE, "direct", "D")
    assert not direct.needs_relay  # not found by yt-dlp: VLC can open it itself


def test_relay_command_formats(monkeypatch):
    from nyrun.urlengine import Options
    from nyrun.urlengine import ytdlp

    monkeypatch.setattr(ytdlp, "find_ffmpeg", lambda *_: "/usr/bin/ffmpeg")
    cmd = ytdlp.relay_command("https://youtu.be/x", Options(quality="720", cookies_browser="firefox"))
    assert cmd[-2:] == ["--", "https://youtu.be/x"] and ["-o", "-"] == cmd[cmd.index("-o"):cmd.index("-o") + 2]
    f = cmd[cmd.index("-f") + 1]
    assert f.startswith("bv*[height<=720]+ba") and "--merge-output-format" in cmd and "mkv" in cmd
    assert cmd[cmd.index("--cookies-from-browser") + 1] == "firefox"
    monkeypatch.setattr(ytdlp, "find_ffmpeg", lambda *_: None)
    plain = ytdlp.relay_command("https://youtu.be/x", Options())
    assert "+" not in plain[plain.index("-f") + 1] and "--merge-output-format" not in plain


def test_youtube_link_is_piped_from_ytdlp_into_vlc(library, tmp_path, monkeypatch):
    """VLC must get the video on stdin (fd://0), never the raw googlevideo address that YouTube answers with HTTP 429."""
    out = tmp_path / "vlc.out"
    vlc = tmp_path / "vlcpipe"
    vlc.write_text("#!/bin/sh\necho \"ARGS $@\" > %s\ncat >> %s\nsleep 5\n" % (out, out))
    vlc.chmod(0o755)
    monkeypatch.setenv("NYRUN_VLC", str(vlc))
    fake_ytdlp = tmp_path / "yt-dlp"
    fake_ytdlp.write_text("#!/bin/sh\necho \"$@\" > %s\nprintf 'VIDEODATA'\n" % (tmp_path / "ytdlp.args"))
    fake_ytdlp.chmod(0o755)
    monkeypatch.setattr("nyrun.urlengine.ytdlp.ytdlp_binary", lambda: str(fake_ytdlp))

    async def go():
        app = make_app(library)
        notes = []
        orig = app.notify
        app.notify = lambda msg, **kw: (notes.append(str(msg)), orig(msg, **kw))[1]
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            assert app.play_url(_youtube_resolved())
            assert await wait_for(pilot, lambda: out.exists() and "VIDEODATA" in out.read_text())
            text = out.read_text()
            assert "fd://0" in text and "googlevideo" not in text and "--rc-host" not in text
            assert "https://www.youtube.com/watch?v=abc" in (tmp_path / "ytdlp.args").read_text()
            assert any("yt-dlp" in n for n in notes)
            assert app.state.url_history[0]["url"] == "https://www.youtube.com/watch?v=abc"

    asyncio.run(go())


def test_vlc_refused_by_the_server_is_explained(library, monkeypatch, tmp_path, web):
    """VLC that quits with code 0 after 'HTTP 429' used to vanish silently; now the user is told."""
    script = tmp_path / "vlc429"
    script.write_text("#!/bin/sh\necho 'access stream error: HTTP 429 error' >&2\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    from nyrun.urlengine import Kind
    from nyrun.urlengine.model import Resolved

    res = Resolved("https://x.example/p", "https://x.example/v.mp4", Kind.FILE, "direct", "V")

    async def go():
        app = make_app(library)
        notes = []
        orig = app.notify
        app.notify = lambda msg, **kw: (notes.append(str(msg)), orig(msg, **kw))[1]
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play_url(res)
            assert await wait_for(pilot, lambda: any("HTTP 429" in n for n in notes), timeout=10)

    asyncio.run(go())


def test_google_drive_and_cookie_links_are_relayed():
    from nyrun.urlengine import Kind
    from nyrun.urlengine import ytdlp
    from nyrun.urlengine.model import Resolved

    drive = Resolved("https://drive.google.com/file/d/abc/view", "https://drive.usercontent.google.com/download?id=abc", Kind.FILE, "yt-dlp", "Clip", via_ytdlp=True)
    assert drive.needs_relay
    # any site whose address only works with cookies yt-dlp holds
    info = {"title": "Cookie site", "url": "https://cdn.example/v.mp4", "protocol": "https", "ext": "mp4", "http_headers": {"Cookie": "a=b", "User-Agent": "UA"}}
    res = ytdlp.info_to_resolved("https://site.example/watch/1", info)
    assert res.cookies and res.needs_relay
    plain = ytdlp.info_to_resolved("https://site.example/watch/2", {"title": "P", "url": "https://cdn.example/v.mp4", "protocol": "https", "ext": "mp4"})
    assert not plain.cookies and not plain.needs_relay
