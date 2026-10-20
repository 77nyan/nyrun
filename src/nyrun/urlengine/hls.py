"""HLS playlists: reading them and (for plain, unencrypted ones) downloading them without ffmpeg."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

from .http import fetch
from .model import Cancelled, Options, Progress, UrlError


class NeedsFfmpeg(Exception):
    """This playlist cannot be downloaded natively (encrypted, separate audio, ...)."""


@dataclass
class Variant:
    uri: str
    bandwidth: int = 0
    width: int = 0
    height: int = 0
    audio_group: str = ""
    codecs: str = ""


@dataclass
class Segment:
    uri: str
    duration: float = 0.0
    key_method: str = "NONE"
    key_uri: str = ""
    byte_range: str = ""


@dataclass
class Playlist:
    master: bool
    variants: list[Variant] = field(default_factory=list)
    audio_uris: dict[str, str] = field(default_factory=dict)  # group id -> rendition playlist
    segments: list[Segment] = field(default_factory=list)
    init_uri: str = ""  # EXT-X-MAP (fragmented MP4)
    endlist: bool = False
    target: float = 0.0

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.segments)

    @property
    def encrypted(self) -> bool:
        return any(s.key_method not in ("NONE", "") for s in self.segments)


_ATTR = re.compile(r'([A-Z0-9\-]+)=("[^"]*"|[^,]*)')


def _attrs(line: str) -> dict[str, str]:
    return {k: v.strip('"') for k, v in _ATTR.findall(line.split(":", 1)[1] if ":" in line else line)}


def parse(text: str, base: str) -> Playlist:
    """Parse an HLS playlist (master or media)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise UrlError("That is not a valid HLS playlist.")
    master = any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines)
    pl = Playlist(master=master)
    pending: Variant | None = None
    dur = 0.0
    key_method, key_uri = "NONE", ""
    byterange = ""
    for ln in lines[1:]:
        if ln.startswith("#EXT-X-STREAM-INF"):
            a = _attrs(ln)
            res = a.get("RESOLUTION", "")
            w, h = (int(x) for x in res.split("x")) if re.fullmatch(r"\d+x\d+", res) else (0, 0)
            pending = Variant("", int(a.get("BANDWIDTH", "0") or 0), w, h, a.get("AUDIO", ""), a.get("CODECS", ""))
        elif ln.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in ln:
            a = _attrs(ln)
            if a.get("URI") and a.get("GROUP-ID"):
                pl.audio_uris.setdefault(a["GROUP-ID"], urljoin(base, a["URI"]))
        elif ln.startswith("#EXTINF"):
            try:
                dur = float(ln.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                dur = 0.0
        elif ln.startswith("#EXT-X-KEY"):
            a = _attrs(ln)
            key_method = a.get("METHOD", "NONE")
            key_uri = urljoin(base, a["URI"]) if a.get("URI") else ""
        elif ln.startswith("#EXT-X-MAP"):
            a = _attrs(ln)
            if a.get("URI"):
                pl.init_uri = urljoin(base, a["URI"])
        elif ln.startswith("#EXT-X-BYTERANGE"):
            byterange = ln.split(":", 1)[1]
        elif ln.startswith("#EXT-X-TARGETDURATION"):
            try:
                pl.target = float(ln.split(":", 1)[1])
            except ValueError:
                pass
        elif ln.startswith("#EXT-X-ENDLIST"):
            pl.endlist = True
        elif not ln.startswith("#"):
            uri = urljoin(base, ln)
            if pending is not None:
                pending.uri = uri
                pl.variants.append(pending)
                pending = None
            else:
                pl.segments.append(Segment(uri, dur, key_method, key_uri, byterange))
                dur, byterange = 0.0, ""
    return pl


def pick_variant(pl: Playlist, max_height: int = 0) -> Variant | None:
    """Best variant, optionally not taller than max_height."""
    if not pl.variants:
        return None
    cands = pl.variants
    if max_height:
        fit = [v for v in cands if v.height and v.height <= max_height]
        if fit:
            cands = fit
        else:
            lowest = [v for v in cands if v.height]
            if lowest:
                cands = [min(lowest, key=lambda v: v.height)]
    return max(cands, key=lambda v: (v.bandwidth, v.height))


def load(url: str, opts: Options, *, referer: str = "", headers: dict[str, str] | None = None) -> Playlist:
    r = fetch(url, opts, referer=referer, headers=headers, max_bytes=5_000_000)
    return parse(r.text(), r.url)


def download(
    url: str,
    dest: Path,
    opts: Options,
    *,
    referer: str = "",
    headers: dict[str, str] | None = None,
    max_height: int = 0,
    on_progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
    workers: int = 4,
) -> Path:
    """Download a plain (unencrypted) VOD playlist to dest (a .ts or .mp4 file, chosen from the segments).

    Raises NeedsFfmpeg when the stream is encrypted, live, or keeps audio in a separate playlist.
    """
    cancel = cancel or threading.Event()
    pl = load(url, opts, referer=referer, headers=headers)
    if pl.master:
        var = pick_variant(pl, max_height)
        if var is None:
            raise UrlError("That stream has no video in it.")
        if var.audio_group and var.audio_group in pl.audio_uris:
            raise NeedsFfmpeg("audio is in a separate playlist")
        pl = load(var.uri, opts, referer=referer, headers=headers)
        url = var.uri
    if pl.encrypted:
        raise NeedsFfmpeg("the stream is encrypted")
    if not pl.endlist:
        raise NeedsFfmpeg("this is a live stream")
    if not pl.segments:
        raise UrlError("The playlist has no video parts in it.")
    if any(s.byte_range for s in pl.segments):
        raise NeedsFfmpeg("byte-range segments")
    total = len(pl.segments)
    parts: dict[int, bytes] = {}
    lock = threading.Lock()
    state = {"done": 0, "bytes": 0}
    started = __import__("time").time()

    def report(stage: str = "downloading") -> None:
        if on_progress:
            elapsed = max(0.001, __import__("time").time() - started)
            frac = state["done"] / total
            speed = state["bytes"] / elapsed
            eta = (elapsed / frac - elapsed) if frac > 0.02 else None
            on_progress(Progress(stage, state["bytes"], 0, frac, speed, eta, f"{state['done']}/{total} parts"))

    def grab(i: int) -> None:
        if cancel.is_set():
            return
        last: Exception | None = None
        for _ in range(4):
            if cancel.is_set():
                return
            try:
                r = fetch(pl.segments[i].uri, opts, referer=referer, headers=headers, max_bytes=60_000_000, timeout=30)
                with lock:
                    parts[i] = r.body
                    state["done"] += 1
                    state["bytes"] += len(r.body)
                report()
                return
            except UrlError as e:
                last = e
        raise last or UrlError("A video part could not be downloaded.")

    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    report("connecting")
    try:
        with open(tmp, "wb") as out:
            if pl.init_uri:
                out.write(fetch(pl.init_uri, opts, referer=referer, headers=headers, max_bytes=5_000_000).body)
            next_write = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(grab, i) for i in range(total)]
                for i, f in enumerate(futures):
                    f.result()
                    if cancel.is_set():
                        raise Cancelled()
                    with lock:
                        while next_write in parts:
                            out.write(parts.pop(next_write))
                            next_write += 1
            with lock:
                while next_write in parts:
                    out.write(parts.pop(next_write))
                    next_write += 1
        if cancel.is_set():
            raise Cancelled()
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    report("done")
    return dest
