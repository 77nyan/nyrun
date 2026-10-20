"""yt-dlp support: used only when NyRun's own methods are not enough (or the site is one yt-dlp knows well).

Works through the yt_dlp Python module when it is installed (it is a dependency of NyRun) and falls
back to a `yt-dlp` program on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from .detect import title_from_url
from .model import Cancelled, Kind, Options, Progress, Resolved, UrlError
from .tools import find_ffmpeg, js_runtime, ytdlp_binary, ytdlp_module

QUIET_LOGGER = None


class _Silent:
    """yt-dlp logger that stays quiet (a TUI must never get text printed over it)."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg):  # noqa: D102
        pass

    def info(self, msg):  # noqa: D102
        pass

    def warning(self, msg):  # noqa: D102
        pass

    def error(self, msg):  # noqa: D102
        self.errors.append(str(msg))


def available() -> bool:
    return ytdlp_module() is not None or ytdlp_binary() is not None


def has_specific_extractor(url: str) -> bool:
    """True when yt-dlp has an extractor made for this site (not just its generic fallback)."""
    mod = ytdlp_module()
    if mod is None:
        return False
    try:
        for ie in mod.extractor.gen_extractor_classes():
            name = ie.ie_key()
            if name in ("Generic", "GenericIE"):
                continue
            if ie.suitable(url):
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def format_selector(opts: Options, *, download: bool, have_ffmpeg: bool) -> str:
    """yt-dlp -f expression for the quality setting.

    Playing: prefer one file with sound (VLC loves those), but go for separate best video + audio
    when no combined file is sharp enough. Downloading: merge best video + audio if ffmpeg exists.
    """
    q = opts.quality
    h = int(q) if q.isdigit() else 0
    cap = f"[height<={h}]" if h else ""
    if download:
        if have_ffmpeg:
            return f"bv*{cap}+ba/b{cap}/bv*+ba/b"
        return f"b{cap}/b"  # without ffmpeg only single-file formats can be saved properly
    if h:
        return f"b{cap}/bv*{cap}+ba/b{cap}/b"
    return "b[height>=720]/bv*[height<=1080]+ba/b/bv*+ba"


def _friendly(msg: str) -> UrlError:
    low = msg.lower()
    msg = re.sub(r"^(error:\s*)+", "", msg.strip(), flags=re.I)
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
    if "drm" in low:
        return UrlError("This video is protected by DRM, so it cannot be played or saved outside the site's own player.")
    if "sign in" in low or "log in" in low or "login" in low or "cookies" in low and "account" in low or "members" in low:
        return UrlError("This video needs you to be signed in.", "In Settings > Web video, pick the browser you are signed in with.")
    if "confirm you" in low and "bot" in low:
        return UrlError("The site wants proof you are not a bot.", "Pick your browser in Settings > Web video so NyRun can use its cookies.")
    if "geo" in low or "not available in your country" in low:
        return UrlError("This video is not available in your country.")
    if "private" in low:
        return UrlError("This video is private.")
    if "unsupported url" in low:
        return UrlError("yt-dlp does not know how to find a video on that page.")
    if "is not a valid url" in low:
        return UrlError("That is not a valid link.")
    if "unable to download" in low or "urlopen error" in low or "getaddrinfo" in low:
        return UrlError("Could not reach the site.", "Check your internet connection.")
    if "http error 429" in low:
        return UrlError("The site says too many requests (429).", "Wait a bit and try again.")
    return UrlError(msg[:200] or "yt-dlp could not get that video.", "Updating NyRun also updates yt-dlp, which fixes most site changes.")


def _base_options(opts: Options, logger) -> dict:
    o: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": logger,
        "noplaylist": True,
        "socket_timeout": max(10, int(opts.timeout)),
        "retries": 3,
        "extractor_retries": 2,
        "http_headers": {"User-Agent": opts.user_agent},
        "geo_bypass": True,
    }
    ff = find_ffmpeg(opts.ffmpeg)
    if ff:
        o["ffmpeg_location"] = ff
    if opts.cookies_browser and opts.cookies_browser != "none":
        o["cookiesfrombrowser"] = (opts.cookies_browser,)
    return o


def _clean_info(info: dict) -> dict:
    """If the page held a playlist, take the first playable entry."""
    seen = 0
    while info.get("_type") in ("playlist", "multi_video") and info.get("entries") and seen < 3:
        entries = [e for e in info["entries"] if e]
        if not entries:
            break
        info = entries[0]
        seen += 1
    return info


