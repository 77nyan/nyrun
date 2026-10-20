"""Finding video and audio links inside a web page.

Pages hide their media in many ways. This module looks in all the common places: the Open Graph and
Twitter meta tags, <video>/<audio>/<source> tags, JSON-LD, <link rel=alternate>, <iframe> embeds, and
finally any media-looking URL written anywhere in the page text (including inside scripts).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from .detect import kind_from_url
from .model import Candidate, Kind

# URL written anywhere in text (also with JSON-escaped slashes) that ends in a media extension
_MEDIA_URL = re.compile(
    r"""(?:https?:)?(?:\\?/\\?/)(?:[^\s"'<>\\()]|\\/)+?\.(?:m3u8|mpd|mp4|m4v|webm|mkv|mov|mp3|m4a|aac|ogg|opus|flac)(?:\?(?:[^\s"'<>\\()]|\\/|\\u0026)*)?""",
    re.I,
)
_SKIP_WORDS = re.compile(r"(thumb|preview|sprite|poster|tracking|pixel|analytics|adserver|doubleclick|/ads?/|[_\-.]ad[_\-.]|trailer|teaser|\.gif|storyboard|beacon)", re.I)


def _unescape(url: str) -> str:
    url = html.unescape(url)
    url = url.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u002F", "/")
    return url.strip()


@dataclass
class PageInfo:
    title: str = ""
    thumbnail: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    iframes: list[str] = field(default_factory=list)
    embeds: list[str] = field(default_factory=list)  # page-style URLs a downloader like yt-dlp may understand
    subtitles: list[str] = field(default_factory=list)


class _Scan(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.info = PageInfo()
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_ldjson = False
        self._ld: list[str] = []
        self.meta: dict[str, list[str]] = {}
        self._video_depth = 0
        self._video_src = ""

    # ----- helpers -----
    def _abs(self, url: str) -> str:
        return urljoin(self.base, _unescape(url))

    def _add(self, url: str, score: int, why: str, kind: Kind | None = None) -> None:
        if not url or url.startswith(("data:", "blob:", "javascript:")):
            return
        full = self._abs(url)
        k = kind or kind_from_url(full)
        if k is None:
            return
        self.info.candidates.append(Candidate(full, k, score, why))

    # ----- parser callbacks -----
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        elif tag == "title" and not self._title_parts:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            val = a.get("content", "")
            if key and val:
                self.meta.setdefault(key, []).append(val)
        elif tag in ("video", "audio"):
            self._video_depth += 1
            for attr in ("src", "data-src", "data-video-src", "data-hls", "data-stream", "data-url"):
                if a.get(attr):
                    self._add(a[attr], 95 if tag == "video" else 85, f"<{tag}> tag", Kind.AUDIO if tag == "audio" and not kind_from_url(self._abs(a[attr])) else None)
            if a.get("poster") and not self.info.thumbnail:
                self.info.thumbnail = self._abs(a["poster"])
        elif tag == "source":
            src = a.get("src") or a.get("data-src")
            if src:
                typ = a.get("type", "").lower()
                kind = None
                if "mpegurl" in typ:
                    kind = Kind.HLS
                elif "dash" in typ:
                    kind = Kind.DASH
                elif typ.startswith("video/"):
                    kind = kind_from_url(self._abs(src)) or Kind.FILE
                elif typ.startswith("audio/"):
                    kind = Kind.AUDIO
                self._add(src, 95 if self._video_depth else 70, "<source> tag", kind)
        elif tag == "track" and a.get("src") and a.get("kind", "subtitles") in ("subtitles", "captions"):
            self.info.subtitles.append(self._abs(a["src"]))
        elif tag == "link":
            rel = a.get("rel", "").lower()
            typ = a.get("type", "").lower()
            if rel == "alternate" and ("mpegurl" in typ or "dash" in typ) and a.get("href"):
                self._add(a["href"], 88, "<link rel=alternate>", Kind.HLS if "mpegurl" in typ else Kind.DASH)
            elif rel == "image_src" and a.get("href") and not self.info.thumbnail:
                self.info.thumbnail = self._abs(a["href"])
        elif tag in ("iframe", "embed"):
            src = a.get("src") or a.get("data-src") or ""
            if src and not src.startswith(("about:", "javascript:", "data:")):
                self.info.iframes.append(self._abs(src))
        elif tag == "a":
            href = a.get("href", "")
            if href and kind_from_url(self._abs(href)) in (Kind.FILE, Kind.HLS, Kind.DASH):
                self._add(href, 55, "download link")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ldjson = True
            self._ld = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("video", "audio"):
            self._video_depth = max(0, self._video_depth - 1)
        elif tag == "script" and self._in_ldjson:
            self._in_ldjson = False
            self._read_ldjson("".join(self._ld))

    def handle_data(self, data):
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_ldjson:
            self._ld.append(data)

    # ----- JSON-LD -----
    def _read_ldjson(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except ValueError:
            return
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                typ = d.get("@type")
                typs = typ if isinstance(typ, list) else [typ]
                if "VideoObject" in typs or "AudioObject" in typs:
                    for key in ("contentUrl", "url"):
                        v = d.get(key)
                        if isinstance(v, str) and kind_from_url(self._abs(v)):
                            self._add(v, 92, "JSON-LD video")
                    emb = d.get("embedUrl")
                    if isinstance(emb, str):
                        self.info.embeds.append(self._abs(emb))
                    if isinstance(d.get("name"), str) and not self.info.title:
                        self.info.title = d["name"].strip()
                    th = d.get("thumbnailUrl")
                    if isinstance(th, list) and th:
                        th = th[0]
                    if isinstance(th, str) and not self.info.thumbnail:
                        self.info.thumbnail = self._abs(th)
                stack.extend(v for v in d.values() if isinstance(v, (dict, list)))

    # ----- finish -----
    def finish(self) -> PageInfo:
        info = self.info
        m = self.meta
        for key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream", "og:audio", "og:audio:url", "og:audio:secure_url"):
            for v in m.get(key, []):
                score = 98 if "secure" in key or key.endswith("stream") else 96
                self._add(v, score, f"{key} tag", Kind.AUDIO if key.startswith("og:audio") and not kind_from_url(self._abs(v)) else None)
                full = self._abs(v)
                if kind_from_url(full) is None and key.startswith("og:video") and not full.lower().endswith((".jpg", ".png", ".gif")):
                    info.embeds.append(full)  # an embed/player page rather than a file
        for v in m.get("twitter:player", []):
            info.embeds.append(self._abs(v))
        title = (m.get("og:title") or m.get("twitter:title") or [""])[0].strip()
        info.title = info.title or title or html.unescape("".join(self._title_parts)).strip()
        info.title = re.sub(r"\s+", " ", info.title)
        if not info.thumbnail:
            for key in ("og:image", "og:image:secure_url", "twitter:image", "twitter:image:src", "thumbnailurl"):
                if m.get(key):
                    info.thumbnail = self._abs(m[key][0])
                    break
        return info


def scan(html_text: str, base_url: str) -> PageInfo:
    """Everything media-related we can find in one page."""
    parser = _Scan(base_url)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001  (broken HTML must never stop the search)
        pass
    info = parser.finish()
    seen = {c.url for c in info.candidates}
    # last resort: media URLs written anywhere in the text, e.g. inside player scripts
    for m in _MEDIA_URL.finditer(html_text):
        url = _unescape(m.group(0))
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/") and not url.startswith("//"):
            url = urljoin(base_url, url)
        if url in seen:
            continue
        kind = kind_from_url(url)
        if kind is None:
            continue
        seen.add(url)
        info.candidates.append(Candidate(url, kind, 60, "found in the page's code"))
    for c in info.candidates:
        score = c.score
        if _SKIP_WORDS.search(c.url):
            score -= 70
        if c.kind is Kind.HLS:
            score += 8
        elif c.kind is Kind.DASH:
            score += 4
        elif c.kind is Kind.AUDIO and "audio" not in c.why and "og:" not in c.why:
            score -= 10
        c.score = score
    # de-duplicate (keep the best score per URL), best first
    best: dict[str, Candidate] = {}
    for c in info.candidates:
        if c.url not in best or c.score > best[c.url].score:
            best[c.url] = c
    info.candidates = sorted(best.values(), key=lambda c: -c.score)
    info.iframes = list(dict.fromkeys(info.iframes))
    info.embeds = list(dict.fromkeys(info.embeds))
    return info


_AD_FRAMES = re.compile(r"(doubleclick|googlesyndication|adservice|adsystem|facebook\.com/plugins|googletagmanager|/ads?/|analytics|recaptcha|disqus|twitter\.com/widgets)", re.I)


def worthwhile_iframes(info: PageInfo, limit: int) -> list[str]:
    """Embedded frames likely to hold a player (ad and widget frames are skipped)."""
    out = [u for u in info.iframes if urlsplit(u).scheme in ("http", "https") and not _AD_FRAMES.search(u)]
    # players first
    out.sort(key=lambda u: 0 if re.search(r"(player|embed|video|stream|watch|play)", u, re.I) else 1)
    return out[:limit]
