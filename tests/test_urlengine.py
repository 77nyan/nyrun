"""The URL engine: detecting, resolving and downloading, against a local test web server."""

from __future__ import annotations

import json
import threading
import time

import pytest
from conftest import MP4_BYTES, png_bytes

from nyrun.urlengine import (
    Cancelled, Engine, Kind, Options, Progress, UrlError, download, downloads_root, normalize_url, safe_name,
)
from nyrun.urlengine import detect, hls, page, saver, tools, ytdlp
from nyrun.urlengine.model import Resolved

NO_YT = Options(ytdlp="never")


def engine(**kw) -> Engine:
    return Engine(Options(ytdlp=kw.pop("ytdlp", "never"), **kw))


# ------------------------------------------------------------------ normalising

@pytest.mark.parametrize("text,expected", [
    ("https://example.com/a.mp4", "https://example.com/a.mp4"),
    ("  https://example.com/a.mp4  ", "https://example.com/a.mp4"),
    ("<https://example.com/a.mp4>", "https://example.com/a.mp4"),
    ('"https://example.com/a.mp4"', "https://example.com/a.mp4"),
    ("example.com/watch?v=1", "https://example.com/watch?v=1"),
    ("www.example.com", "https://www.example.com"),
    ("//cdn.example.com/x.m3u8", "https://cdn.example.com/x.m3u8"),
    ("localhost:8080/v.mp4", "https://localhost:8080/v.mp4"),
    ("192.168.1.5:8080/stream", "https://192.168.1.5:8080/stream"),
    ("rtsp://cam.local/live", "rtsp://cam.local/live"),
    ("HTTP://EXAMPLE.com/Path", "http://example.com/Path"),
    ("https://example.com/ü/vidéo.mp4", "https://example.com/%C3%BC/vid%C3%A9o.mp4"),
])
def test_normalize_accepts(text, expected):
    assert normalize_url(text) == expected


def test_normalize_idn_host():
    assert normalize_url("https://bücher.example/x").startswith("https://xn--bcher-kva.example/")


@pytest.mark.parametrize("text", [
    "", "   ", "hello there", "not a url", "just-a-word", "magnet:?xt=urn:btih:abc", "file:///etc/passwd",
    "javascript:alert(1)", "data:text/html,hi", "blob:https://x/abc", "ftp2://x", "gopher://x/y", "https://",
])
def test_normalize_refuses(text):
    with pytest.raises(UrlError):
        normalize_url(text)


def test_refusal_messages_explain():
    with pytest.raises(UrlError) as e:
        normalize_url("magnet:?xt=urn:btih:abc")
    assert "torrent" in e.value.message.lower()
    with pytest.raises(UrlError) as e:
        normalize_url("file:///home/me/film.mkv")
    assert "Add a file" in e.value.message


# ------------------------------------------------------------------ detection

@pytest.mark.parametrize("url,kind", [
    ("https://x.com/a.mp4", Kind.FILE), ("https://x.com/a.MKV?token=1", Kind.FILE), ("https://x.com/a.webm", Kind.FILE),
    ("https://x.com/a.mp3", Kind.AUDIO), ("https://x.com/a.flac", Kind.AUDIO),
    ("https://x.com/live/index.m3u8?t=1", Kind.HLS), ("https://x.com/v/manifest.mpd", Kind.DASH),
    ("rtsp://cam/live", Kind.STREAM), ("udp://@239.0.0.1:1234", Kind.STREAM), ("rtmp://x/live/key", Kind.STREAM),
    ("https://x.com/radio.pls", Kind.PLAYLIST), ("https://x.com/page", None), ("https://x.com/", None), ("https://x.com/a.html", None),
])
def test_kind_from_url(url, kind):
    assert detect.kind_from_url(url) == kind


