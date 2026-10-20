"""The ways NyRun can turn a URL into something VLC can play.

Each strategy is a small class with a `resolve(ctx)` method. Return a Resolved to finish, or None to
let the next strategy try. Add your own with the @register decorator - nothing else needs to change.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from . import detect, http, page, ytdlp
from .model import Kind, Options, Resolved, UrlError


@dataclass
class Context:
    """Everything strategies share while resolving one URL."""

    url: str
    opts: Options
    notes: list[str] = field(default_factory=list)
    probe: http.Response | None = None
    html: str = ""  # the page text, once some strategy has fetched it
    html_url: str = ""
    info: page.PageInfo | None = None
    last_error: UrlError | None = None  # the most useful failure seen so far
    tried_ytdlp: bool = False
    assumed_https: bool = False  # the user typed no scheme, so https:// was a guess

    def note(self, text: str) -> None:
        if text and text not in self.notes:
            self.notes.append(text)

    def fail(self, err: UrlError) -> None:
        if self.last_error is None:
            self.last_error = err


class Strategy:
    name = "base"

    def resolve(self, ctx: Context) -> Resolved | None:  # pragma: no cover - interface
        raise NotImplementedError


REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    """Class decorator: make a strategy available to the engine by its name."""
    REGISTRY[cls.name] = cls
    return cls


def _ext_for(kind: Kind, url: str, content_type: str = "") -> str:
    e = detect.extension_of(url)
    if kind in (Kind.FILE, Kind.AUDIO) and e in detect.VIDEO_EXTS | detect.AUDIO_EXTS:
        return e
    ct = content_type.split(";")[0].strip().lower()
    table = {"video/mp4": "mp4", "video/webm": "webm", "video/x-matroska": "mkv", "video/quicktime": "mov",
             "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/flac": "flac", "audio/aac": "aac",
             "audio/wav": "wav", "audio/x-wav": "wav", "video/mp2t": "ts", "video/ogg": "ogv"}
    if ct in table:
        return table[ct]
    return {Kind.HLS: "mp4", Kind.DASH: "mp4", Kind.AUDIO: "mp3", Kind.STREAM: "ts"}.get(kind, "mp4")


def _file_title(url: str) -> str:
    return detect.title_from_url(url)


@register
class Direct(Strategy):
    """The address itself says what it is: a media file, an HLS/DASH manifest, or a stream address."""

    name = "direct"

    def resolve(self, ctx: Context) -> Resolved | None:
        kind = detect.kind_from_url(ctx.url)
        if kind is None:
            return None
        note = {
            Kind.FILE: "direct link to a video file",
            Kind.AUDIO: "direct link to an audio file",
            Kind.HLS: "HLS stream (.m3u8)",
            Kind.DASH: "DASH stream (.mpd)",
            Kind.STREAM: "live stream address",
            Kind.PLAYLIST: "playlist file",
        }[kind]
        return Resolved(ctx.url, ctx.url, kind, self.name, _file_title(ctx.url), note=note, ext=_ext_for(kind, ctx.url))


@register
class Probe(Strategy):
    """No clue in the address: ask the server. Media answers become Resolved; HTML is kept for the page scan."""

    name = "probe"

    def resolve(self, ctx: Context) -> Resolved | None:
        try:
            r = http.probe(ctx.url, ctx.opts)
        except UrlError as e:
            if ctx.assumed_https and e.try_http and ctx.url.startswith("https://"):
                # "192.168.1.5:8080/video" style input: https was only a guess, try plain http
                ctx.url = "http://" + ctx.url[len("https://"):]
                try:
                    r = http.probe(ctx.url, ctx.opts)
                except UrlError as e2:
                    ctx.fail(e2)
                    return None
            else:
                ctx.fail(e)
                return None
        ctx.probe = r
        kind = detect.kind_from_content_type(r.content_type)
        head = r.body[:4096]
        by_bytes = detect.kind_from_bytes(head) if r.body else None
        if kind is None or (kind is Kind.FILE and by_bytes in (Kind.HLS, Kind.DASH)):
            kind = by_bytes if by_bytes is not None else kind
        if kind is not None and not (detect.looks_like_html(head, r.content_type) and by_bytes is None and kind is Kind.PLAYLIST):
            name = unquote(posixpath.basename(urlsplit(r.url).path))
            cd = r.headers.get("content-disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd, re.I)
            title = _file_title(r.url if "." in name else ("/" + (unquote(m.group(1)) if m else "")))
            return Resolved(ctx.url, r.url, kind, self.name, title or _file_title(ctx.url),
                            note=f"the server says it is a {kind.value}", ext=_ext_for(kind, r.url, r.content_type))
        if detect.looks_like_html(head, r.content_type) or not r.content_type:
            try:
                full = http.fetch(r.url, ctx.opts, max_bytes=2_500_000)
            except UrlError as e:
                ctx.fail(e)
                return None
            ctx.html, ctx.html_url = full.text(), full.url
        return None


@register
class PageScan(Strategy):
    """Look inside the page's HTML for a video: tags, JSON-LD, embedded players and script text."""

    name = "page"

    def resolve(self, ctx: Context) -> Resolved | None:
        if not ctx.html:
            return None
        info = page.scan(ctx.html, ctx.html_url or ctx.url)
        ctx.info = info
        found = self._best(ctx, info, ctx.html_url or ctx.url)
        depth_frames = page.worthwhile_iframes(info, ctx.opts.max_iframes)
        if found is None:
            for frame in depth_frames:
                try:
                    r = http.fetch(frame, ctx.opts, referer=ctx.html_url or ctx.url, max_bytes=1_500_000)
                except UrlError:
                    continue
                kind = detect.kind_from_content_type(r.content_type)
                if kind in (Kind.HLS, Kind.DASH, Kind.FILE, Kind.AUDIO):
                    found = Resolved(ctx.url, r.url, kind, self.name, info.title or _file_title(r.url), note="video inside an embedded player",
                                     headers=(("Referer", ctx.html_url or ctx.url),), thumbnail=info.thumbnail, ext=_ext_for(kind, r.url, r.content_type))
                    break
                sub = page.scan(r.text(), r.url)
                info.embeds.extend(e for e in sub.embeds if e not in info.embeds)
                info.iframes.extend(u for u in sub.iframes if u not in info.iframes)
                cand = self._best(ctx, sub, r.url)
                if cand is not None:
                    found = Resolved(
                        ctx.url, cand.media_url, cand.kind, self.name, info.title or sub.title or cand.title,
                        headers=(("Referer", r.url),), thumbnail=info.thumbnail or sub.thumbnail,
                        note="video inside an embedded player", ext=cand.ext,
                    )
                    break
        if found is not None:
            return found
        ctx.note("no video link found in the page")
        return None

    def _best(self, ctx: Context, info: page.PageInfo, referer: str) -> Resolved | None:
        # check the best few candidates are really there; dead ones are common on old pages
        for cand in info.candidates[:6]:
            if cand.score < 20:
                continue
            if cand.kind in (Kind.STREAM, Kind.PLAYLIST) or http.alive(cand.url, ctx.opts, referer=referer):
                note = f"found in the page ({cand.why})"
                return Resolved(
                    ctx.url, cand.url, cand.kind, self.name, info.title or _file_title(cand.url),
                    headers=(("Referer", referer),), thumbnail=info.thumbnail, note=note, ext=_ext_for(cand.kind, cand.url),
                )
        return None


