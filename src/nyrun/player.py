"""Finding, configuring and launching VLC."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import Settings, cache_dir


class VLCNotFound(Exception):
    pass


FLATPAK_ID = "org.videolan.VLC"
QT_PLATFORMS = {"xcb": "xcb", "wayland": "wayland"}
CONTINUE_VALUES = {"never": 0, "ask": 1, "always": 2}  # VLC's --qt-continue


# --------------------------------------------------------------------------- #
# Locating VLC
# --------------------------------------------------------------------------- #

def _flatpak_vlc() -> list[str] | None:
    if not shutil.which("flatpak"):
        return None
    try:
        r = subprocess.run(["flatpak", "info", FLATPAK_ID], capture_output=True, timeout=5)
    except (subprocess.SubprocessError, OSError):
        return None
    return ["flatpak", "run", FLATPAK_ID] if r.returncode == 0 else None


def find_vlc(custom: str = "", launcher: str = "auto") -> list[str]:
    """Return the command prefix that starts VLC (e.g. ['vlc']).

    launcher: "auto" tries everything, "native" skips Flatpak, "flatpak" uses only Flatpak.
    """
    if launcher == "flatpak":
        fp = _flatpak_vlc()
        if fp:
            return fp
        raise VLCNotFound("Launcher is set to Flatpak, but org.videolan.VLC is not installed.")

    if custom:
        p = Path(custom).expanduser()
        if p.exists():
            return [str(p)]
        found = shutil.which(custom)
        if found:
            return [found]

    env = os.environ.get("NYRUN_VLC")
    if env and (Path(env).exists() or shutil.which(env)):
        return [env]

    for name in ("vlc", "cvlc"):
        found = shutil.which(name)
        if found:
            return [found]

    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.append(Path("/Applications/VLC.app/Contents/MacOS/VLC"))
    if sys.platform.startswith("win"):
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                candidates.append(Path(base) / "VideoLAN" / "VLC" / "vlc.exe")
    for c in candidates:
        if c.exists():
            return [str(c)]

    if launcher != "native":
        fp = _flatpak_vlc()
        if fp:
            return fp
    raise VLCNotFound(
        "VLC was not found. Install it (https://www.videolan.org) or set its path in NyRun: "
        "press , for Settings (or run: nyrun -vlc)."
    )


def is_flatpak(cmd: list[str]) -> bool:
    return len(cmd) >= 2 and cmd[0] == "flatpak"


# --------------------------------------------------------------------------- #
# Building the command line
# --------------------------------------------------------------------------- #

def _rate_ok(text: str) -> float | None:
    try:
        v = float(text)
    except ValueError:
        return None
    return v if 0.1 <= v <= 8 else None


def vlc_args(s: Settings) -> list[str]:
    """Every VLC option implied by the settings, in a stable order."""
    a: list[str] = []
    if s.fullscreen:
        a.append("--fullscreen")
    if s.vlc_play_and_exit:
        a.append("--play-and-exit")
    if s.vlc_loop:
        a.append("--loop")
    if s.vlc_start_paused:
        a.append("--start-paused")
    if s.vlc_on_top:
        a.append("--video-on-top")
    if not s.vlc_title_osd:
        a.append("--no-video-title-show")
    if s.vlc_quiet:
        a += ["--no-qt-privacy-ask", "--no-qt-recentplay"]
    if s.vlc_hwdec and s.vlc_hwdec != "auto":
        a.append(f"--avcodec-hw={s.vlc_hwdec}")
    if s.vlc_vout.strip():
        a.append(f"--vout={s.vlc_vout.strip()}")
    if s.vlc_aout.strip():
        a.append(f"--aout={s.vlc_aout.strip()}")
    if s.vlc_one_instance == "on":
        a.append("--one-instance")
    elif s.vlc_one_instance == "off":
        a.append("--no-one-instance")
    if s.vlc_sub_lang.strip():
        a.append(f"--sub-language={s.vlc_sub_lang.strip()}")
    if s.vlc_audio_lang.strip():
        a.append(f"--audio-language={s.vlc_audio_lang.strip()}")
    if not s.vlc_sub_autodetect:
        a.append("--no-sub-autodetect-file")
    if s.vlc_continue in CONTINUE_VALUES:
        a.append(f"--qt-continue={CONTINUE_VALUES[s.vlc_continue]}")
    rate = _rate_ok(s.vlc_rate) if s.vlc_rate.strip() else None
    if rate is not None and rate != 1.0:
        a.append(f"--rate={rate:g}")
    a.extend(s.extra_vlc_args)
    return a


def vlc_env(s: Settings) -> dict[str, str]:
    """Extra environment variables for VLC (only what differs from yours)."""
    env: dict[str, str] = {}
    if s.vlc_qt_platform in QT_PLATFORMS:
        env["QT_QPA_PLATFORM"] = QT_PLATFORMS[s.vlc_qt_platform]
    return env


def build_command(files: list[Path], vlc: list[str], s: Settings) -> list[str]:
    cmd = list(vlc)
    if is_flatpak(cmd) and "QT_QPA_PLATFORM" in vlc_env(s):
        # a Flatpak sandbox does not inherit our environment
        cmd[2:2] = [f"--env=QT_QPA_PLATFORM={vlc_env(s)['QT_QPA_PLATFORM']}"]
    cmd.extend(vlc_args(s))
    cmd.append("--")
    cmd.extend(str(f) for f in files)
    return cmd


def preview_command(s: Settings, placeholder: str = "<your file>") -> str:
    """Human-readable command line, for the settings screen."""
    try:
        vlc = find_vlc(s.vlc_path, s.vlc_launcher)
    except VLCNotFound:
        vlc = ["vlc"]
    cmd = build_command([Path(placeholder)], vlc, s)
    env = [] if is_flatpak(vlc) else [f"{k}={v}" for k, v in vlc_env(s).items()]
    return " ".join(env + [shlex.quote(c) if c != placeholder else c for c in cmd])


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #

def log_path() -> Path:
    return cache_dir() / "vlc-last.log"


def watch_args(port: int, s: Settings) -> list[str]:
    """Options that let NyRun ask this VLC how far along it is (see watch.py)."""
    a = ["--extraintf=rc", f"--rc-host=127.0.0.1:{port}"]
    if s.vlc_one_instance != "on":
        a.append("--no-one-instance")  # a hand-off to a running VLC would never answer us
    return a


def launch(files: list[Path], s: Settings, port: int | None = None) -> subprocess.Popen:
    """Start VLC detached from the TUI so closing NyRun never kills the movie.

    VLC's own error output goes to a log file so a bad setting can be reported.
    With `port`, VLC also listens there for NyRun's progress questions.
    """
    vlc = find_vlc(s.vlc_path, s.vlc_launcher)
    cmd = build_command(files, vlc, s)
    if port:
        cmd[len(cmd) - len(files) - 1:len(cmd) - len(files) - 1] = watch_args(port, s)  # before "--"
    env = dict(os.environ)
    env.update(vlc_env(s))
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path(), "wb")
    except OSError:
        log = subprocess.DEVNULL  # type: ignore[assignment]
    kwargs: dict = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        if log is not subprocess.DEVNULL:
            log.close()  # the child keeps its own copy of the descriptor


# --------------------------------------------------------------------------- #
# Music: VLC without a window, driven through its "rc" console interface
# --------------------------------------------------------------------------- #

def audio_args(s: Settings, *, controlled: bool, volume: int | None = None, repeat_track: bool = False) -> list[str]:
    """VLC options for listening (no video options: they mean nothing for audio)."""
    a: list[str] = []
    if controlled:
        # -I rc: a text console NyRun talks to through a pipe. --no-one-instance matters:
        # otherwise a VLC window you already have open would swallow the files and exit.
        a += ["-I", "rc", "--rc-fake-tty", "--no-video", "--no-one-instance"]
    a.append("--play-and-exit")
    if repeat_track:
        a.append("--repeat")
    if volume is not None:
        a.append(f"--volume={_vlc_volume(volume)}")
    if s.vlc_aout.strip():
        a.append(f"--aout={s.vlc_aout.strip()}")
    if s.vlc_quiet:
        a += ["--no-qt-privacy-ask", "--no-qt-recentplay"]
    rate = _rate_ok(s.vlc_rate) if s.vlc_rate.strip() else None
    if rate is not None and rate != 1.0:
        a.append(f"--rate={rate:g}")
    a.extend(s.extra_vlc_args)
    return a


def _vlc_volume(percent: int) -> int:
    """VLC's scale is 0-512 where 256 is 100%."""
    return max(0, min(512, round(percent * 2.56)))


