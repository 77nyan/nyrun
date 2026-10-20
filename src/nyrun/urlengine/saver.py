"""Saving a resolved URL to disk.

Methods, chosen per stream (each falls back to the next when it cannot do the job):

  direct file   plain HTTP with resume (a .part file is kept so an interrupted download continues)
  HLS           ffmpeg -c copy (fast, remuxes to mp4)  ->  NyRun's own downloader (plain VOD only)  ->  yt-dlp
  DASH          ffmpeg  ->  yt-dlp
  yt-dlp sites  yt-dlp (merges the best video and audio with ffmpeg when it is installed)
  live streams  ffmpeg records until you stop it
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import detect, hls, http, ytdlp
from .model import Cancelled, Kind, Options, Progress, Resolved, UrlError
from .tools import ffmpeg_tool, find_ffmpeg, find_ffprobe, install_hint

DOWNLOADS_FOLDER_NAME = "URL Downloads"
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(title: str, fallback: str = "video") -> str:
    """A file/folder name that is valid on Linux, macOS and Windows."""
    name = _BAD.sub(" ", title or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:100].strip(" .")
    return name or fallback


def unique_dir(parent: Path, name: str) -> Path:
    """parent/name, or parent/name (2), (3)... when it already exists."""
    cand = parent / name
    n = 2
    while cand.exists():
        cand = parent / f"{name} ({n})"
        n += 1
    return cand


def downloads_root(settings, library_root: Path | None) -> Path:
    """Where downloads go: your own choice, else <NyRun folder>/URL Downloads, else ~/URL Downloads."""
    custom = (getattr(settings, "url_download_dir", "") or "").strip()
    if custom:
        return Path(custom).expanduser()
    base = library_root if library_root else Path.home()
    return base / DOWNLOADS_FOLDER_NAME


def _report(cb: Callable[[Progress], None] | None, **kw) -> None:
    if cb:
        cb(Progress(**kw))


def _guess_ext(res: Resolved, content_type: str = "", disposition: str = "") -> str:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition or "", re.I)
    if m:
        e = detect.extension_of(unquote(m.group(1)))
        if e in detect.VIDEO_EXTS | detect.AUDIO_EXTS:
            return e
    e = detect.extension_of(res.media_url)
    if e in detect.VIDEO_EXTS | detect.AUDIO_EXTS:
        return e
    return res.ext or ("mp3" if res.kind is Kind.AUDIO else "mp4")


# --------------------------------------------------------------------------- #
# the individual methods
# --------------------------------------------------------------------------- #

def _http_download(res: Resolved, dest: Path, opts: Options, on_progress, cancel: threading.Event) -> Path:
    """Plain HTTP(S) download with resume support."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    have = tmp.stat().st_size if tmp.exists() else 0
    headers = http.base_headers(opts, res.header("Referer"))
    headers["Accept"] = "*/*"
    headers["Accept-Encoding"] = "identity"
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(res.media_url, headers=headers)
    _report(on_progress, stage="connecting", message="connecting")
    try:
        resp = urllib.request.urlopen(req, timeout=opts.timeout)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:  # asked for bytes past the end: the file is already complete
            tmp.replace(dest)
            return dest
        raise http.explain(e, res.media_url) from None
    except (urllib.error.URLError, OSError) as e:
        raise http.explain(e, res.media_url) from None
    with resp:
        status = resp.status
        if have and status != 206:
            have = 0  # the server ignored Range: start over
        total = 0
        try:
            total = int(resp.headers.get("Content-Length", "0")) + (have if status == 206 else 0)
        except ValueError:
            pass
        mode = "ab" if have else "wb"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        done = have
        last = 0.0
        try:
            with open(tmp, mode) as out:
                while True:
                    if cancel.is_set():
                        raise Cancelled()
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last > 0.2:
                        last = now
                        speed = (done - have) / max(0.001, now - started)
                        frac = (done / total) if total else None
                        eta = ((total - done) / speed) if total and speed > 0 else None
                        _report(on_progress, stage="downloading", done=done, total=total, fraction=frac, speed=speed, eta=eta)
        except (OSError, TimeoutError) as e:
            if isinstance(e, Cancelled):
                raise
            raise http.explain(e, res.media_url) from None
    if total and done < total:
        raise UrlError("The connection dropped before the download finished.", "Run it again: NyRun continues where it stopped.")
    tmp.replace(dest)
    return dest


