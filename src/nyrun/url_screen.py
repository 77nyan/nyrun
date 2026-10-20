"""Watch from a URL: paste a link, NyRun finds the video and opens it in VLC (and can save a copy)."""

from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import urlsplit

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .library import human_duration, human_size
from .urlengine import (
    Cancelled, Engine, Options, Progress, Resolved, UrlError, download, downloads_root, normalize_url,
)
from .widgets import bar

INFO = (
    "Paste a link to a video, a stream (.m3u8 / .mpd / rtsp...) or a page that has a video, then press Enter. "
    "VLC opens it. NyRun can't open every website on earth: sites that need a login or use copy protection "
    "(DRM) can't work."
)
SOURCES = {
    "direct": "the link itself",
    "probe": "what the server says it is",
    "page": "a scan of the page",
    "yt-dlp": "yt-dlp",
    "vlc": "VLC's own scripts",
}


def _short(url: str, n: int = 78) -> str:
    return url if len(url) <= n else url[: n - 1] + "…"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url).removeprefix("www.")


def fmt_speed(bps: float) -> str:
    return f"{human_size(int(bps))}/s" if bps >= 1 else ""


class UrlScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+d", "download", "Download", priority=True),
        Binding("ctrl+o", "open_downloads", "Downloads folder", priority=True),
        Binding("ctrl+x", "cancel_download", "Stop download", priority=True),
    ]

    def __init__(self, prefill: str = "", autoplay: bool = False) -> None:
        super().__init__()
        self.prefill = prefill
        self.autoplay = autoplay
        self.res: Resolved | None = None
        self._token = 0  # ignores answers for a URL that was replaced by a newer one
        self._cancel = threading.Event()
        self._downloading = False
        self._typed = ""  # the text that produced self.res

    # ----- layout -----
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="url-body"):
            yield Label("Watch from a URL", id="url-title")
            yield Static(INFO, id="url-info")
            yield Input(placeholder="https://…   (Enter plays it in VLC)", id="url-input")
            yield Static(id="url-status")
            yield Static(id="url-card")
            yield Static(id="url-progress")
            yield Label("Recent", id="url-recent-h")
            yield OptionList(id="url-recent")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Web video"
        box = self.query_one("#url-input", Input)
        box.value = self.prefill
        box.focus()
        self._fill_recent()
        self._status("")
        if self.prefill:
            box.cursor_position = len(self.prefill)
            if self.autoplay:
                self._begin(self.prefill, "play")

    def _fill_recent(self) -> None:
        ol = self.query_one("#url-recent", OptionList)
        ol.clear_options()
        hist = self.app.state.url_history
        ol.add_options([
            Option(Text.assemble((h.get("title") or h["url"])[:60], ("  ·  " + _host(h["url"]), "dim")), id=h["url"])
            for h in hist
        ])
        self.query_one("#url-recent-h").display = bool(hist)
        ol.display = bool(hist)

    # ----- status helpers -----
    def _status(self, text: str, style: str = "") -> None:
        self.query_one("#url-status", Static).update(Text(text, style=style))

    def _card(self, res: Resolved | None) -> None:
        card = self.query_one("#url-card", Static)
        if res is None:
            card.update("")
            return
        s = self.app.settings
        t = Text()
        t.append("✓ ", style="bold green")
        t.append(res.title or "(no title)", style="bold")
        t.append("\n")
        bits = [res.kind.value, f"found via {SOURCES.get(res.source, res.source)}"]
        if res.duration:
            bits.append(human_duration(res.duration))
        if res.is_live:
            bits.append("live")
        t.append("  " + " · ".join(bits) + "\n", style="dim")
        if res.note:
            t.append("  " + res.note + "\n", style="dim")
        t.append("  " + _short(res.media_url) + "\n", style="dim")
        if res.audio_url:
            t.append("  + separate audio track\n", style="dim")
        if s.url_downloads:
            t.append("\n  Ctrl+D  save a copy to the URL Downloads folder", style="bold")
        else:
            t.append("\n  Downloading is off. Turn it on in Settings (,) > Web video.", style="dim")
        card.update(t)

    # ----- playing -----
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._begin(event.value, "play")

    def _begin(self, text: str, action: str) -> None:
        try:
            normalize_url(text)
        except UrlError as e:
            self.res = None
            self._card(None)
            self._status(f"✗ {e}", "bold red")
            return
        self._token += 1
        self._status("⟳ Looking for the video…", "bold yellow")
        self._card(None)
        self._resolve(self._token, text, action)

    @work(thread=True, group="url-resolve", exclusive=True)
    def _resolve(self, token: int, text: str, action: str) -> None:
        try:
            res = Engine(Options.from_settings(self.app.settings)).resolve(text)
            err = None
        except UrlError as e:
            res, err = None, e
        except Exception as e:  # noqa: BLE001  (a bug in a strategy must not take the app down)
            res, err = None, UrlError(f"Something went wrong looking at that link: {e}")
        self.app.call_from_thread(self._resolved, token, text, res, err, action)

    def _resolved(self, token: int, text: str, res: Resolved | None, err: UrlError | None, action: str) -> None:
        if token != self._token or not self.is_attached:
            return
        if err is not None or res is None:
            self.res = None
            self._card(None)
            self._status(f"✗ {err.message if err else 'Nothing found.'}", "bold red")
            if err and err.hint:
                self.query_one("#url-card", Static).update(Text("  " + err.hint, style="dim"))
            return
        self.res, self._typed = res, text
        self._card(res)
        if action == "download":
            self._start_download(res)
            return
        if self.app.play_url(res):
            self._status("▶ Opened in VLC", "bold green")
            self._fill_recent()
        else:
            self._status("✗ VLC could not be started (see the message).", "bold red")

    # ----- downloading -----
    def action_download(self) -> None:
        s = self.app.settings
        if not s.url_downloads:
            self.app.notify(
                "Downloading from URLs is off.\nTurn it on in Settings (,) > Web video.",
                severity="warning", timeout=8, markup=False,
            )
            return
        if self._downloading:
            self.app.notify("A download is already running. Ctrl+X stops it.", severity="warning", markup=False)
            return
        text = self.query_one("#url-input", Input).value
        if self.res is not None and text.strip() == self._typed.strip():
            self._start_download(self.res)
        else:
            self._begin(text, "download")

    def _start_download(self, res: Resolved) -> None:
        root = downloads_root(self.app.settings, self.app.root)
        self._downloading = True
        self._cancel = threading.Event()
        self._status("⇣ Downloading…  (Ctrl+X stops it)", "bold yellow")
        self._progress_text(Progress("connecting", message="connecting"))
        self._download(res, root, Options.from_settings(self.app.settings))

    @work(thread=True, group="url-download", exclusive=False)
    def _download(self, res: Resolved, root: Path, opts: Options) -> None:
        app = self.app
        last = [0.0]

        def on_progress(p: Progress) -> None:
            import time

            now = time.time()
            if p.stage != "done" and now - last[0] < 0.25:
                return
            last[0] = now
            try:
                app.call_from_thread(self._progress_text, p)
            except Exception:  # noqa: BLE001  (app already closed)
                pass

        try:
            path = download(res, root, opts, on_progress=on_progress, cancel=self._cancel)
            app.call_from_thread(self._download_done, path, None)
        except Cancelled:
            app.call_from_thread(self._download_done, None, UrlError("Download stopped."))
        except UrlError as e:
            app.call_from_thread(self._download_done, None, e)
        except Exception as e:  # noqa: BLE001
            app.call_from_thread(self._download_done, None, UrlError(f"The download failed: {e}"))

    def _progress_text(self, p: Progress) -> None:
        if not self.is_attached:
            return
        line = Text()
        if p.stage == "connecting":
            line.append("  connecting…", style="dim")
        elif p.fraction is not None:
            line.append("  " + bar(p.fraction, 30) + f" {round(p.fraction * 100)}%", style="bold")
        else:
            line.append("  downloading…", style="bold")
        extra = []
        if p.total:
            extra.append(f"{human_size(p.done)} of {human_size(p.total)}")
        elif p.done:
            extra.append(human_size(p.done))
        if p.speed:
            extra.append(fmt_speed(p.speed))
        if p.eta:
            extra.append(f"{human_duration(p.eta)} left")
        if p.message and p.stage != "connecting":
            extra.append(p.message)
        if extra:
            line.append("   " + " · ".join(extra), style="dim")
        self.query_one("#url-progress", Static).update(line)

    def _download_done(self, path: Path | None, err: UrlError | None) -> None:
        self._downloading = False
        app = self.app
        if path is not None:
            shown = path.parent
            try:
                shown = "~/" + path.parent.relative_to(Path.home()).as_posix()
            except ValueError:
                pass
            app.notify(f"✓ Saved to {shown}", markup=False, timeout=8)
        else:
            app.notify(str(err), severity="warning" if err and err.message == "Download stopped." else "error", timeout=14, markup=False)
        if self.is_attached:
            self.query_one("#url-progress", Static).update("")
            if path is not None:
                self._status(f"✓ Saved: {path.name}   (Ctrl+O opens the folder)", "bold green")
            else:
                self._status(f"✗ {err.message if err else 'Download failed.'}", "bold red")

    def action_cancel_download(self) -> None:
        if self._downloading:
            self._cancel.set()

    def action_open_downloads(self) -> None:
        root = downloads_root(self.app.settings, self.app.root)
        if root.is_dir():
            self.app.open_in_file_manager(root)
        else:
            self.app.notify("Nothing downloaded yet. The folder is created with your first download.", severity="warning", markup=False)

    # ----- recent list -----
    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        url = str(event.option.id)
        box = self.query_one("#url-input", Input)
        box.value = url
        self._begin(url, "play")

    def on_key(self, event) -> None:
        focused = self.focused
        if event.key == "down" and isinstance(focused, Input) and self.query_one("#url-recent").display:
            ol = self.query_one("#url-recent", OptionList)
            ol.focus()
            if ol.highlighted is None:
                ol.highlighted = 0
            event.stop()
        elif event.key == "up" and isinstance(focused, OptionList) and (focused.highlighted or 0) == 0:
            self.query_one("#url-input", Input).focus()
            event.stop()
        elif event.key == "delete" and isinstance(focused, OptionList) and focused.highlighted is not None:
            opt = focused.get_option_at_index(focused.highlighted)
            self.app.state.forget_url(str(opt.id))
            self._fill_recent()
            event.stop()

    def action_back(self) -> None:
        if self._downloading:
            self.app.notify("The download keeps going in the background. Ctrl+X in this screen stops it.", markup=False)
        self.app.pop_screen()