@register
class YtDlp(Strategy):
    """yt-dlp knows how to get the video out of well over a thousand sites."""

    name = "yt-dlp"

    def resolve(self, ctx: Context) -> Resolved | None:
        if ctx.opts.ytdlp == "never" or ctx.tried_ytdlp:
            return None
        if not ytdlp.available():
            ctx.note("yt-dlp is not installed")
            return None
        ctx.tried_ytdlp = True
        targets = [ctx.url]
        if ctx.info:  # embedded players and player pages the page pointed to (YouTube/Vimeo embeds are common)
            targets += [e for e in ctx.info.embeds + ctx.info.iframes if e not in targets][: ctx.opts.max_iframes + 2]
        first_error: UrlError | None = None
        for t in targets:
            try:
                res = ytdlp.resolve(t, ctx.opts)
            except UrlError as e:
                first_error = first_error or e
                continue
            if t != ctx.url:
                res = Resolved(**{**res.__dict__, "page_url": ctx.url, "note": res.note + " (from an embedded player)"})
            else:
                res = Resolved(**{**res.__dict__, "page_url": ctx.url})
            tip = ytdlp.js_runtime_warning(t)
            if tip:
                ctx.note(tip)
            return res
        if first_error is not None and "does not know" not in first_error.message:
            ctx.fail(first_error)  # a real reason (login, DRM, region...) beats "nothing found"
        return None


@register
class VlcFallback(Strategy):
    """Nothing worked: hand the address to VLC anyway - it has its own site scripts and may succeed."""

    name = "vlc"

    def resolve(self, ctx: Context) -> Resolved | None:
        if ctx.last_error is not None and detect.kind_from_url(ctx.url) is None and ctx.probe is None:
            return None  # the site could not even be reached: VLC will not do better
        title = (ctx.info.title if ctx.info else "") or _file_title(ctx.url)
        return Resolved(
            ctx.url, ctx.html_url or ctx.url, Kind.PAGE, self.name, title,
            thumbnail=(ctx.info.thumbnail if ctx.info else ""),
            note="NyRun could not find a video file itself, so VLC will try the page directly",
        )
