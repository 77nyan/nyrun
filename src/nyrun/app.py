"""The NyRun Textual applications."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from textual import work
from textual.app import App
from textual.binding import Binding

from . import omarchy, themes
from .config import CATEGORIES, Settings, State
from .library import Item, scan_library
from .music import MusicSession, mmss
from .player import VLCNotFound, last_error_lines, launch
from .watch import VideoWatcher, free_port
from .styles import CSS


class ThemedApp(App):
    """Base app: applies the chosen theme and follows Omarchy live."""

    CSS = CSS
    ENABLE_COMMAND_PALETTE = False
    OMARCHY_POLL_SECONDS = 2.0

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.theme_locked = False  # True while the theme picker is previewing
        self._omarchy_sig: tuple | None = None
        self._omarchy_flip = 0

    # ----- theming -----
    def on_mount(self) -> None:
        self.apply_theme_choice(self.settings.theme)
        self.set_interval(self.OMARCHY_POLL_SECONDS, self._poll_omarchy)

    def apply_theme_choice(self, choice: str) -> None:
        """Switch the whole UI to a theme choice (does not save anything)."""
        res = themes.resolve(choice)
        if res.palette is None:
            self.theme = themes.TERMINAL_THEME
        else:
            if res.follows_omarchy:
                # Textual only repaints on a *changed* theme name, so alternate names
                self._omarchy_flip ^= 1
                name = f"omarchy-{self._omarchy_flip}"
            else:
                name = res.palette.name
            self.register_theme(res.palette.to_theme(name))
            self.theme = name
        self._omarchy_sig = omarchy.signature() if res.follows_omarchy else None
        self._refresh_screens()

    def _refresh_screens(self) -> None:
        for scr in self.screen_stack:
            fn = getattr(scr, "refresh_all", None)
            if callable(fn):
                try:
                    fn()
                except Exception:  # noqa: BLE001 - a repaint hiccup must never crash the UI
                    pass

    def _poll_omarchy(self) -> None:
        if self.theme_locked or self.settings.theme not in ("auto", "omarchy"):
            return
        sig = omarchy.signature()
        if sig is not None and sig != self._omarchy_sig:
            self.apply_theme_choice(self.settings.theme)

    def action_cycle_theme(self) -> None:
        names = [e.choice for e in themes.catalog()]
        cur = self.settings.theme
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        self.settings.theme = nxt
        self.settings.save()
        self.apply_theme_choice(nxt)
        self.notify(f"Theme: {nxt}", markup=False, timeout=2)

    def action_pick_theme(self) -> None:
        from .settings_screens import ThemePickerScreen

        if not isinstance(self.screen, ThemePickerScreen):
            self.push_screen(ThemePickerScreen())

    def action_settings(self) -> None:
        from .settings_screens import SettingsScreen

        if not isinstance(self.screen, SettingsScreen):
            self.push_screen(SettingsScreen())

    def action_toggle_fullscreen(self) -> None:
        self.settings.fullscreen = not self.settings.fullscreen
        self.settings.save()
        self.notify(f"VLC fullscreen: {'on' if self.settings.fullscreen else 'off'}", markup=False, timeout=2)


class ScreenApp(ThemedApp):
    """Runs one screen on its own (used by `nyrun -theme` and `nyrun -vlc`)."""

    standalone = True

    def __init__(self, settings: Settings, screen_factory) -> None:
        super().__init__(settings)
        self._factory = screen_factory

    def on_mount(self) -> None:
        super().on_mount()
        self.push_screen(self._factory())


class NyRunApp(ThemedApp):
    TITLE = "NyRun"
    SUB_TITLE = "your cozy video & music selector"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("t", "cycle_theme", "Theme"),
        Binding("T", "pick_theme", "Pick theme", show=False),
        Binding("comma", "settings", "Settings"),
        Binding("z", "toggle_fullscreen", "Fullscreen"),
        Binding("question_mark", "show_help", "Help"),
    ]

    def __init__(self, settings: Settings, state: State | None = None) -> None:
        super().__init__(settings)
        self.root: Path = settings.root or Path.home() / "NyRun"
        self.state = state or State()
        self._last_save = 0.0
        self._no_tracking = False  # set if this VLC rejects the tracking options
        self.library: dict[str, list[Item]] = {c: [] for c in CATEGORIES}
        self.music: MusicSession | None = None
        self.open_add: Path | str | None = None  # set by `nyrun -add [FILE]`
        self.rescan()

    def on_mount(self) -> None:
        super().on_mount()
        from .screens import HomeScreen

        self.push_screen(HomeScreen())
        if self.open_add is not None:
            from .add_screen import AddScreen

            self.push_screen(AddScreen(self.open_add or None))
        self.set_interval(0.5, self._music_tick)

    def on_unmount(self) -> None:
        self.shutdown_music()

    def rescan(self) -> None:
        self.library = scan_library(self.root)

    # ----- library helpers used by the screens -----
    def progress(self, item: Item) -> tuple[int, int]:
        """(episodes watched, episodes total). Movies are 0/1 or 1/1."""
        if item.is_collection:
            done = sum(1 for e in item.episodes if e.key in self.state.watched)
            return done, len(item.episodes)
        return (1 if item.key in self.state.watched else 0), 1

    def next_episode_index(self, item: Item) -> int:
        """Where 'continue' should start: the first episode after the last one
        you played, else the first unwatched one, else the beginning."""
        keys = [e.key for e in item.episodes]
        last = self.state.last_episode.get(item.key)
        if last in keys:
            if last not in self.state.watched and self.settings.track_progress:
                return keys.index(last)  # you did not finish it: pick it up again
            nxt = keys.index(last) + 1
            if nxt < len(keys):
                return nxt
        for n, k in enumerate(keys):
            if k not in self.state.watched:
                return n
        return 0

    def continue_target(self) -> tuple[Item, int] | None:
        """What 'Continue' should resume: the most recently played series/show that still has
        an episode left, or a film you started but have not marked as finished."""
        found: list[tuple[float, Item, int]] = []
        for cat in CATEGORIES:
            for item in self.library.get(cat, []):
                played = self.state.last_played.get(item.key)
                if not played or item.is_music:
                    continue
                if item.is_collection:
                    last = self.state.last_episode.get(item.key)
                    keys = [e.key for e in item.episodes]
                    if last in keys:
                        n = keys.index(last)
                        if last not in self.state.watched and self.settings.track_progress:
                            found.append((played, item, n))  # not finished: resume this episode
                        elif n + 1 < len(keys):
                            found.append((played, item, n + 1))
                elif item.key not in self.state.watched:
                    found.append((played, item, 0))
        if not found:
            return None
        _, item, idx = max(found, key=lambda f: f[0])
        return item, idx

    # ----- music -----
    def play_music(self, item: Item, start: int = 0) -> None:
        """Start an album (hidden VLC + the Now Playing screen). Same album again = just show it."""
        cur = self.music
        if cur is not None and cur.item.key == item.key and cur.active and start == 0:
            self.show_now_playing()
            return
        if cur is not None:
            cur.close()
            self.music = None
        session = MusicSession(item, self.settings)
        try:
            session.start(start)
        except VLCNotFound as e:
            self.notify(str(e), severity="error", timeout=12, markup=False)
            return
        except OSError as e:
            self.notify(f"Could not start VLC: {e}", severity="error", timeout=12, markup=False)
            return
        self.music = session
        self.state.mark_played(item.key)
        if self.settings.auto_watched:
            self.state.set_watched(item.key, True)
        self.show_now_playing()

    def show_now_playing(self) -> None:
        from .music_screen import NowPlayingScreen

        if self.music is None:
            self.notify("Nothing is playing. Open Music and pick an album.", severity="warning", markup=False)
        elif not isinstance(self.screen, NowPlayingScreen):
            self.push_screen(NowPlayingScreen())

    def _music_tick(self) -> None:
        m = self.music
        if m is None:
            return
        m.tick()
        err = m.take_error()
        if err:
            self.notify(
                f"{err}\nTry Settings (,) and turn off 'Control music from NyRun'.",
                severity="error", timeout=15, markup=False,
            )

    def shutdown_music(self) -> None:
        if self.music is not None:
            self.music.close()
            self.music = None

    # ----- playing -----
    def play(self, item: Item, episode: int = 0, *, only_one: bool = False) -> None:
        eps = item.episodes[episode:]
        if not eps:
            return
        files = [eps[0].path] if (only_one or not item.is_collection) else [e.path for e in eps]
        missing = [f for f in files if not f.exists()]
        if missing:
            self.notify(f"File is missing: {missing[0].name}. Is the drive connected?", severity="error", markup=False)
            return
        tracking = self.settings.track_progress and not self._no_tracking
        port = free_port() if tracking else None
        try:
            proc = launch(files, self.settings, port)
        except VLCNotFound as e:
            self.notify(str(e), severity="error", timeout=12, markup=False)
            return
        except OSError as e:
            self.notify(f"Could not start VLC: {e}", severity="error", timeout=12, markup=False)
            return
        self._watch_vlc(proc, files, bool(port))
        if self.music is not None and self.music.state == "playing":
            self.music.toggle_pause()  # don't talk over the film
        first = eps[0]
        key = first.key if item.is_collection else item.key
        self.state.mark_played(key, parent_key=item.key if item.is_collection else None, episode=first.key)
        resume = self.state.resume_point(key) if self.settings.resume else None
        if tracking and port:
            entries = [(e.key if item.is_collection else item.key, e.path) for e in eps[: len(files)]]
            parent = item.key if item.is_collection else None
            VideoWatcher(
                proc, port, entries,
                lambda k, t, total, final, _p=parent: self.call_from_thread(self._progress, k, t, total, final, _p),
                resume_at=resume,
            ).start()
        what = f"{item.title} · {first.label}" if item.is_collection else item.title
        if resume:
            self.notify(f"▶ {what} - resuming at {mmss(resume)}", markup=False)
        else:
            self.notify(f"▶ {what}", markup=False)

    def _progress(self, key: str, seconds: float, total: float, final: bool, parent: str | None) -> None:
        """Called (on the UI thread) as VLC reports how far you are."""
        st = self.state
        if parent:
            st.last_episode[parent] = key  # 'Continue' follows the episode you are really on
            st.last_played[parent] = time.time()
        if total > 0 and seconds / total * 100 >= self.settings.watched_at:
            if key not in st.watched:
                st.set_watched(key, True)
                self.notify("✓ Marked as watched", markup=False)
        elif seconds >= 1:
            st.set_position(key, seconds, total)
        now = time.time()
        if final or now - self._last_save > 10:
            self._last_save = now
            st.save()

    @work(thread=True, group="vlc-watch")
    def _watch_vlc(self, proc: subprocess.Popen, files: list[Path] | None = None, tracked: bool = False) -> None:
        """If VLC dies within a few seconds, tell the user why (usually a bad setting)."""
        try:
            code = proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            return  # still running = it started fine
        if code != 0:
            detail = last_error_lines() or "no output"
            low = detail.lower()
            if tracked and files and ("rc-" in low or "extraintf" in low):
                # this VLC does not understand the progress-tracking options: play without them
                self._no_tracking = True
                try:
                    again = launch(files, self.settings)
                except (OSError, VLCNotFound):
                    again = None
                if again is not None:
                    try:
                        code = again.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        code = 0
                    if code == 0:
                        self.call_from_thread(
                            self.notify,
                            "Your VLC does not support progress tracking, so it is switched off for now.\n"
                            "The film is playing; use w to mark it watched.",
                            severity="warning", timeout=10, markup=False,
                        )
                        return
                    detail = last_error_lines() or "no output"
            self.call_from_thread(
                self.notify,
                f"VLC exited with code {code}: {detail}\nTry Settings (,) > VLC preset > Default.",
                severity="error",
                timeout=15,
                markup=False,
            )

    def open_in_file_manager(self, path: Path) -> None:
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", str(path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
        except (OSError, FileNotFoundError):
            self.notify(f"Could not open a file manager. Path: {path}", severity="warning", markup=False)

    # ----- app-level actions -----
    def action_show_help(self) -> None:
        from .screens import HelpScreen

        if not isinstance(self.screen, HelpScreen):
            self.push_screen(HelpScreen())
