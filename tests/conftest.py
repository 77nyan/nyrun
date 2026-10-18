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
