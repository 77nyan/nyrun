"""Data types shared by every part of the URL engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Kind(str, Enum):
    """What the thing behind a URL is."""

    FILE = "video file"
    AUDIO = "audio file"
    HLS = "HLS stream"
    DASH = "DASH stream"
    STREAM = "live stream"
    PLAYLIST = "playlist"
    PAGE = "web page"  # nothing found: VLC gets the page itself and tries its own luck

    @property
    def is_manifest(self) -> bool:
        return self in (Kind.HLS, Kind.DASH)


class UrlError(Exception):
    """Something went wrong that the user can understand. `hint` says what to try next."""

    def __init__(self, message: str, hint: str = "", *, try_http: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.try_http = try_http  # an https attempt failed in a way that plain http might not

    def __str__(self) -> str:  # what shows up in notifications
        return f"{self.message}  {self.hint}".strip() if self.hint else self.message


class Cancelled(Exception):
    """The user stopped a download."""


@dataclass(frozen=True)
class Resolved:
    """A URL turned into something VLC (and the downloader) can use."""

    page_url: str  # what the user typed (normalised)
    media_url: str  # what VLC should open
    kind: Kind
    source: str  # which strategy found it: direct | probe | page | yt-dlp | vlc
    title: str = ""
    audio_url: str = ""  # separate audio track (VLC gets it with --input-slave)
    headers: tuple[tuple[str, str], ...] = ()  # e.g. (("Referer", "https://site/"),)
    thumbnail: str = ""
    duration: float | None = None
    is_live: bool = False
    note: str = ""  # one friendly line about how it was found (or why it may fail)
    ext: str = ""  # best guess at the file extension (mp4, mkv, mp3, ...)
    via_ytdlp: bool = False  # yt-dlp resolved it, so yt-dlp can also download it
    cookies: bool = False  # the address only works with cookies yt-dlp holds (Google Drive...): VLC cannot open it alone

    def header(self, name: str) -> str:
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return ""

    @property
    def needs_relay(self) -> bool:
        """Some servers answer VLC with errors (YouTube: HTTP 429/403) or need cookies only yt-dlp has (Google Drive),
        so yt-dlp has to fetch the video and hand it to VLC."""
        if not self.via_ytdlp or self.is_live:
            return False
        if self.cookies:
            return True
        from urllib.parse import urlsplit

        hosts = [(urlsplit(u).hostname or "").lower() for u in (self.media_url, self.page_url)]
        return any(h.endswith(("googlevideo.com", "youtube.com", "youtu.be", "youtube-nocookie.com", "drive.google.com", "docs.google.com", "drive.usercontent.google.com")) for h in hosts)

    @property
    def key(self) -> str:
        """State key for resume positions and history."""
        return "url:" + self.page_url

    @property
    def label(self) -> str:
        return self.title or self.media_url


@dataclass
class Options:
    """What the engine needs to know from the settings (kept separate so it is easy to test)."""

    ytdlp: str = "auto"  # auto | never | always
    quality: str = "auto"  # auto | 1080 | 720 | 480
    cookies_browser: str = "none"
    ffmpeg: str = ""  # custom path, empty = look in PATH
    timeout: float = 15.0
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
    max_iframes: int = 3

    @classmethod
    def from_settings(cls, s) -> "Options":
        return cls(
            ytdlp=getattr(s, "url_ytdlp", "auto"),
            quality=getattr(s, "url_quality", "auto"),
            cookies_browser=getattr(s, "url_cookies", "none"),
        )


@dataclass
class Progress:
    """One progress report from the downloader."""

    stage: str = ""  # connecting | downloading | merging | done
    done: int = 0  # bytes (0 when unknown)
    total: int = 0
    fraction: float | None = None  # None = unknown
    speed: float = 0.0  # bytes per second
    eta: float | None = None
    message: str = ""


@dataclass
class Candidate:
    """A possible media URL found on a page."""

    url: str
    kind: Kind
    score: int
    why: str = ""
    extra: dict = field(default_factory=dict)
