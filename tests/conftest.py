import os
import stat
import sys
import wave
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Never touch the real config/cache/home of whoever runs the tests."""
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))  # else a real Omarchy install leaks in
    monkeypatch.delenv("NYRUN_OMARCHY_DIR", raising=False)
    monkeypatch.delenv("NYRUN_VLC", raising=False)
    return home


def make_poster(path: Path, size=(200, 300), color=(200, 80, 120)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def make_wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> None:
    """A real (silent) audio file, so track lengths can be read. A low `rate` keeps long ones tiny."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


@pytest.fixture
def library(tmp_path):
    """A small NyRun library on disk."""
    root = tmp_path / "NyRun"
    for cat in ("Movies", "Series", "Shows", "Music"):
        (root / cat).mkdir(parents=True)
    # movies
    for name in ("Inception (2010)", "Alien (1979)", "Blade Runner 2049 (2017)"):
        d = root / "Movies" / name
        d.mkdir()
        (d / "film.mp4").write_bytes(b"0" * 2048)
        make_poster(d / "poster.jpg")
    (root / "Movies" / "Alien (1979)" / "film.srt").write_text("1\n")
    # a movie folder with a sample and no poster
    d = root / "Movies" / "No Poster"
    d.mkdir()
    (d / "movie.mkv").write_bytes(b"0" * 4096)
    (d / "sample.mkv").write_bytes(b"0" * 100)
    # loose file with sibling image
    (root / "Movies" / "Loose Film (2001).avi").write_bytes(b"0" * 500)
    make_poster(root / "Movies" / "Loose Film (2001).png")
    # hidden stuff must be ignored
    (root / "Movies" / ".hidden").mkdir()
    (root / "Movies" / ".hidden" / "x.mp4").write_bytes(b"0")
    # series with seasons, natural sort matters (ep2 < ep10)
    s = root / "Series" / "Cowboy Bebop"
    for season in ("Season 1", "Season 2"):
        (s / season).mkdir(parents=True)
    for i in (1, 2, 10):
        (s / "Season 1" / f"ep{i}.mkv").write_bytes(b"0" * 300)
    (s / "Season 2" / "ep1.mkv").write_bytes(b"0" * 300)
    make_poster(s / "cover.png")
    # shows
    sh = root / "Shows" / "Planet Earth"
    sh.mkdir()
    for i in (1, 2):
        (sh / f"E{i}.mp4").write_bytes(b"0" * 200)
    # music: an album with a cover, tracks of 20 seconds each
    al = root / "Music" / "Moon Songs (2020)"
    for i in (1, 2, 10):
        make_wav(al / f"{i:02d} - Track {i}.wav", 20.0, rate=100)
    make_poster(al / "cover.png", size=(300, 300))
    # an album without a cover, and a loose single with a sibling image
    make_wav(root / "Music" / "No Cover Album" / "a.wav", 1.0)
    make_wav(root / "Music" / "Single Song.wav", 1.0)
    make_poster(root / "Music" / "Single Song.png", size=(300, 300))
    return root


@pytest.fixture
def fake_vlc(tmp_path, monkeypatch):
    """A 'VLC' that just records its arguments, one call per line."""
    log = tmp_path / "vlc_calls.log"
    script = tmp_path / "fakevlc"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    return log


