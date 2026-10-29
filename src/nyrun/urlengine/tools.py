"""Finding the helper programs NyRun can use: ffmpeg and yt-dlp."""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Tool:
    name: str
    found: bool
    where: str = ""  # path, or "python module"
    version: str = ""
    install: str = ""  # one-line install hint for the user's system


def _package_manager() -> str:
    for pm in ("pacman", "apt", "dnf", "zypper"):
        if shutil.which(pm):
            return pm
    return ""


def install_hint(tool: str) -> str:
    pm = _package_manager()
    names = {"ffmpeg": "ffmpeg", "yt-dlp": "yt-dlp"}
    pkg = names.get(tool, tool)
    cmds = {
        "pacman": f"sudo pacman -S {pkg}",
        "apt": f"sudo apt install {pkg}",
        "dnf": f"sudo dnf install {pkg}",
        "zypper": f"sudo zypper install {pkg}",
    }
    if pm in cmds:
        return cmds[pm]
    return f"pipx install {pkg}" if tool == "yt-dlp" else f"install {pkg} from your package manager"


def find_ffmpeg(custom: str = "") -> str | None:
    """Path to ffmpeg, or None. VLC does not ship it, so it may genuinely be missing."""
    if custom:
        return shutil.which(custom) or None
    return shutil.which("ffmpeg")


def find_ffprobe() -> str | None:
    return shutil.which("ffprobe")


@lru_cache(maxsize=4)
def _version_of(path: str) -> str:
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=8).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    first = out.splitlines()[0] if out else ""
    parts = first.split()
    return parts[2] if len(parts) >= 3 and parts[0] == "ffmpeg" else first[:40]


def ffmpeg_tool(custom: str = "") -> Tool:
    path = find_ffmpeg(custom)
    if path:
        return Tool("ffmpeg", True, path, _version_of(path))
    return Tool("ffmpeg", False, install=install_hint("ffmpeg"))


def ytdlp_module():
    """The yt_dlp Python module, or None."""
    try:
        import yt_dlp  # type: ignore[import-not-found]

        return yt_dlp
    except Exception:  # noqa: BLE001  (a broken install must not crash the app)
        return None


def ytdlp_binary() -> str | None:
    return shutil.which("yt-dlp")


def ytdlp_tool() -> Tool:
    mod = ytdlp_module()
    if mod is not None:
        ver = getattr(getattr(mod, "version", None), "__version__", "")
        return Tool("yt-dlp", True, "python module", ver)
    path = ytdlp_binary()
    if path:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=8).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        return Tool("yt-dlp", True, path, out)
    return Tool("yt-dlp", False, install=install_hint("yt-dlp"))


def js_runtime() -> str | None:
    """YouTube (and a few other sites) need a JavaScript runtime for yt-dlp."""
    for name in ("deno", "node", "bun"):
        if shutil.which(name):
            return name
    return None


def python_exe() -> str:
    return sys.executable
