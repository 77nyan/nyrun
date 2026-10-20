"""Persistent configuration and per-user state (watched, favorites, resume info).

Everything is stored as small JSON files in the platform config dir:

    Linux/macOS: ~/.config/nyrun/   (honours $XDG_CONFIG_HOME)
    Windows:     %APPDATA%\\nyrun\\
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CATEGORIES = ("Movies", "Series", "Shows", "Music")

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".ogv", ".3gp",
}
AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".wma", ".aiff", ".ape"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt"}


def config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "nyrun"


def cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "nyrun"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic on every platform we care about


@dataclass
class Settings:
    """User settings (config.json)."""

    default_folder: str = ""
    theme: str = "auto"  # auto | omarchy | terminal | <theme name>
    auto_watched: bool = True  # albums: mark as listened when you press play
    track_progress: bool = True  # watch VLC while a film plays: real progress, watched at the end
    watched_at: int = 90  # percent of a film/episode that counts as "watched"
    resume: bool = True  # start again where you stopped
    logo_line: str = "rainbow"  # colour of the line under the NyRun logo: rainbow | theme

    # ---- Music ----
    music_controls: bool = True  # play music in a hidden VLC that NyRun controls (off = normal VLC window)
    music_loop: str = "off"  # off | all | track
    music_shuffle: bool = False
    music_volume: int = 80  # percent

    # ---- Web video (URL) ----
    url_downloads: bool = False  # allow saving a URL's video to disk (off by default)
    url_ytdlp: str = "auto"  # auto = only when NyRun's own methods are not enough | never | always
    url_quality: str = "auto"  # auto | 1080 | 720 | 480
    url_cookies: str = "none"  # browser whose login yt-dlp may use: none | firefox | chrome | chromium | brave | edge | vivaldi | opera
    url_download_dir: str = ""  # empty = "URL Downloads" inside your NyRun folder

    # ---- VLC ----
    vlc_path: str = ""  # empty = auto-detect
    vlc_launcher: str = "auto"  # auto | native | flatpak
    fullscreen: bool = True
    vlc_play_and_exit: bool = True  # close VLC when the queue ends
    vlc_hwdec: str = "auto"  # auto = let VLC decide; none | vaapi | vdpau | d3d11va | dxva2 | videotoolbox
    vlc_vout: str = ""  # video output module, e.g. gl, xcb_x11 (empty = VLC default)
    vlc_aout: str = ""  # audio output module, e.g. pulse, alsa (empty = VLC default)
    vlc_qt_platform: str = "auto"  # auto | xcb | wayland  (QT_QPA_PLATFORM for VLC's window)
    vlc_title_osd: bool = True  # show the file name on screen when playback starts
    vlc_quiet: bool = False  # no first-run privacy prompt, don't keep VLC's recent list
    vlc_one_instance: str = "default"  # default | on | off
    vlc_loop: bool = False
    vlc_start_paused: bool = False
    vlc_on_top: bool = False
    vlc_sub_autodetect: bool = True
    vlc_sub_lang: str = ""  # e.g. "en" or "en,pl"
    vlc_audio_lang: str = ""
    vlc_continue: str = "default"  # default | never | ask | always  (resume where you stopped)
    vlc_rate: str = ""  # playback speed, e.g. 1.25 (empty = normal)
    extra_vlc_args: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Settings":
        raw = _read_json(config_dir() / "config.json", {})
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        _write_json(config_dir() / "config.json", self.__dict__)

    @property
    def root(self) -> Path | None:
        return Path(self.default_folder).expanduser() if self.default_folder else None


URL_HISTORY_MAX = 30


class State:
    """Mutable per-user state: watched flags, favorites, last-played timestamps.

    Keys are paths *relative to the NyRun root* (e.g. "Movies/Inception (2010)"),
    so you can move the whole library to another disk and keep your history.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (config_dir() / "state.json")
        raw = _read_json(self.path, {})
        self.watched: set[str] = set(raw.get("watched", []))
        self.favorites: set[str] = set(raw.get("favorites", []))
        self.last_played: dict[str, float] = dict(raw.get("last_played", {}))
        self.play_count: dict[str, int] = dict(raw.get("play_count", {}))
        self.last_episode: dict[str, str] = dict(raw.get("last_episode", {}))
        # recently opened URLs, newest first: [{"url", "title", "kind", "time"}]
        self.url_history: list[dict] = [
            h for h in raw.get("url_history", []) if isinstance(h, dict) and isinstance(h.get("url"), str)
        ][:URL_HISTORY_MAX]
        # key -> [seconds watched, total seconds] for things you stopped part-way through
        self.positions: dict[str, list[float]] = {
            k: [float(v[0]), float(v[1])] for k, v in dict(raw.get("positions", {})).items()
            if isinstance(v, (list, tuple)) and len(v) == 2
        }

    def save(self) -> None:
        _write_json(
            self.path,
            {
                "watched": sorted(self.watched),
                "favorites": sorted(self.favorites),
                "last_played": self.last_played,
                "play_count": self.play_count,
                "last_episode": self.last_episode,
                "positions": self.positions,
                "url_history": self.url_history,
            },
        )

    def toggle_watched(self, key: str) -> bool:
        result = key not in self.watched
        self.set_watched(key, result)
        return result

    def set_watched(self, key: str, value: bool) -> None:
        (self.watched.add if value else self.watched.discard)(key)
        self.positions.pop(key, None)  # watched or reset: no half-way point to remember
        self.save()

    def add_url(self, url: str, title: str = "", kind: str = "") -> None:
        """Remember a URL you opened (most recent first, no duplicates)."""
        self.url_history = [h for h in self.url_history if h.get("url") != url]
        self.url_history.insert(0, {"url": url, "title": title, "kind": kind, "time": time.time()})
        del self.url_history[URL_HISTORY_MAX:]
        self.save()

    def forget_url(self, url: str) -> None:
        self.url_history = [h for h in self.url_history if h.get("url") != url]
        self.save()

    def set_position(self, key: str, seconds: float, total: float) -> None:
        """Remember where you stopped (in memory; call save() to write it)."""
        self.positions[key] = [round(seconds, 1), round(total, 1)]

    def resume_point(self, key: str, minimum: float = 20.0) -> float | None:
        """Seconds to resume at, or None (never started, finished, or only just begun)."""
        pos = self.positions.get(key)
        if key in self.watched or not pos or pos[0] < minimum:
            return None
        return pos[0]

    def percent(self, key: str) -> int | None:
        pos = self.positions.get(key)
        if not pos or pos[1] <= 0:
            return None
        return max(1, min(99, int(pos[0] / pos[1] * 100)))

    def toggle_favorite(self, key: str) -> bool:
        if key in self.favorites:
            self.favorites.discard(key)
            result = False
        else:
            self.favorites.add(key)
            result = True
        self.save()
        return result

    def mark_played(self, key: str, parent_key: str | None = None, episode: str | None = None) -> None:
        now = time.time()
        for k in {key, parent_key} - {None}:
            self.last_played[k] = now
            self.play_count[k] = self.play_count.get(k, 0) + 1
        if parent_key and episode:
            self.last_episode[parent_key] = episode
        self.save()