def _probe_duration(res: Resolved, opts: Options) -> float | None:
    if res.duration:
        return res.duration
    probe = find_ffprobe()
    if not probe or res.kind is Kind.STREAM:
        return None
    cmd = [probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1"]
    if res.header("Referer"):
        cmd += ["-headers", f"Referer: {res.header('Referer')}\r\n"]
    cmd += ["-user_agent", opts.user_agent, res.media_url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25).stdout.strip()
        return float(out) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _ffmpeg_download(res: Resolved, dest: Path, opts: Options, on_progress, cancel: threading.Event) -> Path:
    """Copy a stream into a file with ffmpeg (no re-encoding)."""
    ff = find_ffmpeg(opts.ffmpeg)
    if not ff:
        raise UrlError("ffmpeg is needed for this kind of stream and was not found.", f"Install it with: {install_hint('ffmpeg')}")
    live = res.kind is Kind.STREAM or res.is_live
    duration = None if live else _probe_duration(res, opts)
    tmp = dest.with_suffix(".part" + dest.suffix)
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-progress", "pipe:1", "-user_agent", opts.user_agent]
    if res.header("Referer") and res.media_url.startswith("http"):
        cmd += ["-headers", f"Referer: {res.header('Referer')}\r\n"]
    cmd += ["-i", res.media_url]
    if res.audio_url:
        cmd += ["-i", res.audio_url, "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v?", "-map", "0:a?"]
    cmd += ["-c", "copy"]
    if dest.suffix.lower() in (".mp4", ".m4v", ".mov"):
        cmd += ["-bsf:a", "aac_adtstoasc"] if res.kind is Kind.HLS else []
        cmd += ["-movflags", "+faststart"]
    cmd += [str(tmp)]
    tmp.parent.mkdir(parents=True, exist_ok=True)
    _report(on_progress, stage="connecting", message="connecting")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    started = time.time()
    last_stdout = 0.0
    errtext: list[str] = []

    def drain() -> None:
        assert proc.stderr is not None
        for ln in proc.stderr:
            errtext.append(ln.strip())

    threading.Thread(target=drain, daemon=True).start()
    assert proc.stdout is not None
    cancelled = False
    try:
        for line in proc.stdout:
            if cancel.is_set():
                cancelled = True
                break
            k, _, v = line.strip().partition("=")
            if k in ("out_time_us", "out_time_ms") and v.isdigit():
                secs = int(v) / 1_000_000
                now = time.time()
                if now - last_stdout > 0.2:
                    last_stdout = now
                    frac = min(0.99, secs / duration) if duration else None
                    speed = secs / max(0.001, now - started)  # media seconds per second, shown as x
                    eta = ((duration - secs) / speed) if duration and speed > 0 else None
                    _report(on_progress, stage="downloading", fraction=frac, speed=0.0, eta=eta,
                            message=f"{int(secs // 60)}:{int(secs % 60):02d} saved" + (" (recording)" if live else ""))
    finally:
        if cancelled or cancel.is_set():
            try:
                proc.terminate()
            except OSError:
                pass
        code = proc.wait()
    if cancelled or cancel.is_set():
        if live and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(dest)  # a recording you stopped is still a recording
            return dest
        tmp.unlink(missing_ok=True)
        raise Cancelled()
    if code != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        detail = (errtext[-1] if errtext else "ffmpeg failed")[:200]
        raise UrlError(f"ffmpeg could not save that stream: {detail}")
    tmp.replace(dest)
    return dest


def _fetch_poster(res: Resolved, thumb_url: str, folder: Path, opts: Options) -> Path | None:
    """Save the thumbnail as poster.jpg so the folder can be dropped straight into a NyRun library."""
    if not thumb_url or not thumb_url.startswith("http"):
        return None
    try:
        r = http.fetch(thumb_url, opts, referer=res.page_url, max_bytes=8_000_000)
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(r.body)) as im:
            im = im.convert("RGB")
            out = folder / "poster.jpg"
            im.save(out, "JPEG", quality=90)
            return out
    except Exception:  # noqa: BLE001  (a missing poster must never fail a download)
        return None


# --------------------------------------------------------------------------- #
# the public entry point
# --------------------------------------------------------------------------- #

