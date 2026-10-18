"""Library scanning, metadata, thumbnails and the import (cut/copy) logic."""

from __future__ import annotations

import hashlib
import functools
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import (
    AUDIO_EXTS,
    CATEGORIES,
    IMAGE_EXTS,
    SUBTITLE_EXTS,
    VIDEO_EXTS,
    cache_dir,
)

_POSTER_HINTS = ("poster", "cover", "folder", "thumb", "front", "art")
_YEAR_RE = re.compile(r"\((\d{4})\)\s*$|\[(\d{4})\]\s*$|[ .](\d{4})\s*$")


def natural_key(text: str) -> list[object]:
    """Sort 'Episode 2' before 'Episode 10'."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def human_duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


@dataclass
class Episode:
    path: Path
    label: str
    key: str  # relative key used for watched-state
    size: int = 0


@dataclass
class Item:
    """One selectable entry: a movie folder, a series folder, or a loose video."""

    title: str
    path: Path
    category: str
    key: str
    poster: Path | None
    episodes: list[Episode] = field(default_factory=list)
    year: str = ""
    added: float = 0.0

    @property
    def is_music(self) -> bool:
        return self.category == "Music"

    @property
    def is_collection(self) -> bool:
        """True for series/shows with more than one video inside (albums are not 'episodes')."""
        return self.category not in ("Movies", "Music") and len(self.episodes) > 1

    @property
    def main_video(self) -> Path | None:
        return self.episodes[0].path if self.episodes else None

    @property
    def total_size(self) -> int:
        return sum(e.size for e in self.episodes)

    @functools.cached_property
    def subtitles(self) -> list[Path]:
        folder = self.path if self.path.is_dir() else self.path.parent
        try:
            return sorted(p for p in folder.rglob("*") if p.suffix.lower() in SUBTITLE_EXTS)
        except OSError:
            return []


_RELEASE_YEAR = re.compile(r"^(?P<title>.+)[ ._(\[-]+(?P<year>(?:19|20)\d{2})(?:[ ._)\]-].*)?$")
_RELEASE_TAGS = re.compile(
    r"[ ._-](480p|576p|720p|1080p|2160p|4k|bluray|bdrip|brrip|web-?dl|webrip|hdrip|dvdrip|x264|x265|h\.?264|hevc)\b.*$",
    re.IGNORECASE,
)


def guess_title(name: str) -> str:
    """Turn a release-style name into a tidy folder name.

    'Inception.2010.1080p.BluRay.x264' -> 'Inception (2010)'
    """
    name = name.strip()
    if " " not in name:
        name = name.replace(".", " ").replace("_", " ")
    m = _RELEASE_YEAR.match(name)
    if m:
        title = m.group("title").strip(" ._-([")
        return f"{title} ({m.group('year')})" if title else name
    return _RELEASE_TAGS.sub("", name).strip(" ._-") or name


def _split_year(name: str) -> tuple[str, str]:
    m = _YEAR_RE.search(name)
    if not m:
        return name.strip(), ""
    year = next(g for g in m.groups() if g)
    return name[: m.start()].strip(" ._-") or name, year


def _pick_poster(folder: Path, images: list[Path]) -> Path | None:
    if not images:
        return None
    stem = folder.name.lower()
    for img in images:
        s = img.stem.lower()
        if s == stem or any(s == h or s.startswith(h) for h in _POSTER_HINTS):
            return img
    return sorted(images, key=lambda p: natural_key(p.name))[0]


def _videos_in(folder: Path, recursive: bool, exts: set[str] = VIDEO_EXTS) -> list[Path]:
    it = folder.rglob("*") if recursive else folder.iterdir()
    vids = []
    for p in it:
        try:
            if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("."):
                vids.append(p)
        except OSError:
            continue
    return vids


def _is_sample(p: Path) -> bool:
    return "sample" in p.stem.lower() and p.stat().st_size < 150 * 1024 * 1024


def scan_category(root: Path, category: str) -> list[Item]:
    """Scan root/<category> and return its items (unsorted)."""
    base = root / category
    if not base.is_dir():
        return []
    music = category == "Music"
    exts = AUDIO_EXTS if music else VIDEO_EXTS
    items: list[Item] = []
    try:
        entries = sorted(base.iterdir(), key=lambda p: natural_key(p.name))
    except OSError:
        return []

    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            key = f"{category}/{entry.name}"
            if entry.is_dir():
                vids = [v for v in _videos_in(entry, recursive=True, exts=exts) if music or not _is_sample(v)]
                if not vids:
                    continue
                vids.sort(key=lambda p: natural_key(str(p.relative_to(entry))))
                if category == "Movies":
                    # A movie folder plays its biggest file (skips trailers/extras).
                    vids = [max(vids, key=lambda p: p.stat().st_size)]
                images = [
                    p for p in entry.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_EXTS
                ]
                poster = _pick_poster(entry, images)
                if poster is None and music:
                    poster = _embedded_cover(vids)
                episodes = [
                    Episode(
                        path=v,
                        label=_episode_label(v, entry),
                        key=f"{key}/{v.relative_to(entry).as_posix()}",
                        size=v.stat().st_size,
                    )
                    for v in vids
                ]
                title, year = _split_year(entry.name)
                items.append(
                    Item(title, entry, category, key, poster, episodes, year, entry.stat().st_mtime)
                )
            elif entry.is_file() and entry.suffix.lower() in exts:
                # Loose file, optionally with a same-named image next to it.
                poster = next(
                    (entry.with_suffix(e) for e in (".jpg", ".jpeg", ".png", ".JPG", ".PNG")
                     if entry.with_suffix(e).exists()),
                    None,
                )
                if poster is None and music:
                    poster = _embedded_cover([entry])
                title, year = _split_year(entry.stem)
                ep = Episode(entry, entry.stem, key, entry.stat().st_size)
                items.append(Item(title, entry, category, key, poster, [ep], year, entry.stat().st_mtime))
        except OSError:
            continue  # unreadable entry (permissions, broken symlink) - skip it
    return items


def _embedded_cover(tracks: list[Path]) -> Path | None:
    """Cover art stored inside the first track that has some (music only)."""
    from . import audio

    for t in tracks[:3]:
        cover = audio.cached_cover(t)
        if cover:
            return cover
    return None


def _episode_label(video: Path, folder: Path) -> str:
    rel = video.relative_to(folder)
    parts = list(rel.parts)
    parts[-1] = rel.stem
    return " / ".join(parts)


def scan_library(root: Path) -> dict[str, list[Item]]:
    return {cat: scan_category(root, cat) for cat in CATEGORIES}


def sort_items(items: list[Item], mode: str, last_played: dict[str, float]) -> list[Item]:
    if mode == "name":
        return sorted(items, key=lambda i: natural_key(i.title))
    if mode == "added":
        return sorted(items, key=lambda i: -i.added)
    if mode == "played":
        return sorted(items, key=lambda i: (-last_played.get(i.key, 0), natural_key(i.title)))
    if mode == "year":
        return sorted(items, key=lambda i: (-(int(i.year) if i.year else 0), natural_key(i.title)))
    if mode == "size":
        return sorted(items, key=lambda i: -i.total_size)
    return items


SORT_MODES = ["name", "added", "played", "year", "size"]


# --------------------------------------------------------------------------- #
# Metadata (duration via ffprobe, if installed) and thumbnails
# --------------------------------------------------------------------------- #

def _meta_cache_path() -> Path:
    return cache_dir() / "durations.json"


def item_duration(item: Item) -> float | None:
    """Total running time: an album is the sum of its tracks, a film is its video."""
    if item.is_music:
        from . import audio

        known = [audio.duration(e.path) for e in item.episodes]
        total = sum(d for d in known if d)
        return total or None
    return probe_duration(item.main_video) if item.main_video else None


def probe_duration(video: Path) -> float | None:
    """Return duration in seconds using ffprobe (cached). None if unavailable."""
    if video.suffix.lower() in AUDIO_EXTS:
        from . import audio

        got = audio.duration(video)
        if got:
            return got
    if not shutil.which("ffprobe"):
        return None
    cache_path = _meta_cache_path()
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    try:
        st = video.stat()
    except OSError:
        return None
    ckey = f"{video}|{st.st_size}|{int(st.st_mtime)}"
    if ckey in cache:
        return cache[ckey]
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, timeout=8,
        ).stdout.strip()
        dur = float(out) if out else None
    except (subprocess.SubprocessError, ValueError, OSError):
        dur = None
    cache[ckey] = dur
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache))
    except OSError:
        pass
    return dur


def thumbnail(poster: Path, max_px: int = 420) -> Path | None:
    """Create (or reuse) a small cached copy of a poster. Keeps the TUI fast
    even if your posters are 4000px scans."""
    try:
        from PIL import Image  # local import keeps `nyrun -help` instant

        st = poster.stat()
        digest = hashlib.sha1(f"{poster}|{st.st_size}|{int(st.st_mtime)}|{max_px}".encode()).hexdigest()[:16]
        out = cache_dir() / "thumbs" / f"{digest}.png"
        if out.exists():
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(poster) as im:
            im = im.convert("RGB")
            im.thumbnail((max_px, int(max_px * 1.6)), Image.LANCZOS)
            im.save(out, "PNG", optimize=True)
        return out
    except Exception:  # noqa: BLE001 - a corrupt poster must never crash the UI
        return None


def placeholder_image(size: tuple[int, int] = (300, 450)):
    """A pleasant gradient 'no poster' image generated on the fly."""
    from PIL import Image, ImageDraw

    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        t = y / h
        r = int(40 + 60 * t)
        g = int(24 + 20 * t)
        b = int(70 + 70 * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(img)
    cx, cy = w // 2, h // 2
    r = int(min(w, h) * 0.22)
    d.ellipse((cx - int(r * 1.5), cy - int(r * 1.5), cx + int(r * 1.5), cy + int(r * 1.5)), outline=(255, 183, 213), width=max(3, r // 8))
    d.polygon([(cx - r // 2, cy - r), (cx - r // 2, cy + r), (cx + r, cy)], fill=(255, 183, 213))
    return img


# --------------------------------------------------------------------------- #
# Folder creation + import (cut / copy)
# --------------------------------------------------------------------------- #

def create_structure(path: Path) -> list[Path]:
    """Create the NyRun folder and its category sub-folders. Returns created dirs."""
    created = []
    for d in [path, *(path / c for c in CATEGORIES)]:
        if not d.exists():
            created.append(d)
        d.mkdir(parents=True, exist_ok=True)
    return created


class ImportError_(Exception):
    pass


def sniff_kind(path: Path, limit: int = 400) -> str | None:
    """'video', 'audio' or None - what a file or folder is mostly made of."""
    path = Path(path)
    if path.is_file():
        ext = path.suffix.lower()
        return "audio" if ext in AUDIO_EXTS else "video" if ext in VIDEO_EXTS else None
    audio = video = seen = 0
    try:
        for p in path.rglob("*"):
            seen += 1
            ext = p.suffix.lower()
            audio += ext in AUDIO_EXTS
            video += ext in VIDEO_EXTS
            if seen >= limit or (audio and video):
                break
    except OSError:
        pass
    if video and not audio:
        return "video"
    if audio and not video:
        return "audio"
    return None  # empty or mixed


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    i = 2
    while True:
        cand = dest.with_name(f"{dest.name} ({i})") if dest.is_dir() or not dest.suffix else \
            dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not cand.exists():
            return cand
        i += 1


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def clean_name(name: str) -> str:
    return _BAD_CHARS.sub("", name).strip(" .") or "Untitled"


def import_into_library(
    src: Path,
    root: Path,
    category: str,
    *,
    move: bool = True,
    title: str | None = None,
    poster: Path | None = None,
) -> Path:
    """Move (cut) or copy `src` into root/<category>.

    * A folder is moved/copied as-is.
    * A single video or music file gets its own folder (named after `title` or the file),
      together with same-named subtitles/images sitting next to it.
    * `poster` (optional image) is copied in as poster.<ext>.
    Returns the new item path.
    """
    src = Path(src).expanduser()
    if category not in CATEGORIES:
        raise ImportError_(f"Unknown category: {category}")
    if not src.exists():
        raise ImportError_(f"Not found: {src}")
    target_base = root / category
    target_base.mkdir(parents=True, exist_ok=True)

    try:
        src_res, base_res = src.resolve(), target_base.resolve()
    except OSError as e:
        raise ImportError_(str(e)) from e
    if src_res.parent == base_res:
        raise ImportError_("That item is already in this folder.")
    if src_res == base_res or src_res in base_res.parents:
        raise ImportError_("Can't import a folder into itself.")
    kind = sniff_kind(src)
    if kind == "audio" and category != "Music":
        raise ImportError_("That is music - put it in Music.")
    if kind == "video" and category == "Music":
        raise ImportError_("That is a video - put it in Movies, Series or Shows.")

    if src.is_dir():
        dest = _unique(target_base / clean_name(title or src.name))
        if move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copytree(src, dest)
    elif src.suffix.lower() in (VIDEO_EXTS | AUDIO_EXTS):
        folder = _unique(target_base / clean_name(title or src.stem))
        folder.mkdir(parents=True)
        companions = [src] + [
            p for p in src.parent.iterdir()
            if p.is_file() and p != src and p.stem.lower().startswith(src.stem.lower())
            and p.suffix.lower() in (SUBTITLE_EXTS | IMAGE_EXTS)
        ]
        for f in companions:
            if move:
                shutil.move(str(f), str(folder / f.name))
            else:
                shutil.copy2(f, folder / f.name)
        dest = folder
    else:
        raise ImportError_("Pick a video or music file, or a folder that contains one.")

    if poster:
        poster = Path(poster).expanduser()
        if poster.is_file() and poster.suffix.lower() in IMAGE_EXTS:
            folder = dest if dest.is_dir() else dest.parent
            shutil.copy2(poster, folder / f"poster{poster.suffix.lower()}")
    return dest
