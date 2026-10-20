"""Looking at a URL (or a few bytes of what it points to) and saying what it is."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit, urlunsplit

from .model import Kind, UrlError

VIDEO_EXTS = {"mp4", "m4v", "mkv", "webm", "mov", "avi", "wmv", "flv", "ts", "m2ts", "mts", "mpg", "mpeg", "3gp", "ogv", "vob", "divx"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "flac", "ogg", "oga", "opus", "wav", "wma", "mka", "aiff", "ape", "alac"}
PLAYLIST_EXTS = {"pls", "m3u", "asx", "xspf", "wpl"}  # VLC reads these itself
MANIFEST_EXTS = {"m3u8": Kind.HLS, "mpd": Kind.DASH}

WEB_SCHEMES = {"http", "https"}
STREAM_SCHEMES = {"rtsp", "rtsps", "rtmp", "rtmps", "rtmpt", "rtmpe", "mms", "mmsh", "mmst", "udp", "rtp", "srt", "ftp", "ftps", "sftp"}
REFUSED = {
    "magnet": "Magnet links are torrents. Download them with a torrent client, then use Add a file.",
    "file": "That is a local file. Use Add a file or Import instead.",
    "javascript": "That is not a link to a video.",
    "data": "That is not a link to a video.",
    "blob": "A blob: address only works inside the page that made it. Copy the page's address instead.",
}

_HOST_LIKE = re.compile(r"^(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)|^localhost(?::\d+)?(?:[/?#]|$)|^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?(?:[/?#]|$)", re.I)


def normalize_url(text: str) -> str:
    """Clean up what the user typed or pasted. Raises UrlError when it cannot be a link."""
    url = (text or "").strip().strip("\"'<>").strip()
    if not url:
        raise UrlError("Type or paste a link first.")
    if re.search(r"\s", url):
        raise UrlError("That has spaces in it, so it is not one link.", "Paste just the address.")
    m = re.match(r"^([a-z][a-z0-9+.\-]*):", url, re.I)
    scheme = m.group(1).lower() if m else ""
    if scheme in REFUSED:
        raise UrlError(REFUSED[scheme])
    if not re.match(r"^[a-z][a-z0-9+.\-]*://", url, re.I):
        if url.startswith("//"):
            url = "https:" + url
        elif _HOST_LIKE.match(url):
            url = "https://" + url
        else:
            raise UrlError("That does not look like a web address.", "Example: https://example.com/video.mp4")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme in REFUSED:
        raise UrlError(REFUSED[scheme])
    if scheme not in WEB_SCHEMES | STREAM_SCHEMES:
        raise UrlError(f"NyRun cannot open {scheme}:// links.", "Use http(s), a stream address (rtsp, rtmp, udp...) or a direct file link.")
    if not parts.netloc:
        raise UrlError("That link has no website name in it.")
    host = parts.hostname or ""
    try:
        host_ascii = host.encode("idna").decode("ascii") if host else host
    except UnicodeError:
        host_ascii = host
    # hostnames are case-insensitive: rebuild the address part with a lower-case, ASCII host
    userinfo, _, hostport = parts.netloc.rpartition("@")
    port = hostport.rsplit(":", 1)[1] if re.search(r":\d+$", hostport) else ""
    shown = f"[{host_ascii}]" if ":" in host_ascii else host_ascii
    netloc = (userinfo + "@" if userinfo else "") + shown + (":" + port if port else "")
    # spaces were refused above; make sure non-ASCII path characters are percent-encoded
    path = re.sub(r"[^\x21-\x7e]", lambda m_: "".join(f"%{b:02X}" for b in m_.group(0).encode()), parts.path)
    query = re.sub(r"[^\x21-\x7e]", lambda m_: "".join(f"%{b:02X}" for b in m_.group(0).encode()), parts.query)
    return urlunsplit((scheme, netloc, path, query, parts.fragment))


def extension_of(url: str) -> str:
    path = unquote(urlsplit(url).path)
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_from_url(url: str) -> Kind | None:
    """What the address itself says (stream scheme or file extension), or None if it says nothing."""
    scheme = urlsplit(url).scheme.lower()
    if scheme in STREAM_SCHEMES - {"ftp", "ftps", "sftp"}:
        return Kind.STREAM
    ext = extension_of(url)
    if ext in MANIFEST_EXTS:
        return MANIFEST_EXTS[ext]
    if ext in VIDEO_EXTS:
        return Kind.FILE
    if ext in AUDIO_EXTS:
        return Kind.AUDIO
    if ext in PLAYLIST_EXTS:
        return Kind.PLAYLIST
    return None


def kind_from_content_type(ct: str) -> Kind | None:
    ct = (ct or "").split(";")[0].strip().lower()
    if not ct:
        return None
    if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl", "application/mpegurl"):
        return Kind.HLS
    if ct == "application/dash+xml":
        return Kind.DASH
    if ct.startswith("video/"):
        return Kind.FILE
    if ct.startswith("audio/"):
        return Kind.AUDIO
    if ct in ("application/ogg", "application/x-matroska", "application/mp4", "application/x-mpegts"):
        return Kind.FILE
    if ct in ("application/pls+xml", "audio/x-scpls", "application/xspf+xml", "application/vnd.ms-asf"):
        return Kind.PLAYLIST
    return None


def kind_from_bytes(head: bytes) -> Kind | None:
    """Recognise media by its first bytes - for servers that send a useless Content-Type."""
    if head[:7] == b"#EXTM3U":
        return Kind.HLS if b"#EXT-X-" in head or b"#EXTINF" in head else Kind.PLAYLIST
    if b"<MPD" in head[:2048]:
        return Kind.DASH
    if head[4:8] == b"ftyp" or head[:4] in (b"\x1aE\xdf\xa3", b"OggS", b"RIFF", b"FLV\x01") or head[:3] == b"ID3":
        return Kind.AUDIO if head[:3] == b"ID3" or head[:4] == b"OggS" and b"vorbis" in head[:64] else Kind.FILE
    if head[:4] == b"fLaC":
        return Kind.AUDIO
    if len(head) >= 188 * 2 and head[0] == 0x47 and head[188] == 0x47:
        return Kind.FILE  # MPEG transport stream
    return None


def looks_like_html(head: bytes, content_type: str = "") -> bool:
    ct = (content_type or "").lower()
    if "html" in ct or "xhtml" in ct:
        return True
    sample = head[:512].lstrip().lower()
    return sample.startswith((b"<!doctype html", b"<html", b"<head", b"<?xml")) or b"<html" in sample


# hosts where a plain page scan finds nothing useful but yt-dlp (or VLC's own scripts) usually can
KNOWN_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com", "twitch.tv", "dailymotion.com", "dai.ly",
    "twitter.com", "x.com", "tiktok.com", "instagram.com", "facebook.com", "fb.watch", "reddit.com", "v.redd.it",
    "soundcloud.com", "bandcamp.com", "bilibili.com", "nicovideo.jp", "streamable.com", "imgur.com", "odysee.com",
    "rumble.com", "kick.com", "ok.ru", "vk.com", "arte.tv", "bbc.co.uk", "bbc.com", "ted.com", "mixcloud.com",
)


def is_known_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in KNOWN_HOSTS)


def title_from_url(url: str) -> str:
    """A readable fallback title: the file name without extension, or the host."""
    parts = urlsplit(url)
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    if "." in name:
        name = name.rsplit(".", 1)[0]
    name = re.sub(r"[_\-+.]+", " ", name).strip()
    return name or (parts.hostname or url)