def extract(url: str, opts: Options, *, download_format: bool = False) -> dict:
    """Ask yt-dlp about a URL without downloading. Returns yt-dlp's info dict."""
    fmt = format_selector(opts, download=download_format, have_ffmpeg=find_ffmpeg(opts.ffmpeg) is not None)
    mod = ytdlp_module()
    if mod is not None:
        log = _Silent()
        o = _base_options(opts, log)
        o.update({"skip_download": True, "format": fmt})
        try:
            with mod.YoutubeDL(o) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001  yt-dlp raises many kinds
            msg = " ".join(log.errors[-1:]) or str(e)
            raise _friendly(msg) from None
        if not info:
            raise UrlError("yt-dlp found nothing at that link.")
        return _clean_info(info)
    exe = ytdlp_binary()
    if exe is None:
        raise UrlError("yt-dlp is not installed.", "Run the NyRun installer's update, or: pipx install yt-dlp")
    cmd = [exe, "-J", "--no-warnings", "--no-playlist", "-f", fmt, "--socket-timeout", str(int(opts.timeout)), "--user-agent", opts.user_agent]
    if opts.cookies_browser and opts.cookies_browser != "none":
        cmd += ["--cookies-from-browser", opts.cookies_browser]
    ff = find_ffmpeg(opts.ffmpeg)
    if ff:
        cmd += ["--ffmpeg-location", ff]
    cmd += ["--", url]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        raise UrlError(f"Could not run yt-dlp: {e}") from None
    if p.returncode != 0:
        raise _friendly((p.stderr.strip().splitlines() or ["yt-dlp failed"])[-1])
    try:
        return _clean_info(json.loads(p.stdout))
    except ValueError:
        raise UrlError("yt-dlp gave an answer NyRun could not read.") from None


def _kind_of_format(f: dict) -> Kind:
    proto = (f.get("protocol") or "").lower()
    ext = (f.get("ext") or "").lower()
    if "m3u8" in proto or ext == "m3u8":
        return Kind.HLS
    if "dash" in proto or ext == "mpd":
        return Kind.DASH
    if proto in ("rtmp", "rtsp", "rtmpe", "mms"):
        return Kind.STREAM
    if (f.get("vcodec") in (None, "none")) and f.get("acodec") not in (None, "none"):
        return Kind.AUDIO
    return Kind.FILE


def _headers_of(f: dict) -> tuple[tuple[str, str], ...]:
    h = f.get("http_headers") or {}
    keep = []
    for k in ("Referer", "User-Agent"):
        for hk, hv in h.items():
            if hk.lower() == k.lower() and hv:
                keep.append((k, hv))
    return tuple(keep)


def info_to_resolved(page_url: str, info: dict) -> Resolved:
    """Turn yt-dlp's answer into a Resolved for VLC."""
    title = (info.get("title") or "").strip() or title_from_url(page_url)
    live = bool(info.get("is_live"))
    req = info.get("requested_formats")
    audio_url = ""
    if req and len(req) >= 2:
        v = next((f for f in req if f.get("vcodec") not in (None, "none")), req[0])
        a = next((f for f in req if f is not v and f.get("acodec") not in (None, "none")), None)
        media = v.get("url", "")
        audio_url = a.get("url", "") if a else ""
        main = v
    else:
        main = (req[0] if req else info)
        media = main.get("url") or info.get("url") or ""
    if not media:
        fmts = [f for f in info.get("formats") or [] if f.get("url")]
        if not fmts:
            raise UrlError("yt-dlp found the video but no playable address for it.")
        main = fmts[-1]
        media = main["url"]
    kind = _kind_of_format(main)
    if live and kind is Kind.FILE:
        kind = Kind.STREAM
    ext = (info.get("ext") or main.get("ext") or "").lower()
    if ext in ("unknown_video", "m3u8", "mpd"):
        ext = ""
    site = info.get("extractor_key") or info.get("extractor") or ""
    note = f"found by yt-dlp ({site})" if site and site != "Generic" else "found by yt-dlp"
    return Resolved(
        page_url=page_url,
        media_url=media,
        kind=kind,
        source="yt-dlp",
        title=title,
        audio_url=audio_url,
        headers=_headers_of(main),
        thumbnail=info.get("thumbnail") or "",
        duration=float(info["duration"]) if info.get("duration") else None,
        is_live=live,
        note=note,
        ext=ext,
        via_ytdlp=True,
    )


