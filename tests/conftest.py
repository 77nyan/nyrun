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
