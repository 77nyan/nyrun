"""The Now Playing screen: a spinning record, progress, and the track list."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, OptionList, Static
from textual.widgets.option_list import Option

from . import audio
from .disc import Disc
from .music import LOOP_MODES, MusicSession, mmss
from .widgets import theme_pair

LOOP_LABEL = {"off": "off", "all": "repeat album", "track": "repeat track"}


class NowPlayingScreen(Screen):
    BINDINGS = [
        Binding("space", "toggle_pause", "Pause / Play"),
        Binding("n", "next", "Next"),
        Binding("p", "prev", "Previous"),
        Binding("left", "seek(-10)", "-10s", show=False),
        Binding("right", "seek(10)", "+10s", show=False),
        Binding("plus,equals_sign", "volume(5)", "Volume +"),
        Binding("minus", "volume(-5)", "Volume -"),
        Binding("l", "cycle_loop", "Repeat"),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("x", "stop", "Stop"),
        Binding("o", "open_folder", "Folder", show=False),
        Binding("escape,backspace", "back", "Back"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._shown_cur: int | None = None
        self._shown_meta = -1
        self._shown_state = ""
        self._covers: dict[int, Path | None] = {}

    @property
    def session(self) -> MusicSession | None:
        return self.app.music

    # ----- layout -----
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="np-body"):
            with Vertical(id="np-left"):
                with Vertical(id="np-disc-box"):
                    yield Disc(40, id="np-disc")
                yield Label(id="np-title", markup=False)
                yield Static(id="np-sub")
                yield Static(id="np-time")
                yield Static(id="np-status")
            with Vertical(id="np-right"):
                yield Label("Tracks   (Enter plays one)", id="np-tracks-h")
                yield OptionList(id="np-tracks")
        yield Footer()

    def on_mount(self) -> None:
        m = self.session
        if m is None:
            self.app.pop_screen()
            return
        self.sub_title = m.item.title
        self.query_one("#np-tracks", OptionList).focus()
        self.set_interval(0.25, self._refresh)
        self.call_after_refresh(self._layout)

    def on_resize(self) -> None:
        self.call_after_refresh(self._layout)
        self._shown_meta = -1  # re-flow the track rows

    def on_screen_resume(self) -> None:
        self.query_one(Disc).resume_timer()
        self._refresh()

    def on_screen_suspend(self) -> None:
        self.query_one(Disc).pause_timer()

    def refresh_all(self) -> None:  # theme changed
        try:
            self.query_one(Disc).refresh_theme()
        except Exception:  # noqa: BLE001
            pass

    def _layout(self) -> None:
        left = self.query_one("#np-left")
        w = left.size.width
        if w <= 0:
            return
        rows_free = self.size.height - 13
        self.query_one(Disc).set_px(max(16, min(44, w - 2, rows_free * 2)))

    # ----- data -> widgets -----
    def _cover(self, m: MusicSession) -> Path | None:
        if m.item.poster:
            return m.item.poster
        idx = m.current.index
        if idx not in self._covers:
            self._covers[idx] = audio.cached_cover(m.current.path)
        return self._covers[idx]

    def _row(self, m: MusicSession, i: int, width: int) -> Text:
        t = m.tracks[i]
        playing_here = m.active and m.current.index == i
        line = Text(no_wrap=True, overflow="ellipsis")
        if playing_here:
            line.append("▶ " if m.state == "playing" else "❚❚", style="bold " + theme_pair(self.app)[0])
        else:
            line.append("  ")
        line.append(f"{i + 1:>3}  ", style="dim")
        room = max(10, width - 16)
        title = t.title if len(t.title) <= room else t.title[: room - 1] + "…"
        line.append(title.ljust(room), style="bold" if playing_here else "")
        line.append(f" {mmss(t.duration):>6}", style="dim")
        return line

    def _fill_tracks(self, m: MusicSession) -> None:
        ol = self.query_one("#np-tracks", OptionList)
        width = ol.size.width - 4 if ol.size.width else 60
        cur = m.current.index
        if ol.option_count != len(m.tracks):
            ol.clear_options()
            ol.add_options([Option(self._row(m, i, width), id=str(i)) for i in range(len(m.tracks))])
        else:
            for i in range(len(m.tracks)):
                ol.replace_option_prompt_at_index(i, self._row(m, i, width))
        if self._shown_cur != cur:
            ol.highlighted = cur
        self._shown_cur, self._shown_meta, self._shown_state = cur, m.meta_version, m.state

    def _bar(self, frac: float | None, width: int) -> Text:
        c = theme_pair(self.app)[0]
        t = Text(no_wrap=True)
        if frac is None:
            t.append("─" * width, style="dim")
            return t
        done = max(0, min(width - 1, int(frac * width)))
        t.append("━" * done, style="bold " + c)
        t.append("●", style="bold " + c)
        t.append("─" * (width - done - 1), style="dim")
        return t

    def _refresh(self) -> None:
        m = self.session
        if m is None:
            return
        cur = m.current
        disc = self.query_one(Disc)
        disc.set_cover(self._cover(m))
        disc.spinning = m.state == "playing"
        if (self._shown_cur != cur.index or self._shown_meta != m.meta_version or self._shown_state != m.state):
            self._fill_tracks(m)

        self.query_one("#np-title", Label).update(cur.title)
        who = " · ".join(x for x in (cur.artist, cur.album or m.item.title) if x)
        self.query_one("#np-sub", Static).update(who)

        pos, dur = m.position(), cur.duration
        if dur:
            pos = min(pos, dur)
        width = max(12, min(40, self.query_one("#np-left").size.width - 18))
        line = Text(justify="center", no_wrap=True)
        line.append(f"{mmss(pos):>6} ", style="dim")
        line.append_text(self._bar(pos / dur if dur else None, width))
        line.append(f" {mmss(dur):>6}", style="dim")
        self.query_one("#np-time", Static).update(line)

        icon = {"playing": "▶ playing", "paused": "❚❚ paused", "ended": "■ finished", "stopped": "■ stopped"}[m.state]
        bits = [icon, f"vol {m.volume}%", f"repeat: {LOOP_LABEL[m.loop]}", f"shuffle: {'on' if m.shuffle else 'off'}"]
        if not m.controlled:
            bits = [icon, "VLC window mode - use VLC's own controls"]
        self.query_one("#np-status", Static).update(Text("   ".join(bits), justify="center"))

    # ----- actions -----
    def _need(self) -> MusicSession | None:
        m = self.session
        if m is not None and not m.controlled:
            self.app.notify("Music is in VLC window mode. Use VLC's controls, or turn on "
                            "'Control music from NyRun' in Settings.", severity="warning", markup=False)
            return None
        return m

    def action_toggle_pause(self) -> None:
        m = self._need()
        if m:
            m.toggle_pause()
            self._refresh()

    def action_next(self) -> None:
        m = self._need()
        if m and not m.next():
            self.app.notify("That was the last track. (Press l for repeat.)", markup=False, timeout=3)
        self._refresh()

    def action_prev(self) -> None:
        m = self._need()
        if m:
            m.prev()
            self._refresh()

    def action_seek(self, seconds: int) -> None:
        m = self._need()
        if m:
            m.seek(seconds)
            self._refresh()

    def action_volume(self, delta: int) -> None:
        m = self.session
        if not m:
            return
        s = self.app.settings
        s.music_volume = m.set_volume(m.volume + delta)
        s.save()
        self._refresh()

    def action_cycle_loop(self) -> None:
        m = self.session
        if m:
            s = self.app.settings
            s.music_loop = m.cycle_loop()
            s.save()
            self.app.notify(f"Repeat: {LOOP_LABEL[m.loop]}", markup=False, timeout=2)
            self._refresh()

    def action_toggle_shuffle(self) -> None:
        m = self._need()
        if m:
            s = self.app.settings
            s.music_shuffle = m.toggle_shuffle()
            s.save()
            self.app.notify(f"Shuffle: {'on' if m.shuffle else 'off'}", markup=False, timeout=2)
            self._shown_cur = None
            self._refresh()

    def action_stop(self) -> None:
        self.app.shutdown_music()
        self.app.pop_screen()

    def action_open_folder(self) -> None:
        m = self.session
        if m:
            self.app.open_in_file_manager(m.item.path)

    def action_back(self) -> None:
        self.app.pop_screen()  # the music keeps playing

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        m = self._need()
        if m:
            m.jump(int(event.option.id or 0))
            self._shown_cur = None
            self._refresh()
