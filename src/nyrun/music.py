"""Listening to music: the queue, a playback clock, and a window-less VLC we steer.

NyRun starts VLC with its text console interface (``-I rc``) and writes commands such
as ``pause`` or ``next`` to it.  VLC's answers are only used as a gentle correction of
NyRun's own clock, so playback keeps working (just with less accurate progress) even if
a VLC build answers differently than expected.
"""

from __future__ import annotations

import atexit
import random
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from . import audio
from .config import Settings
from .library import Item
from .player import _vlc_volume, last_error_lines, launch_audio

LOOP_MODES = ("off", "all", "track")
_INT_LINE = re.compile(r"^(?:>\s*)*(\d+)\s*$")
_LIVE: "set[MusicSession]" = set()


def _close_all() -> None:
    for s in list(_LIVE):
        s.close()


atexit.register(_close_all)


@dataclass
class Track:
    index: int
    path: Path
    label: str
    tags: audio.Tags | None = None

    @property
    def title(self) -> str:
        return (self.tags.title if self.tags and self.tags.title else "") or self.label

    @property
    def artist(self) -> str:
        return self.tags.artist if self.tags else ""

    @property
    def album(self) -> str:
        return self.tags.album if self.tags else ""

    @property
    def duration(self) -> float | None:
        return self.tags.duration if self.tags else None