def resolve(url: str, opts: Options) -> Resolved:
    return info_to_resolved(url, extract(url, opts))


def download(
    url: str,
    folder: Path,
    opts: Options,
    *,
    on_progress: Callable[[Progress], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[Path, dict]:
    """Download with yt-dlp into `folder`. Returns (file path, info dict)."""
    cancel = cancel or threading.Event()
    folder.mkdir(parents=True, exist_ok=True)
    have_ff = find_ffmpeg(opts.ffmpeg) is not None
    fmt = format_selector(opts, download=True, have_ffmpeg=have_ff)
    mod = ytdlp_module()
    if mod is None:
        return _download_binary(url, folder, opts, fmt, on_progress, cancel)

    def hook(d: dict) -> None:
        if cancel.is_set():
            raise Cancelled()
        if not on_progress:
            return
        st = d.get("status")
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else None
            on_progress(Progress("downloading", int(done), int(total), frac, float(d.get("speed") or 0), d.get("eta"), ""))
        elif st == "finished":
            on_progress(Progress("merging", 0, 0, 1.0, 0.0, None, "finishing"))

    def post_hook(d: dict) -> None:
        if cancel.is_set():
            raise Cancelled()
        if on_progress and d.get("status") == "started":
            on_progress(Progress("merging", 0, 0, None, 0.0, None, "joining video and sound"))

    log = _Silent()
    o = _base_options(opts, log)
    o.update({
        "format": fmt,
        "outtmpl": str(folder / "%(title).150B.%(ext)s"),
        "restrictfilenames": False,
        "windowsfilenames": True,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "postprocessor_hooks": [post_hook],
        "overwrites": False,
    })
    if have_ff:
        o["merge_output_format"] = "mp4"
    try:
        with mod.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=True)
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        if cancel.is_set():
            raise Cancelled() from None
        raise _friendly(" ".join(log.errors[-1:]) or str(e)) from None
    info = _clean_info(info or {})
    path = None
    for rd in info.get("requested_downloads") or []:
        if rd.get("filepath"):
            path = Path(rd["filepath"])
    if path is None and info.get("filepath"):
        path = Path(info["filepath"])
    if path is None or not path.exists():
        newest = sorted((p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".part")), key=lambda p: p.stat().st_mtime)
        path = newest[-1] if newest else None
    if path is None:
        raise UrlError("yt-dlp finished but NyRun could not find the saved file.")
    return path, info


def _download_binary(url, folder, opts, fmt, on_progress, cancel):
    exe = ytdlp_binary()
    if exe is None:
        raise UrlError("yt-dlp is not installed.", "Run the NyRun installer's update, or: pipx install yt-dlp")
    cmd = [exe, "--no-playlist", "--newline", "--no-warnings", "-f", fmt, "-o", str(folder / "%(title).150B.%(ext)s"),
           "--print", "after_move:filepath", "--progress-template", "download:NYPROG %(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.total_bytes_estimate)s %(progress.speed)s %(progress.eta)s",
           "--windows-filenames", "--user-agent", opts.user_agent]
    if find_ffmpeg(opts.ffmpeg):
        cmd += ["--merge-output-format", "mp4", "--ffmpeg-location", find_ffmpeg(opts.ffmpeg)]
    if opts.cookies_browser and opts.cookies_browser != "none":
        cmd += ["--cookies-from-browser", opts.cookies_browser]
    cmd += ["--", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    path = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel.is_set():
            proc.terminate()
            raise Cancelled()
        line = line.strip()
        if line.startswith("NYPROG") and on_progress:
            parts = line.split()[1:]

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return 0.0

            done, total, est, speed, eta = (num(x) for x in (parts + ["0"] * 5)[:5])
            total = total or est
            on_progress(Progress("downloading", int(done), int(total), (done / total) if total else None, speed, eta or None, ""))
        elif line and not line.startswith("NYPROG"):
            path = line
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise _friendly((err.strip().splitlines() or ["yt-dlp failed"])[-1])
    if not path or not Path(path).exists():
        raise UrlError("yt-dlp finished but NyRun could not find the saved file.")
    return Path(path), {"title": Path(path).stem}


def js_runtime_warning(url: str) -> str:
    """A one-line tip when a site that needs a JavaScript runtime is used without one."""
    host = url.lower()
    if ("youtube.com" in host or "youtu.be" in host) and js_runtime() is None:
        return "YouTube works best with deno or node installed (yt-dlp uses it to read the page)."
    return ""
