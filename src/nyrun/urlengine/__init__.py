"""The NyRun URL engine.

    from nyrun.urlengine import Engine, Options
    res = Engine(Options()).resolve("https://example.com/watch/42")
    # res.media_url is what VLC opens; res.kind says what it is; res.note says how it was found

Pipeline (each step only runs if the one before could not answer):

  1. direct   the address says it all (.mp4 / .m3u8 / .mpd / rtsp://...)
  2. probe    ask the server what it is; keep the page if it is HTML
  3. page     scan the HTML for <video>, og:video, JSON-LD, players in iframes, URLs in scripts
  4. yt-dlp   only when needed: first for sites it has a dedicated extractor for, last for the rest
  5. vlc      hand the page to VLC and hope

Add a strategy with `strategies.register` and put its name in `Engine.order`.
"""

from __future__ import annotations

from .detect import normalize_url
from .engine import Engine, resolve
from .saver import DOWNLOADS_FOLDER_NAME, download, downloads_root, safe_name
from .model import Cancelled, Kind, Options, Progress, Resolved, UrlError
from .tools import Tool, ffmpeg_tool, find_ffmpeg, install_hint, ytdlp_tool

__all__ = [
    "Cancelled", "Engine", "Kind", "Options", "Progress", "Resolved", "Tool", "UrlError",
    "DOWNLOADS_FOLDER_NAME", "download", "downloads_root", "ffmpeg_tool", "find_ffmpeg", "install_hint",
    "normalize_url", "resolve", "safe_name", "ytdlp_tool",
]