def build_audio_command(files: list[Path], vlc: list[str], s: Settings, *, controlled: bool,
                        volume: int | None = None, repeat_track: bool = False) -> list[str]:
    cmd = list(vlc)
    cmd.extend(audio_args(s, controlled=controlled, volume=volume, repeat_track=repeat_track))
    cmd.append("--")
    cmd.extend(str(f) for f in files)
    return cmd


def launch_audio(files: list[Path], s: Settings, *, controlled: bool, volume: int | None = None,
                 repeat_track: bool = False) -> subprocess.Popen:
    """Start VLC on these files. If `controlled`, stdin/stdout are pipes (see music.MusicSession)."""
    vlc = find_vlc(s.vlc_path, s.vlc_launcher)
    cmd = build_audio_command(files, vlc, s, controlled=controlled, volume=volume, repeat_track=repeat_track)
    env = dict(os.environ)
    env.update(vlc_env(s))
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path(), "wb")
    except OSError:
        log = subprocess.DEVNULL  # type: ignore[assignment]
    kwargs: dict = dict(
        stdin=subprocess.PIPE if controlled else subprocess.DEVNULL,
        stdout=subprocess.PIPE if controlled else log,
        stderr=log,
        env=env,
    )
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(cmd, **kwargs)
    finally:
        if log is not subprocess.DEVNULL:
            log.close()


def last_error_lines(n: int = 3) -> str:
    """The last few meaningful lines VLC wrote, for 'VLC exited' messages."""
    try:
        lines = [ln.strip() for ln in log_path().read_text(errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return " | ".join(lines[-n:])


def version(s: Settings) -> str:
    """First line of `vlc --version` (also proves the launcher works)."""
    vlc = find_vlc(s.vlc_path, s.vlc_launcher)
    env = dict(os.environ)
    env.update(vlc_env(s))
    try:
        out = subprocess.run(
            [*vlc, "--version"], capture_output=True, text=True, timeout=15, env=env
        )
    except subprocess.TimeoutExpired as e:
        raise VLCNotFound("VLC did not answer within 15 seconds.") from e
    except OSError as e:
        raise VLCNotFound(str(e)) from e
    text = (out.stdout or out.stderr).strip()
    if not text:
        raise VLCNotFound(f"VLC printed nothing (exit code {out.returncode}).")
    return text.splitlines()[0]
