"""Finding out how much of a film you really watched.

VLC is started with its "rc" console listening on a local port (127.0.0.1 only). While the
movie plays, a small thread asks it for the current time and length every couple of seconds.
Because it is a socket - not a pipe - the movie keeps playing if you quit NyRun.

If your VLC has no rc interface (or the port can't be reached) the watcher simply gives up:
nothing is marked, and `w` still toggles "watched" by hand.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

_NUM = re.compile(r"^\s*(?:>\s*)*(\d+)\s*$")
POLL = 1.5


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class VideoWatcher(threading.Thread):
    """Polls one VLC process.

    entries    - [(state key, file)] in play order (one for a film, several for a queue)
    on_update  - on_update(key, seconds, total, final) - called from this thread
    resume_at  - seconds to jump to in the first entry (None = start normally)
    """

    def __init__(self, proc, port: int, entries: list[tuple[str, Path]],
                 on_update: Callable[[str, float, float, bool], None], resume_at: float | None = None,
                 connect_timeout: float = 25.0) -> None:
        super().__init__(daemon=True, name="nyrun-video-watch")
        self.proc, self.port, self.entries = proc, port, entries
        self.on_update = on_update
        self.resume_at = resume_at
        self.connect_timeout = connect_timeout
        self.sock: socket.socket | None = None
        self._buf = ""
        self.cur = 0
        self.last: tuple[int, float, float] | None = None  # (entry index, seconds, total)
        self.connected = False

    # ----- socket helpers -----
    def _connect(self) -> bool:
        end = time.time() + self.connect_timeout
        while time.time() < end and self.proc.poll() is None:
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=1.0)
                self.sock.settimeout(0.1)
                return True
            except OSError:
                time.sleep(0.4)
        return False

    def _read_lines(self, wait: float) -> list[str]:
        assert self.sock is not None
        end = time.time() + wait
        out: list[str] = []
        while time.time() < end:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                break
            except OSError:
                raise
            if not data:
                raise OSError("closed")
            self._buf += data.decode("utf-8", "replace")
            while True:
                m = re.search(r"[\r\n]", self._buf)
                if not m:
                    break
                out.append(self._buf[: m.start()])
                self._buf = self._buf[m.end():]
            if out:
                break  # got whole lines: hand them over right away
        return out

    def _ask(self, cmd: str) -> int | None:
        assert self.sock is not None
        try:
            self._read_lines(0.0)  # drop anything stale
        except socket.timeout:
            pass
        self.sock.sendall(cmd.encode() + b"\n")
        end = time.time() + 1.5
        while time.time() < end:
            for line in self._read_lines(0.5):
                m = _NUM.match(line)
                if m:
                    return int(m.group(1))
        return None

    def _current_index(self) -> int | None:
        """Which queued file is playing? VLC marks it with * in `playlist`."""
        if len(self.entries) == 1:
            return 0
        assert self.sock is not None
        self.sock.sendall(b"playlist\n")
        lines: list[str] = []
        end = time.time() + 1.0
        while time.time() < end:
            got = self._read_lines(0.4)
            lines += got
            if any("End of playlist" in g for g in got):
                break
        for ln in lines:
            if "*" in ln:
                for n, (_k, path) in enumerate(self.entries):
                    if path.name in ln or path.stem in ln:
                        return n
        return None

    # ----- main loop -----
    def run(self) -> None:
        try:
            if not self._connect():
                return
            self.connected = True
            seeked = self.resume_at is None
            prev_len = -1
            while self.proc.poll() is None:
                t = self._ask("get_time")
                total = self._ask("get_length")
                if t is None or total is None or total <= 0:
                    time.sleep(POLL)
                    continue
                if total != prev_len:
                    idx = self._current_index()
                    if idx is not None:
                        self.cur = idx
                    elif prev_len != -1 and self.cur + 1 < len(self.entries):
                        self.cur += 1  # length changed but no name match: assume the next file
                    prev_len = total
                if not seeked and self.cur == 0:
                    seeked = True
                    if self.resume_at and self.resume_at < total - 10:
                        self.sock.sendall(f"seek {int(self.resume_at)}\n".encode())  # type: ignore[union-attr]
                        time.sleep(0.5)
                        continue
                self.last = (self.cur, float(t), float(total))
                self.on_update(self.entries[self.cur][0], float(t), float(total), False)
                time.sleep(POLL)
        except OSError:
            pass
        finally:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            if self.last is not None:
                n, t, total = self.last
                self.on_update(self.entries[n][0], t, total, True)
