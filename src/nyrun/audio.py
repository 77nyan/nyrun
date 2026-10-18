"""Audio metadata and embedded cover art (via mutagen, which is optional at runtime)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import cache_dir

try:  # mutagen is a dependency, but a missing/broken install must never crash the TUI
    import mutagen
    from mutagen.id3 import ID3
except Exception:  # noqa: BLE001
    mutagen = None  # type: ignore[assignment]
    ID3 = None  # type: ignore[assignment,misc]


@dataclass(frozen=True)
class Tags:
    title: str = ""
    artist: str = ""
    album: str = ""
    number: int = 0
    duration: float | None = None


def _first(tags, *keys: str) -> str:
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return str(v[0] if isinstance(v, (list, tuple)) else v).strip()
    return ""


def _track_number(text: str) -> int:
    try:
        return int(text.split("/")[0])
    except ValueError:
        return 0


@lru_cache(maxsize=4096)
def _read(path: str, size: int, mtime: float) -> Tags:
    if mutagen is None:
        return Tags()
    duration = None
    tags = None
    try:
        f = mutagen.File(path, easy=True)
        if f is not None:
            tags = f.tags
            length = getattr(getattr(f, "info", None), "length", None)
            duration = float(length) if length else None
    except Exception:  # noqa: BLE001 - damaged file or exotic format
        pass
    if tags is None and path.lower().endswith((".mp3", ".wav", ".aiff")):
        try:
            from mutagen.easyid3 import EasyID3

            tags = EasyID3(path)
        except Exception:  # noqa: BLE001
            tags = None
    if tags is None:
        return Tags(duration=duration)
    return Tags(
        title=_first(tags, "title"),
        artist=_first(tags, "artist", "albumartist"),
        album=_first(tags, "album"),
        number=_track_number(_first(tags, "tracknumber")),
        duration=duration,
    )


def read_tags(path: Path) -> Tags:
    """Title/artist/album/duration of one audio file (cached per file version)."""
    try:
        st = path.stat()
    except OSError:
        return Tags()
    return _read(str(path), st.st_size, st.st_mtime)


def duration(path: Path) -> float | None:
    return read_tags(path).duration


# --------------------------------------------------------------------------- #
# Embedded cover art
# --------------------------------------------------------------------------- #

def _embedded_bytes(path: Path) -> bytes | None:
    if mutagen is None:
        return None
    suffix = path.suffix.lower()
    try:
        if suffix in (".mp3", ".wav", ".aiff"):
            frames = ID3(str(path)).getall("APIC")
            if frames:
                front = [f for f in frames if getattr(f, "type", 3) == 3]
                return bytes((front or frames)[0].data)
            return None
        f = mutagen.File(str(path))
        if f is None:
            return None
        pics = getattr(f, "pictures", None)  # FLAC
        if pics:
            return bytes(pics[0].data)
        tags = f.tags
        if tags is not None and "covr" in tags:  # MP4 / M4A
            return bytes(tags["covr"][0])
        if tags is not None and "metadata_block_picture" in tags:  # Ogg / Opus
            import base64

            from mutagen.flac import Picture

            return bytes(Picture(base64.b64decode(tags["metadata_block_picture"][0])).data)
    except Exception:  # noqa: BLE001
        return None
    return None


def cached_cover(path: Path) -> Path | None:
    """Extract a track's embedded cover to the cache and return that file (or None)."""
    try:
        st = path.stat()
    except OSError:
        return None
    digest = hashlib.sha1(f"{path}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    folder = cache_dir() / "covers"
    for ext in (".jpg", ".png"):
        hit = folder / f"{digest}{ext}"
        if hit.exists():
            return hit
    miss = folder / f"{digest}.none"
    if miss.exists():
        return None
    data = _embedded_bytes(path)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not data:
            miss.write_bytes(b"")
            return None
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
        out = folder / f"{digest}{ext}"
        out.write_bytes(data)
        return out
    except OSError:
        return None