def download(
    res: Resolved,
    root: Path,
    opts: Options,
    *,
    on_progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Download `res` into root/<Title>/<Title>.<ext> (+ poster.jpg). Returns the video file's path.

    Raises UrlError (friendly), or Cancelled when `cancel` was set.
    """
    cancel = cancel or threading.Event()
    title = safe_name(res.title or detect.title_from_url(res.page_url))
    folder = unique_dir(root, title)
    ext = _guess_ext(res)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        path = _dispatch(res, folder, title, ext, opts, on_progress, cancel)
    except BaseException:
        # leave nothing half-made behind
        try:
            if not any(p for p in folder.iterdir() if not p.name.endswith(".part")):
                for p in folder.iterdir():
                    p.unlink(missing_ok=True)
                folder.rmdir()
        except OSError:
            pass
        raise
    thumb = res.thumbnail
    if thumb:
        _fetch_poster(res, thumb, folder, opts)
    _report(on_progress, stage="done", fraction=1.0, message="saved")
    return path


def _dispatch(res: Resolved, folder: Path, title: str, ext: str, opts: Options, on_progress, cancel) -> Path:
    errors: list[UrlError] = []

    def attempt(fn) -> Path | None:
        try:
            return fn()
        except (Cancelled, KeyboardInterrupt):
            raise
        except UrlError as e:
            errors.append(e)
        except hls.NeedsFfmpeg as e:
            errors.append(UrlError(f"This stream can't be saved without ffmpeg ({e}).", f"Install it with: {install_hint('ffmpeg')}"))
        return None

    have_ff = find_ffmpeg(opts.ffmpeg) is not None
    max_h = int(opts.quality) if opts.quality.isdigit() else 0

    def via_ytdlp() -> Path:
        path, info = ytdlp.download(res.page_url if res.source != "direct" else res.media_url, folder, opts, on_progress=on_progress, cancel=cancel)
        final = folder / f"{title}{path.suffix}"
        if path != final and not final.exists():
            path.rename(final)
            return final
        return path

    def ffm(extension: str) -> Callable[[], Path]:
        return lambda: _ffmpeg_download(res, folder / f"{title}.{extension}", opts, on_progress, cancel)

    # 1) yt-dlp resolved it (or nothing else can): let yt-dlp do the saving, it handles merged formats and cookies
    if res.via_ytdlp or res.kind is Kind.PAGE:
        if ytdlp.available() and opts.ytdlp != "never":
            got = attempt(via_ytdlp)
            if got:
                return got
        if res.kind is not Kind.PAGE and res.media_url:  # fall through to the direct methods with the address yt-dlp gave
            pass
        elif not errors:
            raise UrlError("NyRun could not find a video to save at that link.", "Playing it in VLC may still work.")
    # 2) by kind
    if res.kind in (Kind.FILE, Kind.AUDIO, Kind.PLAYLIST) and not res.audio_url:
        got = attempt(lambda: _http_download(res, folder / f"{title}.{ext}", opts, on_progress, cancel))
        if got:
            return got
    elif res.kind in (Kind.FILE, Kind.AUDIO) and res.audio_url:  # separate video and audio: join with ffmpeg
        got = attempt(ffm("mp4" if res.kind is Kind.FILE else ext))
        if got:
            return got
    elif res.kind is Kind.HLS:
        if have_ff:
            got = attempt(ffm("mp4"))
            if got:
                return got
        got = attempt(lambda: hls.download(res.media_url, folder / f"{title}.ts", opts, referer=res.header("Referer"), max_height=max_h, on_progress=on_progress, cancel=cancel))
        if got:
            return got
        if ytdlp.available() and opts.ytdlp != "never" and not res.via_ytdlp:
            got = attempt(lambda: ytdlp.download(res.media_url, folder, opts, on_progress=on_progress, cancel=cancel)[0])
            if got:
                return got
    elif res.kind is Kind.DASH:
        if have_ff:
            got = attempt(ffm("mp4"))
            if got:
                return got
        if ytdlp.available() and opts.ytdlp != "never":
            got = attempt(lambda: ytdlp.download(res.media_url, folder, opts, on_progress=on_progress, cancel=cancel)[0])
            if got:
                return got
        if not have_ff:
            errors.append(UrlError("DASH streams need ffmpeg to be saved.", f"Install it with: {install_hint('ffmpeg')}"))
    elif res.kind is Kind.STREAM:
        got = attempt(ffm("ts"))
        if got:
            return got
    if errors:
        # the first error is the most specific one (the last is usually "nothing else worked")
        raise errors[0]
    raise UrlError("NyRun does not know how to save that.", "Playing it in VLC may still work.")