@pytest.fixture
def fake_music_vlc(tmp_path, monkeypatch):
    """A 'VLC' that speaks just enough of the rc console: logs args and every command,
    answers get_time, quits on quit/EOF. Returns the log path."""
    log = tmp_path / "rc.log"
    script = tmp_path / "fakevlc-rc"
    script.write_text(f"""#!{sys.executable}
import sys, time
log = open({str(log)!r}, "a", buffering=1)
log.write("ARGS " + " ".join(sys.argv[1:]) + "\\n")
t0 = time.time()
for line in sys.stdin:
    cmd = line.strip()
    log.write("CMD " + cmd + "\\n")
    if cmd == "get_time":
        print(int(time.time() - t0), flush=True)
    elif cmd in ("quit", "shutdown"):
        break
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    return log


FAKE_WATCH_SCRIPT = r"""#!__PY__
import os, re, socket, sys, time
log = open(__LOG__, "a", buffering=1)
log.write("ARGS " + " ".join(sys.argv[1:]) + "\n")
m = next((re.match(r"--rc-host=(.+):(\d+)", a) for a in sys.argv if a.startswith("--rc-host=")), None)
if not m:
    sys.exit(0)
length = float(os.environ.get("FAKE_LEN", "100"))
stop = float(os.environ.get("FAKE_STOP", "50"))
speed = float(os.environ.get("FAKE_SPEED", "40"))
files = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind((m.group(1), int(m.group(2))))
srv.listen(1)
srv.settimeout(20)
conn, _ = srv.accept()
conn.settimeout(0.05)
t0 = time.time()
offset = 0.0
def pos():
    return min(stop, offset + (time.time() - t0) * speed)
reached = None
while True:
    if pos() >= stop:  # the "user" quit here; linger briefly so a slow poller still sees the final position
        reached = reached or time.time()
        if time.time() - reached > 1.5:
            break
    try:
        data = conn.recv(1024)
    except socket.timeout:
        continue
    if not data:
        break
    for cmd in data.decode().split("\n"):
        cmd = cmd.strip()
        if not cmd:
            continue
        log.write("CMD " + cmd + "\n")
        if cmd == "get_time":
            conn.sendall(("%d\r\n" % int(pos())).encode())
        elif cmd == "get_length":
            conn.sendall(("%d\r\n" % int(length)).encode())
        elif cmd == "playlist":
            body = "".join(("|   *%d - %s\r\n" if n == 1 else "|    %d - %s\r\n") % (n, os.path.basename(f))
                           for n, f in enumerate(files, 1))
            conn.sendall(("+----[ Playlist ]\r\n" + body + "+----[ End of playlist ]\r\n").encode())
        elif cmd.startswith("seek"):
            offset = float(cmd.split()[1]) - (time.time() - t0) * speed
log.write("EXIT\n")
"""


@pytest.fixture
def fake_watch_vlc(tmp_path, monkeypatch):
    """A 'VLC' that listens on --rc-host like the real rc interface and pretends to play.

    Call it as fake_watch_vlc(length=100, stop=50, speed=40): the film is `length` seconds long,
    time runs `speed` times faster than real life, and the 'user' quits when it reaches `stop`.
    Returns the log path (ARGS line, then CMD lines).
    """
    import nyrun.watch as watch

    monkeypatch.setattr(watch, "POLL", 0.1)
    log = tmp_path / "watch.log"
    script = tmp_path / "fakevlc-watch"
    script.write_text(FAKE_WATCH_SCRIPT.replace("__PY__", sys.executable).replace("__LOG__", repr(str(log))))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))

    def configure(length=100, stop=50, speed=40):
        monkeypatch.setenv("FAKE_LEN", str(length))
        monkeypatch.setenv("FAKE_STOP", str(stop))
        monkeypatch.setenv("FAKE_SPEED", str(speed))
        return log

    return configure


# --------------------------------------------------------------------------- #
# a tiny web server for the URL engine tests
# --------------------------------------------------------------------------- #
import http.server  # noqa: E402
import shutil  # noqa: E402
import socketserver  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402


@dataclass
class Route:
    body: bytes = b""
    ctype: str = "text/html; charset=utf-8"
    status: int = 200
    headers: dict = field(default_factory=dict)
    ranges: bool = True  # honour Range requests
    head: bool = True  # answer HEAD
    delay: float = 0.0  # seconds between 16 KB chunks (to test cancelling)


class MediaServer:
    def __init__(self):
        self.routes: dict[str, Route] = {}
        self.hits: dict[str, int] = {}
        self.requests: list[tuple[str, str, dict]] = []  # (method, path, headers)
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # silence
                pass

            def _serve(self, method):
                path = self.path.split("?")[0]
                outer.hits[path] = outer.hits.get(path, 0) + 1
                outer.requests.append((method, self.path, {k.lower(): v for k, v in self.headers.items()}))
                r = outer.routes.get(path)
                if r is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method == "HEAD" and not r.head:
                    self.send_response(405)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body, status = r.body, r.status
                extra = dict(r.headers)
                rng = self.headers.get("Range")
                if rng and r.ranges and status == 200:
                    import re

                    m = re.match(r"bytes=(\d+)-(\d*)", rng)
                    if m:
                        a = int(m.group(1))
                        b = int(m.group(2)) if m.group(2) else len(body) - 1
                        if a >= len(body):
                            self.send_response(416)
                            self.send_header("Content-Range", f"bytes */{len(body)}")
                            self.send_header("Content-Length", "0")
                            self.end_headers()
                            return
                        b = min(b, len(body) - 1)
                        extra["Content-Range"] = f"bytes {a}-{b}/{len(body)}"
                        body, status = body[a : b + 1], 206
                self.send_response(status)
                self.send_header("Content-Type", r.ctype)
                if "Content-Length" not in extra:
                    self.send_header("Content-Length", str(len(body)))
                for k, v in extra.items():
                    self.send_header(k, v)
                self.end_headers()
                if method == "HEAD":
                    return
                try:
                    if r.delay:
                        import time

                        for i in range(0, len(body), 16384):
                            self.wfile.write(body[i : i + 16384])
                            self.wfile.flush()
                            time.sleep(r.delay)
                    else:
                        self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                self._serve("GET")

            def do_HEAD(self):
                self._serve("HEAD")

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.srv = Server(("127.0.0.1", 0), Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def add(self, path: str, body=b"", ctype="text/html; charset=utf-8", **kw) -> str:
        if isinstance(body, str):
            body = body.encode()
        self.routes[path] = Route(body, ctype, **kw)
        return self.url(path)

    def page(self, path: str, html: str, **kw) -> str:
        return self.add(path, html, "text/html; charset=utf-8", **kw)

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def web():
    s = MediaServer()
    yield s
    s.close()


MP4_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 40  # looks like an mp4 (10 KB)
PNG_BYTES = None


def png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (64, 36), (200, 60, 120)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def sample_media(tmp_path_factory):
    """A real 2-second clip (mp4) and an HLS version of it, made with ffmpeg. Skips when ffmpeg is missing."""
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg is not installed")
    d = tmp_path_factory.mktemp("media")
    mp4 = d / "clip.mp4"
    base = [ff, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-g", "10", "-c:a", "aac", "-shortest"]
    r = subprocess.run(base + [str(mp4)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"ffmpeg cannot make test media: {r.stderr[-200:]}")
    hls = d / "hls"
    hls.mkdir()
    r = subprocess.run(base + ["-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod", "-hls_segment_filename", str(hls / "seg%d.ts"), str(hls / "index.m3u8")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"ffmpeg cannot make an HLS test: {r.stderr[-200:]}")
    return {"mp4": mp4, "hls": hls}