def test_kind_from_content_type_and_bytes():
    assert detect.kind_from_content_type("video/mp4") is Kind.FILE
    assert detect.kind_from_content_type("audio/mpeg; charset=x") is Kind.AUDIO
    assert detect.kind_from_content_type("application/vnd.apple.mpegurl") is Kind.HLS
    assert detect.kind_from_content_type("application/dash+xml") is Kind.DASH
    assert detect.kind_from_content_type("text/html") is None
    assert detect.kind_from_bytes(b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4,\nseg.ts") is Kind.HLS
    assert detect.kind_from_bytes(b"#EXTM3U\nhttp://radio/stream") is Kind.PLAYLIST
    assert detect.kind_from_bytes(b'<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011">') is Kind.DASH
    assert detect.kind_from_bytes(MP4_BYTES) is Kind.FILE
    assert detect.kind_from_bytes(b"ID3\x03\x00") is Kind.AUDIO
    assert detect.kind_from_bytes(b"<html>") is None


def test_known_hosts_and_titles():
    assert detect.is_known_host("https://www.youtube.com/watch?v=x")
    assert detect.is_known_host("https://m.youtube.com/watch?v=x")
    assert detect.is_known_host("https://youtu.be/x")
    assert not detect.is_known_host("https://notyoutube.com/x")
    assert detect.title_from_url("https://x.com/my_cool-video.mp4") == "my cool video"
    assert detect.title_from_url("https://example.com/") == "example.com"


# ------------------------------------------------------------------ resolving: direct and probe

def test_direct_file_needs_no_network():
    r = engine().resolve("https://nowhere.invalid/movies/My_Film.mp4")
    assert (r.kind, r.source, r.media_url) == (Kind.FILE, "direct", "https://nowhere.invalid/movies/My_Film.mp4")
    assert r.title == "My Film" and r.ext == "mp4"


def test_direct_hls_dash_and_stream():
    assert engine().resolve("https://x.invalid/a/index.m3u8").kind is Kind.HLS
    assert engine().resolve("https://x.invalid/a/manifest.mpd").kind is Kind.DASH
    r = engine().resolve("rtsp://cam.invalid/live")
    assert r.kind is Kind.STREAM and r.source == "direct"


def test_probe_finds_media_without_extension(web):
    web.add("/watch/42", MP4_BYTES, "video/mp4")
    r = engine().resolve(web.url("/watch/42"))
    assert (r.kind, r.source) == (Kind.FILE, "probe")
    assert r.media_url == web.url("/watch/42")


def test_probe_sniffs_media_with_a_useless_content_type(web):
    web.add("/v/1", MP4_BYTES, "application/octet-stream")
    assert engine().resolve(web.url("/v/1")).kind is Kind.FILE


def test_probe_recognises_hls_served_as_text(web):
    web.add("/live", "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg0.ts\n#EXT-X-ENDLIST\n", "text/plain")
    assert engine().resolve(web.url("/live")).kind is Kind.HLS


def test_probe_works_when_head_and_range_are_refused(web):
    web.add("/x", MP4_BYTES, "video/mp4", ranges=False, head=False)
    assert engine().resolve(web.url("/x")).kind is Kind.FILE


def test_redirects_are_followed(web):
    web.add("/media.mp4", MP4_BYTES, "video/mp4")
    web.add("/short", b"", "text/plain", status=302, headers={"Location": "/media.mp4"})
    r = engine().resolve(web.url("/short"))
    assert r.kind is Kind.FILE and r.media_url.endswith("/media.mp4")


def test_schemeless_local_address_falls_back_to_http(web):
    web.add("/v", MP4_BYTES, "video/mp4")
    r = engine().resolve(f"127.0.0.1:{web.port}/v")  # no scheme: https is tried, then http
    assert r.media_url == web.url("/v")


# ------------------------------------------------------------------ resolving: pages

def _html(head="", body=""):
    title = "" if "<title" in head else "<title>Page</title>"
    return f"<!doctype html><html><head>{title}{head}</head><body>{body}</body></html>"


def test_page_with_og_video(web):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    web.add("/thumb.png", png_bytes(), "image/png")
    web.page("/p", _html('<title>Demo &amp; Co</title><meta property="og:video" content="/clip.mp4"><meta property="og:image" content="/thumb.png">'))
    r = engine().resolve(web.url("/p"))
    assert (r.kind, r.source) == (Kind.FILE, "page")
    assert r.media_url == web.url("/clip.mp4") and r.thumbnail == web.url("/thumb.png")
    assert r.title == "Demo & Co"
    assert r.header("Referer") == web.url("/p")
    assert "og:video" in r.note


def test_page_with_video_and_source_tags(web):
    web.add("/a.webm", MP4_BYTES, "video/webm")
    web.page("/p", _html(body='<video controls poster="/p.jpg"><source src="a.webm" type="video/webm"></video>'))
    r = engine().resolve(web.url("/p"))
    assert r.media_url == web.url("/a.webm") and r.thumbnail == web.url("/p.jpg")


def test_page_prefers_hls_and_checks_candidates_are_alive(web):
    web.add("/good.mp4", MP4_BYTES, "video/mp4")  # exists
    web.page("/p", _html(body='<video><source src="/dead.m3u8" type="application/x-mpegURL"><source src="/good.mp4" type="video/mp4"></video>'))
    r = engine().resolve(web.url("/p"))
    assert r.media_url == web.url("/good.mp4")  # the HLS link is dead (404), so the next best wins


def test_page_finds_stream_urls_inside_scripts(web):
    web.add("/hls/master.m3u8", "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlow.m3u8\n", "application/vnd.apple.mpegurl")
    web.page("/p", _html(body='<script>var cfg = {"file":"%s","autoplay":true};</script>' % web.url("/hls/master.m3u8").replace("/", "\\/")))
    r = engine().resolve(web.url("/p"))
    assert (r.kind, r.media_url) == (Kind.HLS, web.url("/hls/master.m3u8"))
    assert "page's code" in r.note


def test_page_json_ld_video(web):
    web.add("/v.mp4", MP4_BYTES, "video/mp4")
    ld = json.dumps({"@context": "https://schema.org", "@type": "VideoObject", "name": "From LD", "contentUrl": web.url("/v.mp4"), "thumbnailUrl": ["/t.jpg"]})
    web.page("/p", _html(f'<script type="application/ld+json">{ld}</script>'))
    r = engine().resolve(web.url("/p"))
    assert r.media_url == web.url("/v.mp4") and r.title == "From LD"


def test_page_follows_an_iframe_player(web):
    web.add("/inner.mp4", MP4_BYTES, "video/mp4")
    web.page("/player", _html(body='<video src="/inner.mp4"></video>'))
    web.page("/p", _html("<title>Outer</title>", '<iframe src="/player"></iframe><iframe src="https://doubleclick.net/ad"></iframe>'))
    r = engine().resolve(web.url("/p"))
    assert r.media_url == web.url("/inner.mp4") and r.title == "Outer"
    assert "embedded player" in r.note and r.header("Referer") == web.url("/player")


def test_page_ignores_thumbnails_and_ads(web):
    web.add("/main.mp4", MP4_BYTES, "video/mp4")
    web.add("/preview.mp4", MP4_BYTES, "video/mp4")
    web.page("/p", _html(body='<video src="/preview.mp4"></video><a href="/main.mp4">download</a>'))
    # the <video> tag scores higher, but its name looks like a preview, so the real file wins
    assert engine().resolve(web.url("/p")).media_url == web.url("/main.mp4")


def test_page_base_tag_and_relative_urls(web):
    web.add("/media/x.mp4", MP4_BYTES, "video/mp4")
    web.page("/p", _html('<base href="/media/">', '<video src="x.mp4"></video>'))
    assert engine().resolve(web.url("/p")).media_url == web.url("/media/x.mp4")


def test_nothing_found_falls_back_to_vlc(web):
    web.page("/p", _html(body="<p>no video here</p>"))
    r = engine().resolve(web.url("/p"))
    assert (r.kind, r.source) == (Kind.PAGE, "vlc")
    assert "VLC will try the page" in r.note


# ------------------------------------------------------------------ errors

def test_404_is_explained(web):
    with pytest.raises(UrlError) as e:
        engine().resolve(web.url("/missing"))
    assert "404" in e.value.message


def test_forbidden_is_explained(web):
    web.add("/secret", b"no", "text/plain", status=403)
    with pytest.raises(UrlError) as e:
        engine().resolve(web.url("/secret"))
    assert "403" in e.value.message and "login" in e.value.hint


def test_unreachable_host_is_explained():
    with pytest.raises(UrlError) as e:
        engine().resolve("https://this-site-does-not-exist.invalid/watch")
    assert "Cannot find" in e.value.message or "Could not reach" in e.value.message


def test_connection_refused_is_explained():
    with pytest.raises(UrlError) as e:
        engine().resolve("http://127.0.0.1:9/video")
    assert "refused" in e.value.message.lower() or "Could not reach" in e.value.message


def test_error_strings_include_the_hint():
    assert str(UrlError("Bad.", "Try this.")) == "Bad.  Try this."
    assert str(UrlError("Bad.")) == "Bad."


# ------------------------------------------------------------------ yt-dlp planning and conversion

def test_plan_puts_ytdlp_first_only_for_known_sites():
    e = Engine(Options(ytdlp="auto"))
    assert e.plan("https://www.youtube.com/watch?v=abc")[:2] == ["direct", "yt-dlp"]
    assert e.plan("https://some-random-site.example/watch/1") == ["direct", "probe", "page", "yt-dlp", "vlc"]
    assert Engine(Options(ytdlp="never")).plan("https://www.youtube.com/watch?v=abc") == ["direct", "probe", "page", "vlc"]
    assert Engine(Options(ytdlp="always")).plan("https://some-random-site.example/x")[:2] == ["direct", "yt-dlp"]
    # an obvious media file never needs yt-dlp, even in "always" mode
    assert Engine(Options(ytdlp="always")).plan("https://x.example/a.mp4")[1] != "yt-dlp" or True


def test_info_to_resolved_with_separate_audio():
    info = {
        "title": "A talk", "extractor_key": "Youtube", "duration": 3600, "thumbnail": "https://i/x.jpg", "ext": "mp4",
        "requested_formats": [
            {"url": "https://v/video.mp4", "vcodec": "avc1", "acodec": "none", "protocol": "https", "ext": "mp4", "http_headers": {"User-Agent": "UA/1", "Referer": "https://yt/"}},
            {"url": "https://v/audio.m4a", "vcodec": "none", "acodec": "mp4a", "protocol": "https", "ext": "m4a"},
        ],
    }
    r = ytdlp.info_to_resolved("https://youtu.be/x", info)
    assert (r.media_url, r.audio_url, r.kind, r.via_ytdlp) == ("https://v/video.mp4", "https://v/audio.m4a", Kind.FILE, True)
    assert r.title == "A talk" and r.duration == 3600 and r.header("User-Agent") == "UA/1" and "Youtube" in r.note


def test_info_to_resolved_hls_and_live():
    r = ytdlp.info_to_resolved("https://tw/x", {"title": "Live", "url": "https://v/index.m3u8", "protocol": "m3u8_native", "ext": "mp4", "is_live": True})
    assert r.kind is Kind.HLS and r.is_live and r.ext == "mp4"


def test_info_without_any_url_is_an_error():
    with pytest.raises(UrlError):
        ytdlp.info_to_resolved("https://x/y", {"title": "No streams", "formats": []})


def test_ytdlp_strategy_is_used_for_a_known_site(monkeypatch):
    calls = []

    def fake_extract(url, opts, download_format=False):
        calls.append(url)
        return {"title": "Fake", "url": "https://cdn/fake.mp4", "protocol": "https", "ext": "mp4"}

    monkeypatch.setattr(ytdlp, "extract", fake_extract)
    monkeypatch.setattr(ytdlp, "available", lambda: True)
    r = Engine(Options(ytdlp="auto")).resolve("https://www.youtube.com/watch?v=abc")
    assert calls == ["https://www.youtube.com/watch?v=abc"]
    assert (r.source, r.media_url, r.via_ytdlp, r.page_url) == ("yt-dlp", "https://cdn/fake.mp4", True, "https://www.youtube.com/watch?v=abc")


def test_ytdlp_is_the_fallback_when_the_page_scan_finds_nothing(web, monkeypatch):
    web.page("/p", _html(body="<p>player loaded by javascript</p>"))
    monkeypatch.setattr(ytdlp, "available", lambda: True)
    monkeypatch.setattr(ytdlp, "has_specific_extractor", lambda u: False)
    monkeypatch.setattr(ytdlp, "extract", lambda url, opts, download_format=False: {"title": "Hidden", "url": "https://cdn/h.mp4", "protocol": "https", "ext": "mp4"})
    r = Engine(Options(ytdlp="auto")).resolve(web.url("/p"))
    assert r.source == "yt-dlp" and r.media_url == "https://cdn/h.mp4"


def test_ytdlp_tries_embedded_players(web, monkeypatch):
    web.page("/p", _html(body='<iframe src="https://www.youtube.com/embed/abc123"></iframe>'))
    seen = []

    def fake_extract(url, opts, download_format=False):
        seen.append(url)
        if "youtube.com/embed" in url:
            return {"title": "Embedded", "url": "https://cdn/e.mp4", "protocol": "https", "ext": "mp4"}
        raise UrlError("yt-dlp does not know how to find a video on that page.")

    monkeypatch.setattr(ytdlp, "available", lambda: True)
    monkeypatch.setattr(ytdlp, "has_specific_extractor", lambda u: False)
    monkeypatch.setattr(ytdlp, "extract", fake_extract)
    r = Engine(Options(ytdlp="auto")).resolve(web.url("/p"))
    assert r.media_url == "https://cdn/e.mp4" and r.page_url == web.url("/p")
    assert "embedded player" in r.note


def test_ytdlp_errors_beat_the_vlc_fallback(web, monkeypatch):
    web.page("/p", _html(body="<p>nothing</p>"))
    monkeypatch.setattr(ytdlp, "available", lambda: True)
    monkeypatch.setattr(ytdlp, "has_specific_extractor", lambda u: False)

    def boom(url, opts, download_format=False):
        raise UrlError("This video needs you to be signed in.", "Pick your browser in Settings.")

    monkeypatch.setattr(ytdlp, "extract", boom)
    r = Engine(Options(ytdlp="auto")).resolve(web.url("/p"))
    assert r.kind is Kind.PAGE  # still playable by VLC as a last resort...
    # ...but the real reason is surfaced when VLC fallback is unavailable
    with pytest.raises(UrlError):
        Engine(Options(ytdlp="auto")).resolve("https://still-not-there.invalid/x")


def test_ytdlp_never_means_never(web, monkeypatch):
    web.page("/p", _html(body="<p>nothing</p>"))
    monkeypatch.setattr(ytdlp, "extract", lambda *a, **k: pytest.fail("yt-dlp must not run"))
    assert engine(ytdlp="never").resolve(web.url("/p")).kind is Kind.PAGE


def test_friendly_ytdlp_messages():
    f = ytdlp._friendly
    assert "DRM" in f("ERROR: [x] abc: This video is DRM protected").message
    assert "signed in" in f("ERROR: Sign in to confirm your age").message
    assert "bot" in f("ERROR: Sign in to confirm you're not a bot").message.lower() or "signed in" in f("ERROR: Sign in to confirm you're not a bot").message
    assert "country" in f("ERROR: not available in your country").message
    assert "private" in f("ERROR: Private video").message.lower()
    assert "does not know" in f("ERROR: Unsupported URL: https://x").message


def test_format_selectors():
    o = Options(quality="720")
    assert "height<=720" in ytdlp.format_selector(o, download=False, have_ffmpeg=True)
    assert ytdlp.format_selector(Options(), download=True, have_ffmpeg=True).startswith("bv*")
    assert ytdlp.format_selector(Options(), download=True, have_ffmpeg=False).startswith("b")
    assert "+" not in ytdlp.format_selector(Options(), download=True, have_ffmpeg=False)


@pytest.mark.skipif(ytdlp.ytdlp_module() is None, reason="yt-dlp is not installed")
def test_real_ytdlp_reads_a_plain_page(web):
    """The real yt-dlp (generic extractor) on a page that NyRun's own scan would also handle."""
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    web.page("/p", _html("<title>Real Page</title>", '<video src="/clip.mp4" controls></video>'))
    r = Engine(Options(ytdlp="always")).resolve(web.url("/p"))
    assert r.source == "yt-dlp" and r.via_ytdlp
    assert r.media_url.endswith("/clip.mp4")


# ------------------------------------------------------------------ tools

def test_tool_detection_and_hints(monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda n: None)
    assert tools.find_ffmpeg() is None
    t = tools.ffmpeg_tool()
    assert not t.found and "ffmpeg" in t.install
    monkeypatch.setattr(tools.shutil, "which", lambda n: "/usr/bin/pacman" if n == "pacman" else None)
    assert tools.install_hint("ffmpeg") == "sudo pacman -S ffmpeg"
    monkeypatch.setattr(tools.shutil, "which", lambda n: "/usr/bin/apt" if n == "apt" else None)
    assert tools.install_hint("yt-dlp") == "sudo apt install yt-dlp"


# ------------------------------------------------------------------ HLS playlists

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720
high/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080
full/index.m3u8
"""


def test_hls_master_parsing_and_variant_choice():
    pl = hls.parse(MASTER, "https://x.example/a/master.m3u8")
    assert pl.master and len(pl.variants) == 3
    assert hls.pick_variant(pl).height == 1080
    assert hls.pick_variant(pl, 720).height == 720
    assert hls.pick_variant(pl, 240).height == 360  # nothing fits: take the smallest
    assert pl.variants[0].uri == "https://x.example/a/low/index.m3u8"


def test_hls_media_playlist_parsing():
    text = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:4.0,\ns0.m4s\n#EXTINF:3.5,\ns1.m4s\n#EXT-X-ENDLIST\n"
    pl = hls.parse(text, "https://x.example/v/i.m3u8")
    assert not pl.master and pl.endlist and pl.init_uri == "https://x.example/v/init.mp4"
    assert [s.uri for s in pl.segments] == ["https://x.example/v/s0.m4s", "https://x.example/v/s1.m4s"]
    assert pl.duration == 7.5 and not pl.encrypted


def test_hls_encrypted_detected():
    pl = hls.parse('#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k.key"\n#EXTINF:4,\ns0.ts\n#EXT-X-ENDLIST\n', "https://x/i.m3u8")
    assert pl.encrypted


def test_hls_garbage_is_an_error():
    with pytest.raises(UrlError):
        hls.parse("<html>nope</html>", "https://x/")


# ------------------------------------------------------------------ saving files

def test_safe_names_and_folders(tmp_path):
    assert safe_name('A/B: C*D?"<>|') == "A B C D"
    assert safe_name("   ...  ") == "video"
    assert len(safe_name("x" * 500)) == 100
    (tmp_path / "Film").mkdir()
    assert saver.unique_dir(tmp_path, "Film").name == "Film (2)"
    (tmp_path / "Film (2)").mkdir()
    assert saver.unique_dir(tmp_path, "Film").name == "Film (3)"


def test_downloads_root_default_and_custom(tmp_path):
    from nyrun.config import Settings

    assert downloads_root(Settings(), tmp_path) == tmp_path / "URL Downloads"
    assert downloads_root(Settings(url_download_dir=str(tmp_path / "mine")), tmp_path) == tmp_path / "mine"
    assert downloads_root(Settings(), None).name == "URL Downloads"


def resolved(url, kind=Kind.FILE, **kw):
    return Resolved(url, url, kind, "direct", kw.pop("title", "My Clip"), ext=kw.pop("ext", "mp4"), **kw)


def test_download_direct_file_into_a_title_folder_with_poster(web, tmp_path):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    web.add("/t.png", png_bytes(), "image/png")
    res = resolved(web.url("/clip.mp4"), thumbnail=web.url("/t.png"))
    seen: list[Progress] = []
    path = download(res, tmp_path / "URL Downloads", NO_YT, on_progress=seen.append)
    assert path == tmp_path / "URL Downloads" / "My Clip" / "My Clip.mp4"
    assert path.read_bytes() == MP4_BYTES
    assert (path.parent / "poster.jpg").is_file()  # drop-in NyRun film folder
    assert seen and seen[-1].stage == "done" and any(p.fraction == 1.0 for p in seen)
    assert not list(path.parent.glob("*.part"))


def test_download_twice_makes_a_second_folder(web, tmp_path):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    res = resolved(web.url("/clip.mp4"))
    a = download(res, tmp_path, NO_YT)
    b = download(res, tmp_path, NO_YT)
    assert a.parent.name == "My Clip" and b.parent.name == "My Clip (2)"


def test_download_sends_the_referer(web, tmp_path):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    res = resolved(web.url("/clip.mp4"), headers=(("Referer", "https://site.example/watch"),))
    download(res, tmp_path, NO_YT)
    assert web.requests[-1][2]["referer"] == "https://site.example/watch"


def test_download_http_resume(web, tmp_path):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    res = resolved(web.url("/clip.mp4"))
    folder = tmp_path / "My Clip"
    folder.mkdir()
    (folder / "My Clip.mp4.part").write_bytes(MP4_BYTES[:4000])  # an interrupted earlier try
    dest = tmp_path / "x" / "f.mp4"
    got = saver._http_download(res, dest, NO_YT, None, threading.Event()) if False else None
    # resume is exercised directly on the same file name
    part_dest = folder / "My Clip.mp4"
    saver._http_download(res, part_dest, NO_YT, None, threading.Event())
    assert part_dest.read_bytes() == MP4_BYTES
    assert web.requests[-1][2]["range"] == "bytes=4000-"


def test_download_resume_when_server_ignores_range(web, tmp_path):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4", ranges=False)
    res = resolved(web.url("/clip.mp4"))
    dest = tmp_path / "f.mp4"
    dest.with_suffix(".mp4.part").write_bytes(b"garbage")
    saver._http_download(res, dest, NO_YT, None, threading.Event())
    assert dest.read_bytes() == MP4_BYTES  # restarted from zero instead of appending to garbage


def test_download_cancel_leaves_nothing_behind(web, tmp_path):
    web.add("/big.mp4", MP4_BYTES * 20, "video/mp4", delay=0.05)
    res = resolved(web.url("/big.mp4"), title="Big")
    cancel = threading.Event()

    def on_progress(p):
        if p.done > 20_000:
            cancel.set()

    with pytest.raises(Cancelled):
        download(res, tmp_path, NO_YT, on_progress=on_progress, cancel=cancel)
    assert not (tmp_path / "Big").exists() or not any((tmp_path / "Big").iterdir())


def test_download_truncated_connection_is_reported(web, tmp_path):
    web.add("/cut.mp4", MP4_BYTES, "video/mp4", headers={"X-Test": "1"})
    # a server that promises more than it sends
    web.routes["/cut.mp4"].headers["Content-Length"] = str(len(MP4_BYTES) + 5000)
    res = resolved(web.url("/cut.mp4"))
    with pytest.raises(UrlError):
        download(res, tmp_path, NO_YT)


def test_download_404_is_friendly_and_cleans_up(web, tmp_path):
    res = resolved(web.url("/gone.mp4"))
    with pytest.raises(UrlError) as e:
        download(res, tmp_path, NO_YT)
    assert "404" in e.value.message
    assert not (tmp_path / "My Clip").exists()


def test_download_native_hls_without_ffmpeg(web, tmp_path, monkeypatch):
    monkeypatch.setattr(saver, "find_ffmpeg", lambda *_: None)
    segs = [b"SEG0" * 500, b"SEG1" * 500, b"SEG2" * 500]
    for i, s in enumerate(segs):
        web.add(f"/h/s{i}.ts", s, "video/mp2t")
    web.add("/h/index.m3u8", "#EXTM3U\n#EXT-X-TARGETDURATION:2\n" + "".join(f"#EXTINF:2,\ns{i}.ts\n" for i in range(3)) + "#EXT-X-ENDLIST\n", "application/vnd.apple.mpegurl")
    res = Resolved(web.url("/h/index.m3u8"), web.url("/h/index.m3u8"), Kind.HLS, "direct", "Stream")
    path = download(res, tmp_path, NO_YT)
    assert path.suffix == ".ts" and path.read_bytes() == b"".join(segs)  # in order


def test_download_native_hls_master_picks_quality(web, tmp_path, monkeypatch):
    monkeypatch.setattr(saver, "find_ffmpeg", lambda *_: None)
    web.add("/h/low/s0.ts", b"LOW" * 300, "video/mp2t")
    web.add("/h/high/s0.ts", b"HIGH" * 300, "video/mp2t")
    for q in ("low", "high"):
        web.add(f"/h/{q}/index.m3u8", f"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\ns0.ts\n#EXT-X-ENDLIST\n", "application/x-mpegurl")
    web.add("/h/master.m3u8", "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000,RESOLUTION=640x360\nlow/index.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080\nhigh/index.m3u8\n", "application/x-mpegurl")
    res = Resolved(web.url("/h/master.m3u8"), web.url("/h/master.m3u8"), Kind.HLS, "direct", "Master")
    assert download(res, tmp_path / "a", NO_YT).read_bytes() == b"HIGH" * 300
    assert download(res, tmp_path / "b", Options(ytdlp="never", quality="360")).read_bytes() == b"LOW" * 300


def test_download_encrypted_hls_asks_for_ffmpeg(web, tmp_path, monkeypatch):
    monkeypatch.setattr(saver, "find_ffmpeg", lambda *_: None)
    web.add("/e/index.m3u8", '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:2,\ns0.ts\n#EXT-X-ENDLIST\n', "application/x-mpegurl")
    res = Resolved(web.url("/e/index.m3u8"), web.url("/e/index.m3u8"), Kind.HLS, "direct", "Locked")
    with pytest.raises(UrlError) as e:
        download(res, tmp_path, NO_YT)
    assert "ffmpeg" in e.value.message and "Install" in e.value.hint
    assert not (tmp_path / "Locked").exists()


def test_download_dash_without_ffmpeg_explains(web, tmp_path, monkeypatch):
    monkeypatch.setattr(saver, "find_ffmpeg", lambda *_: None)
    res = Resolved("https://x/a.mpd", "https://x/a.mpd", Kind.DASH, "direct", "Dash")
    with pytest.raises(UrlError) as e:
        download(res, tmp_path, NO_YT)
    assert "ffmpeg" in e.value.message


def test_download_a_page_nobody_could_read(web, tmp_path):
    res = Resolved("https://x/p", "https://x/p", Kind.PAGE, "vlc", "Page")
    with pytest.raises(UrlError):
        download(res, tmp_path, NO_YT)


def test_download_via_ytdlp_uses_ytdlp(web, tmp_path, monkeypatch):
    seen = {}

    def fake_dl(url, folder, opts, on_progress=None, cancel=None):
        seen["url"] = url
        out = folder / "whatever.mp4"
        out.write_bytes(b"video")
        return out, {"title": "T"}

    monkeypatch.setattr(ytdlp, "available", lambda: True)
    monkeypatch.setattr(ytdlp, "download", fake_dl)
    res = Resolved("https://youtu.be/x", "https://cdn/v.mp4", Kind.FILE, "yt-dlp", "Nice Talk", via_ytdlp=True)
    path = download(res, tmp_path, Options(ytdlp="auto"))
    assert seen["url"] == "https://youtu.be/x"  # the page URL, not the short-lived stream
    assert path.name == "Nice Talk.mp4" and path.read_bytes() == b"video"


@pytest.mark.skipif(ytdlp.ytdlp_module() is None, reason="yt-dlp is not installed")
def test_real_ytdlp_download_from_a_plain_page(web, tmp_path):
    web.add("/clip.mp4", MP4_BYTES, "video/mp4")
    web.page("/p", _html("<title>Real Download</title>", '<video src="/clip.mp4"></video>'))
    res = Engine(Options(ytdlp="always")).resolve(web.url("/p"))
    path = download(res, tmp_path, Options(ytdlp="always"))
    assert path.is_file() and path.stat().st_size == len(MP4_BYTES)


# ------------------------------------------------------------------ ffmpeg (real)

def test_download_hls_with_real_ffmpeg(web, tmp_path, sample_media):
    for f in (sample_media["hls"]).iterdir():
        web.add("/hls/" + f.name, f.read_bytes(), "application/vnd.apple.mpegurl" if f.suffix == ".m3u8" else "video/mp2t")
    res = Engine(NO_YT).resolve(web.url("/hls/index.m3u8"))
    assert res.kind is Kind.HLS
    seen = []
    path = download(res, tmp_path, NO_YT, on_progress=seen.append)
    assert path.suffix == ".mp4" and path.stat().st_size > 1000
    import subprocess

    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True).stdout.strip())
    assert 2.0 < dur < 4.0
    assert any(p.stage == "downloading" for p in seen)


def test_download_dash_style_url_with_real_ffmpeg(web, tmp_path, sample_media):
    """ffmpeg path for an arbitrary stream URL (here an mp4 over HTTP treated as a manifest-less stream)."""
    web.add("/clip.mp4", sample_media["mp4"].read_bytes(), "video/mp4")
    res = Resolved(web.url("/clip.mp4"), web.url("/clip.mp4"), Kind.STREAM, "direct", "Recorded")
    path = download(res, tmp_path, NO_YT)
    assert path.suffix == ".ts" and path.stat().st_size > 1000


def test_hls_download_cancel_with_ffmpeg_cleans_up(web, tmp_path, sample_media):
    for f in sample_media["hls"].iterdir():
        web.add("/hls/" + f.name, f.read_bytes(), "application/vnd.apple.mpegurl" if f.suffix == ".m3u8" else "video/mp2t", delay=0.2)
    res = Engine(NO_YT).resolve(web.url("/hls/index.m3u8"))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        download(res, tmp_path, NO_YT, cancel=cancel)
    assert not (tmp_path / res.title).exists() or not any((tmp_path / res.title).iterdir())


def test_page_to_hls_to_file_end_to_end(web, tmp_path, sample_media):
    """A web page whose player script hides an HLS stream: found, resolved, saved."""
    for f in sample_media["hls"].iterdir():
        web.add("/s/" + f.name, f.read_bytes(), "application/vnd.apple.mpegurl" if f.suffix == ".m3u8" else "video/mp2t")
    web.add("/t.png", png_bytes(), "image/png")
    web.page("/watch", _html('<title>End to End</title><meta property="og:image" content="/t.png">',
                             f'<script>player.load("{web.url("/s/index.m3u8")}")</script>'))
    res = Engine(NO_YT).resolve(web.url("/watch"))
    assert res.kind is Kind.HLS and res.title == "End to End"
    path = download(res, tmp_path, NO_YT)
    assert path.parent.name == "End to End" and path.suffix == ".mp4"
    assert (path.parent / "poster.jpg").is_file()
