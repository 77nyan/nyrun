"""Theme picker and Settings (VLC compatibility) screens."""

from __future__ import annotations

import asyncio
import shlex
import shutil
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from . import player, themes
from .config import Settings

_ANSI_SWATCH = ["red", "green", "yellow", "blue", "magenta", "cyan"]


# --------------------------------------------------------------------------- #
# Theme picker
# --------------------------------------------------------------------------- #

def _swatch_text(entry: themes.Entry) -> Text:
    t = Text(no_wrap=True)
    if entry.palette is None:
        for c in _ANSI_SWATCH:
            t.append("██", style=c)
        t.append("████")  # pad to the same width as the 8-colour swatches
    else:
        for col in entry.palette.swatch:
            t.append("██", style=col)
    t.append("  ")
    t.append(f"{entry.label:<26}", style="bold")
    t.append(entry.note, style="dim")
    return t


class ThemePickerScreen(Screen):
    """Pick a theme with a live preview. Works inside NyRun and standalone (`nyrun -theme`)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("q", "cancel", "Cancel", show=False),
        Binding("enter", "choose", "Use this theme"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._original = "auto"
        self._ready = False
        self._entries: dict[str, themes.Entry] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="tp-body"):
            with Vertical(id="tp-left"):
                yield Label("Move with ↑ ↓ - the whole app previews each theme live. Enter keeps it, Esc cancels.", id="tp-title")
                yield OptionList(id="tp-list")
            with Vertical(id="tp-right"):
                yield Label("Preview", classes="tp-h")
                yield Static("Inception\n2010 · ♥", id="tp-card", classes="card -selected")
                yield Static("MOVIES\n11 titles · 30 GB\n████████░░ 62%", id="tp-tile", classes="tile")
                yield Static(id="tp-note")
                yield Static(
                    "Your own theme: drop a file such as  mytheme.toml  into\n"
                    f"{themes.user_theme_dir()}\n"
                    "using Omarchy's colors.toml format (background, foreground, accent, color0-15).",
                    id="tp-help",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Themes"
        self.app.theme_locked = True
        self._original = self.app.settings.theme
        entries = themes.catalog()
        self._entries = {e.choice: e for e in entries}
        lst = self.query_one("#tp-list", OptionList)
        lst.add_options([Option(_swatch_text(e), id=e.choice) for e in entries])
        names = [e.choice for e in entries]
        idx = names.index(self._original) if self._original in names else 0
        lst.focus()
        lst.highlighted = idx
        self._ready = True
        self._update_note(names[idx])

    def _update_note(self, choice: str) -> None:
        e = self._entries.get(choice)
        if not e:
            return
        note = {
            "auto": "Uses your Omarchy theme when it finds one, and keeps following it.",
            "omarchy": "Recolours NyRun within a couple of seconds whenever you switch Omarchy themes.",
            "terminal": "Uses whatever palette your terminal is set to. Good if your terminal is already themed.",
        }.get(choice, f"{e.label} ({e.note})")
        self.query_one("#tp-note", Static).update(note)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if not self._ready or event.option.id is None:
            return
        self.app.apply_theme_choice(str(event.option.id))
        self._update_note(str(event.option.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._commit(str(event.option.id))

    def action_choose(self) -> None:
        lst = self.query_one("#tp-list", OptionList)
        if lst.highlighted is not None:
            self._commit(str(lst.get_option_at_index(lst.highlighted).id))

    def _commit(self, choice: str) -> None:
        app = self.app
        app.settings.theme = choice
        app.settings.save()
        app.theme_locked = False
        app.apply_theme_choice(choice)
        app.notify(f"Theme saved: {choice}", markup=False, timeout=2)
        self._leave()

    def action_cancel(self) -> None:
        self.app.theme_locked = False
        self.app.apply_theme_choice(self._original)
        self._leave()

    def _leave(self) -> None:
        if getattr(self.app, "standalone", False):
            self.app.exit()
        else:
            self.app.pop_screen()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

LAUNCHERS = [("Auto-detect", "auto"), ("Installed VLC (native)", "native"), ("Flatpak (org.videolan.VLC)", "flatpak")]
HWDEC = [
    ("Auto - let VLC decide", "auto"),
    ("Off - software decoding (fixes green/black video)", "none"),
    ("VA-API (Intel / AMD, Linux)", "vaapi"),
    ("VDPAU (Nvidia, Linux)", "vdpau"),
    ("D3D11 (Windows)", "d3d11va"),
    ("DXVA2 (Windows)", "dxva2"),
    ("VideoToolbox (macOS)", "videotoolbox"),
]
QT_PLATFORM = [
    ("Auto", "auto"),
    ("X11 / XWayland (fixes many Wayland crashes & black windows)", "xcb"),
    ("Wayland (native)", "wayland"),
]
ONE_INSTANCE = [("VLC default", "default"), ("Reuse one VLC window", "on"), ("Always open a new window", "off")]
CONTINUE = [("VLC default", "default"), ("Never resume", "never"), ("Ask me", "ask"), ("Always resume where I stopped", "always")]

LOGO_LINE = [("Rainbow (RGB)", "rainbow"), ("Match my theme (follows Omarchy too)", "theme")]
MUSIC_LOOP = [("Off", "off"), ("Repeat the album", "all"), ("Repeat one track", "track")]

PRESETS: dict[str, tuple[str, dict]] = {
    "default": ("Default - nothing special", {}),
    "wayland": ("Wayland / Hyprland / Omarchy - run VLC through XWayland", {"vlc_qt_platform": "xcb"}),
    "safe": ("Safe mode - software decoding (for glitchy video)", {"vlc_hwdec": "none"}),
    "quiet": ("Quiet - no file-name popup, no first-run prompts", {"vlc_title_osd": False, "vlc_quiet": True}),
}
# what a preset resets before applying itself
PRESET_FIELDS = ["vlc_hwdec", "vlc_vout", "vlc_aout", "vlc_qt_platform", "vlc_title_osd", "vlc_quiet", "vlc_one_instance"]

SWITCHES = [
    ("fullscreen", "Start in fullscreen", ""),
    ("vlc_play_and_exit", "Close VLC when playback ends", "Off = VLC stays open after the last file"),
    ("auto_watched", "Mark as watched when I press play", "NyRun can't see how far you got, so this marks on start"),
    ("vlc_title_osd", "Show the file name on screen at start", ""),
    ("vlc_quiet", "Quiet start", "No first-run privacy prompt, don't keep VLC's recent-files list"),
    ("vlc_loop", "Loop the queue", ""),
    ("vlc_start_paused", "Start paused", ""),
    ("vlc_on_top", "Keep video window on top", ""),
    ("vlc_sub_autodetect", "Auto-load subtitle files next to the video", ""),
]


class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "close", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._syncing = False

    # ----- layout -----
    def _row(self, label: str, control, hint: str = "") -> ComposeResult:
        with Horizontal(classes="st-row"):
            yield Label(label, classes="st-label")
            yield control
        if hint:
            yield Static(hint, classes="st-hint")

    def compose(self) -> ComposeResult:
        s: Settings = self.app.settings
        yield Header()
        with VerticalScroll(id="st-body"):
            yield Label("Appearance", classes="st-h")
            yield from self._row("Theme", Button("Pick a theme…", id="st-theme"))
            yield Static(id="st-theme-now", classes="st-hint")
            yield from self._row(
                "Line under the NyRun logo",
                Select(LOGO_LINE, value=s.logo_line if s.logo_line in ("rainbow", "theme") else "rainbow",
                       allow_blank=False, id="f-logo_line"),
                "Rainbow keeps the RGB look; 'Match my theme' blends your current theme's colours "
                "(your Omarchy theme when you use it).",
            )

            yield Label("VLC compatibility", classes="st-h")
            yield from self._row(
                "Quick preset",
                Select([(v[0], k) for k, v in PRESETS.items()], prompt="Apply a preset…", allow_blank=True, id="st-preset"),
                "Presets only change the compatibility options below. Everything stays editable.",
            )
            yield from self._row("VLC program", Input(s.vlc_path, placeholder="auto-detect (or a path such as /usr/bin/vlc)", id="f-vlc_path"))
            yield from self._row("Launch VLC via", Select(LAUNCHERS, value=s.vlc_launcher, allow_blank=False, id="f-vlc_launcher"),
                                 "Flatpak VLC can only open folders it has been given access to - see the Test button.")
            yield from self._row("Hardware decoding", Select(HWDEC, value=s.vlc_hwdec, allow_blank=False, id="f-vlc_hwdec"))
            yield from self._row("Window system", Select(QT_PLATFORM, value=s.vlc_qt_platform, allow_blank=False, id="f-vlc_qt_platform"),
                                 "Sets QT_QPA_PLATFORM for VLC only. Try X11 if VLC crashes or shows a black window.")
            yield from self._row("Video output", Input(s.vlc_vout, placeholder="VLC default  (e.g. gl, xcb_x11)", id="f-vlc_vout"))
            yield from self._row("Audio output", Input(s.vlc_aout, placeholder="VLC default  (e.g. pulse, alsa)", id="f-vlc_aout"))
            yield from self._row("New files open in", Select(ONE_INSTANCE, value=s.vlc_one_instance, allow_blank=False, id="f-vlc_one_instance"))
            yield from self._row("Resume inside a file", Select(CONTINUE, value=s.vlc_continue, allow_blank=False, id="f-vlc_continue"),
                                 "VLC remembers where you stopped; this is VLC's own 'Continue playback?' option.")

            yield Label("Playback", classes="st-h")
            for attr, label, hint in SWITCHES:
                yield from self._row(label, Switch(getattr(s, attr), id=f"f-{attr}"), hint)
            yield from self._row("Preferred subtitle language", Input(s.vlc_sub_lang, placeholder="e.g. en  or  pl,en", id="f-vlc_sub_lang"))
            yield from self._row("Preferred audio language", Input(s.vlc_audio_lang, placeholder="e.g. ja  or  en", id="f-vlc_audio_lang"))
            yield from self._row("Playback speed", Input(s.vlc_rate, placeholder="1.0  (0.1 - 8)", id="f-vlc_rate"))
            yield from self._row("Extra VLC options", Input(shlex.join(s.extra_vlc_args), placeholder="anything else, e.g. --deinterlace=1 --gain=2", id="f-extra_vlc_args"),
                                 "Passed to VLC exactly as typed.")

            yield Label("Music", classes="st-h")
            yield from self._row(
                "Control music from NyRun", Switch(s.music_controls, id="f-music_controls"),
                "On: music plays in a hidden VLC that NyRun steers (pause, seek, volume, the spinning disc). "
                "Off: a normal VLC window opens instead.",
            )
            yield from self._row("Repeat", Select(MUSIC_LOOP, value=s.music_loop if s.music_loop in ("off", "all", "track") else "off",
                                                  allow_blank=False, id="f-music_loop"))
            yield from self._row("Shuffle", Switch(s.music_shuffle, id="f-music_shuffle"))

            yield Label("What NyRun will run", classes="st-h")
            yield Static(id="st-preview")
            yield Static(id="st-warn")
            with Horizontal(id="st-buttons"):
                yield Button("Test VLC", id="st-test", variant="primary")
                yield Button("Reset VLC options", id="st-reset")
                yield Button("Done", id="st-done")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Settings"
        self._refresh_info()

    def on_screen_resume(self) -> None:
        self._refresh_info()

    def refresh_all(self) -> None:  # called by the app after a theme change
        self._refresh_info()

    # ----- info panes -----
    def _warnings(self) -> list[str]:
        s: Settings = self.app.settings
        out = []
        if s.vlc_path.strip() and not (Path(s.vlc_path).expanduser().exists() or shutil.which(s.vlc_path)):
            out.append("VLC program: that path does not exist.")
        if s.vlc_rate.strip() and player._rate_ok(s.vlc_rate) is None:
            out.append("Playback speed: enter a number between 0.1 and 8 (ignored for now).")
        try:
            shlex.split(shlex.join(s.extra_vlc_args))
        except ValueError:
            out.append("Extra options: unbalanced quotes.")
        return out

    def _refresh_info(self) -> None:
        s: Settings = self.app.settings
        res = themes.resolve(s.theme)
        shown = "terminal colours" if res.palette is None else res.label
        self.query_one("#st-theme-now", Static).update(f"current: {s.theme}  ->  {shown}")
        self.query_one("#st-preview", Static).update(player.preview_command(s))
        warn = self._warnings()
        self.query_one("#st-warn", Static).update("\n".join(f"! {w}" for w in warn))

    # ----- applying changes -----
    def _set(self, attr: str, value) -> None:
        s: Settings = self.app.settings
        if getattr(s, attr, None) == value:
            return
        setattr(s, attr, value)
        s.save()
        self._refresh_info()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if self._syncing or not (event.switch.id or "").startswith("f-"):
            return
        self._set((event.switch.id or "")[2:], bool(event.value))

    def on_select_changed(self, event: Select.Changed) -> None:
        sid = event.select.id or ""
        if self._syncing:
            return
        if sid == "st-preset":
            if event.value is not Select.NULL:
                self._apply_preset(str(event.value))
            return
        if sid.startswith("f-") and event.value is not Select.NULL:
            self._set(sid[2:], str(event.value))

    def on_input_changed(self, event: Input.Changed) -> None:
        iid = event.input.id or ""
        if self._syncing or not iid.startswith("f-"):
            return
        attr = iid[2:]
        if attr == "extra_vlc_args":
            try:
                self._set(attr, shlex.split(event.value))
            except ValueError:
                pass  # half-typed quote: keep the previous value, warning shows below
            return
        self._set(attr, event.value.strip())

    def _apply_preset(self, key: str) -> None:
        s: Settings = self.app.settings
        defaults = Settings()
        for f in PRESET_FIELDS:
            setattr(s, f, getattr(defaults, f))
        for f, v in PRESETS[key][1].items():
            setattr(s, f, v)
        s.save()
        self._sync_widgets()
        self._refresh_info()
        self.app.notify(f"Preset applied: {PRESETS[key][0].split(' - ')[0]}", markup=False, timeout=2)

    def _sync_widgets(self) -> None:
        s: Settings = self.app.settings
        self._syncing = True
        try:
            for w in self.query("Switch, Select, Input"):
                wid = w.id or ""
                if not wid.startswith("f-"):
                    continue
                attr = wid[2:]
                val = shlex.join(s.extra_vlc_args) if attr == "extra_vlc_args" else getattr(s, attr)
                w.value = val
            preset = self.query_one("#st-preset", Select)
            preset.value = Select.NULL
        finally:
            # events from the programmatic changes are delivered asynchronously
            self.call_after_refresh(self._end_sync)

    def _end_sync(self) -> None:
        self._syncing = False

    # ----- buttons -----
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "st-theme":
            self.app.action_pick_theme()
        elif bid == "st-test":
            self._test_vlc()
        elif bid == "st-reset":
            self._apply_preset("default")
            for f in ("vlc_path", "vlc_launcher", "fullscreen", "vlc_play_and_exit", "vlc_loop", "vlc_start_paused",
                      "vlc_on_top", "vlc_sub_autodetect", "vlc_sub_lang", "vlc_audio_lang", "vlc_continue", "vlc_rate"):
                setattr(self.app.settings, f, getattr(Settings(), f))
            self.app.settings.extra_vlc_args = []
            self.app.settings.save()
            self._sync_widgets()
            self._refresh_info()
        elif bid == "st-done":
            self.action_close()

    async def _test_vlc_async(self) -> None:
        s: Settings = self.app.settings
        try:
            line = await asyncio.to_thread(player.version, s)
        except player.VLCNotFound as e:
            self.app.notify(str(e), severity="error", timeout=10, markup=False)
            return
        msg = f"OK: {line}"
        try:
            cmd = player.find_vlc(s.vlc_path, s.vlc_launcher)
        except player.VLCNotFound:
            cmd = []
        if player.is_flatpak(cmd):
            folder = getattr(self.app, "root", None) or s.default_folder or "<your NyRun folder>"
            msg += f"\nFlatpak: let it read your library with\nflatpak override --user --filesystem={folder} org.videolan.VLC"
        self.app.notify(msg, timeout=12, markup=False)

    def _test_vlc(self) -> None:
        self.run_worker(self._test_vlc_async(), exclusive=True, group="vlc-test")

    def action_close(self) -> None:
        if getattr(self.app, "standalone", False):
            self.app.exit()
        else:
            self.app.pop_screen()