def mmss(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class MusicSession:
    """One album/folder being played."""

    GRACE = 0.4  # seconds past the known length before NyRun moves its own marker on

    def __init__(self, item: Item, settings: Settings) -> None:
        self.item = item
        self.settings = settings
        self.tracks = [Track(i, e.path, e.label) for i, e in enumerate(item.episodes)]
        self.queue: list[int] = list(range(len(self.tracks)))
        self.qpos = 0
        self.state = "stopped"  # playing | paused | ended | stopped
        self.loop = settings.music_loop if settings.music_loop in LOOP_MODES else "off"
        self.shuffle = settings.music_shuffle
        self.volume = max(0, min(100, settings.music_volume))
        self.controlled = settings.music_controls
        self.meta_version = 0
        self._error = ""
        self.proc: subprocess.Popen | None = None
        self._vlc_base = 0  # queue position that is element 0 of VLC's own playlist
        self._pos_at = 0.0
        self._ref = time.monotonic()
        self._launched = 0.0
        self._pending_seek: float | None = None
        self._last_poll = 0.0
        self._samples: deque = deque(maxlen=16)
        self._meta_started = False

    # ------------------------------------------------------------------ info
    @property
    def current(self) -> Track:
        return self.tracks[self.queue[self.qpos]]

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def active(self) -> bool:
        return self.state in ("playing", "paused")

    def position(self) -> float:
        if self.state == "playing":
            return self._pos_at + (time.monotonic() - self._ref)
        return self._pos_at

    def take_error(self) -> str:
        e, self._error = self._error, ""
        return e

    # --------------------------------------------------------------- queueing
    def _build_queue(self, first: int) -> None:
        n = len(self.tracks)
        if self.shuffle:
            rest = [i for i in range(n) if i != first]
            random.shuffle(rest)
            self.queue = [first] + rest
            self.qpos = 0
        else:
            self.queue = list(range(n))
            self.qpos = first

    def start(self, first: int = 0) -> None:
        first = max(0, min(first, len(self.tracks) - 1))
        self._build_queue(first)
        self._launch(self.qpos)
        if not self._meta_started:
            self._meta_started = True
            threading.Thread(target=self._load_meta, daemon=True, name="nyrun-tags").start()

    def _load_meta(self) -> None:
        for t in self.tracks:
            t.tags = audio.read_tags(t.path)
            self.meta_version += 1

    def _launch(self, qpos: int, seek: float = 0.0) -> None:
        self._kill()
        files = [self.tracks[i].path for i in self.queue[qpos:]]
        self.proc = launch_audio(
            files, self.settings, controlled=self.controlled,
            volume=self.volume, repeat_track=self.loop == "track",
        )
        _LIVE.add(self)
        self.qpos = qpos
        self._vlc_base = qpos
        self.state = "playing"
        self._pos_at = 0.0
        self._ref = self._launched = time.monotonic()
        self._pending_seek = seek if seek > 0 else None
        self._last_poll = 0.0
        self._samples.clear()
        if self.controlled and self.proc.stdout is not None:
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True, name="nyrun-vlc-rc").start()

    def _reader(self, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                m = _INT_LINE.match(raw.decode("utf-8", "replace"))
                if m:
                    self._samples.append((proc, time.monotonic(), int(m.group(1))))
        except (OSError, ValueError):
            pass

    # ---------------------------------------------------------------- process
    def _send(self, *cmds: str) -> bool:
        if not (self.controlled and self.alive and self.proc and self.proc.stdin):
            return False
        try:
            self.proc.stdin.write(("\n".join(cmds) + "\n").encode())
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                try:
                    proc.stdin.write(b"quit\n")
                    proc.stdin.flush()
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        except OSError:
            pass

    def close(self) -> None:
        self.state = "stopped"
        self._kill()
        _LIVE.discard(self)

    stop = close

    # --------------------------------------------------------------- controls
    def toggle_pause(self) -> bool:
        """Pause/resume. False when VLC can't be steered (window mode)."""
        if self.state in ("ended", "stopped"):
            self.start(self.queue[self.qpos] if self.queue else 0)
            return True
        if not (self.controlled and self.alive):
            return False
        if self.state == "playing":
            self._pos_at = self.position()
            self.state = "paused"
        else:
            self._ref = time.monotonic()
            self.state = "playing"
        self._send("pause")
        return True

    def _reset_clock(self, to: float = 0.0) -> None:
        self._pos_at = to
        self._ref = time.monotonic()
        self.state = "playing"

    def next(self) -> bool:
        if self.qpos + 1 >= len(self.queue):
            if self.loop == "all":
                self._launch(0)
                return True
            return False
        if self.controlled and self.alive and self.state != "ended":
            self._send("next")
            self.qpos += 1
            self._reset_clock()
        else:
            self._launch(self.qpos + 1)
        return True

    def prev(self) -> bool:
        if self.position() > 3.0 or self.qpos == 0:
            if self.controlled and self.alive:
                self._send("seek 0")
                self._pos_at = 0.0
                self._ref = time.monotonic()
            else:
                self._launch(self.qpos)
            return True
        target = self.qpos - 1
        if self.controlled and self.alive and target >= self._vlc_base:
            self._send("prev")
            self.qpos = target
            self._reset_clock()
        else:
            self._launch(target)
        return True

    def jump(self, track_index: int) -> None:
        """Play a specific track of the album now."""
        if track_index not in self.queue:
            return
        self._launch(self.queue.index(track_index))

    def seek(self, delta: float) -> bool:
        if not (self.controlled and self.alive and self.active):
            return False
        dur = self.current.duration
        new = max(0.0, self.position() + delta)
        if dur:
            new = min(new, max(0.0, dur - 1))
        if self._send(f"seek {int(new)}"):
            self._pos_at = new
            self._ref = time.monotonic()
            return True
        return False

    def set_volume(self, percent: int) -> int:
        self.volume = max(0, min(100, percent))
        self._send(f"volume {_vlc_volume(self.volume)}")
        return self.volume

    def cycle_loop(self) -> str:
        self.loop = LOOP_MODES[(LOOP_MODES.index(self.loop) + 1) % len(LOOP_MODES)]
        self._send("repeat on" if self.loop == "track" else "repeat off")
        return self.loop

    def toggle_shuffle(self) -> bool:
        self.shuffle = not self.shuffle
        if not self.active:
            return self.shuffle
        cur, pos = self.queue[self.qpos], self.position()
        was_paused = self.state == "paused"
        self._build_queue(cur)
        self._launch(self.qpos, seek=pos)
        if was_paused:
            self._pending_pause = True
        return self.shuffle

    _pending_pause = False

    # ------------------------------------------------------------------- tick
    def tick(self) -> None:
        """Call a few times per second: follows the process and keeps the clock honest."""
        proc = self.proc
        if proc is None:
            return
        code = proc.poll()
        if code is not None:
            self._on_exit(code)
            return
        now = time.monotonic()
        self._consume_samples()
        if self.controlled and self.active and now - self._last_poll >= 1.0:
            self._last_poll = now
            self._send("get_time")
        if self.state == "playing":
            self._advance()

    def _consume_samples(self) -> None:
        while self._samples:
            proc, t, value = self._samples.popleft()
            if proc is not self.proc:
                continue
            if self._pending_seek is not None:
                # first answer = VLC has an input now, so a seek will land
                self._send(f"seek {int(self._pending_seek)}")
                self._pos_at, self._ref = self._pending_seek, time.monotonic()
                self._pending_seek = None
                if self._pending_pause:
                    self._pending_pause = False
                    self._pos_at = self.position()
                    self.state = "paused"
                    self._send("pause")
                continue
            if self.state == "playing" and abs(value - self.position()) <= 3.0:
                self._pos_at, self._ref = float(value), t

    def _advance(self) -> None:
        dur = self.current.duration
        if not dur or self.position() < dur + self.GRACE:
            return
        now = time.monotonic()
        if self.loop == "track":
            self._pos_at, self._ref = 0.0, now
        elif self.qpos + 1 < len(self.queue):
            self.qpos += 1
            self._pos_at, self._ref = 0.0, now
        elif self.loop == "all":
            self._launch(0)
        else:
            self._pos_at = dur
            self.state = "ended"

    def _on_exit(self, code: int) -> None:
        self.proc = None
        _LIVE.discard(self)
        if self.state == "stopped":
            return
        if code != 0 and time.monotonic() - self._launched < 6.0:
            detail = last_error_lines() or "no output"
            self._error = f"VLC exited with code {code}: {detail}"
            self.state = "stopped"
            return
        if self.state == "playing" and self.loop == "all":
            self._launch(0)
            return
        self.state = "ended"
