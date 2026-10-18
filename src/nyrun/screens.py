"""All screens: Home, Library (poster grid), Episodes, Import, Help.  (Now Playing lives in music_screen.py.)"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RadioButton,
    RadioSet,
    Static,
)
from textual.widgets.option_list import Option

from .config import CATEGORIES, IMAGE_EXTS
from .music import mmss
from .library import (
    SORT_MODES,
    ImportError_,
    Item,
    guess_title,
    human_duration,
    human_size,
    import_into_library,
    item_duration,
    sniff_kind,
    sort_items,
)
from .widgets import (
    PosterCard,
    Rainbow,
    Tile,
    badge_for,
    bar,
    make_poster,
    set_poster,
    lerp_hex as _lerp_hex,
    theme_pair,
    tile_text,
)

VIEWS = ["all", "unwatched", "watched", "favorites"]

LOGO = [
    "███╗   ██╗██╗   ██╗██████╗ ██╗   ██╗███╗   ██╗",
    "████╗  ██║╚██╗ ██╔╝██╔══██╗██║   ██║████╗  ██║",
    "██╔██╗ ██║ ╚████╔╝ ██████╔╝██║   ██║██╔██╗ ██║",
    "██║╚██╗██║  ╚██╔╝  ██╔══██╗██║   ██║██║╚██╗██║",
    "██║ ╚████║   ██║   ██║  ██║╚██████╔╝██║ ╚████║",
    "╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝",
]


def _tilde(path: Path) -> str:
    """/home/me/NyRun -> ~/NyRun, so the tagline stays short."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def ago(ts: float | None) -> str:
    if not ts:
        return "never"
    d = time.time() - ts
    for unit, secs in (("day", 86400), ("hour", 3600), ("min", 60)):
        if d >= secs:
            n = int(d // secs)
            return f"{n} {unit}{'s' if n != 1 and unit != 'min' else ''} ago"
    return "just now"


# --------------------------------------------------------------------------- #
# Home
# --------------------------------------------------------------------------- #

class HomeScreen(Screen):
    BINDINGS = [
        Binding("left,up", "app.focus_previous", "Move", show=False),
        Binding("right,down", "app.focus_next", "Move", show=False),
        Binding("1", "category(0)", "Movies"),
        Binding("2", "category(1)", "Series"),
        Binding("3", "category(2)", "Shows"),
        Binding("4", "category(3)", "Music"),
        Binding("c", "continue_watching", "Continue"),
        Binding("n", "now_playing", "Now playing"),
        Binding("i", "import_files", "Import"),
        Binding("a", "add_file", "Add a file"),
        Binding("s", "app.settings", "Settings"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="home-wrap"):
            yield Static(id="logo")
            yield Rainbow(len(LOGO[0]))
            yield Static(id="tagline")
            with Horizontal(classes="tile-row"):
                for cat in CATEGORIES:
                    yield Tile(f"tile-{cat}")
            with Horizontal(classes="tile-row"):
                yield Tile("tile-continue", classes="-action")
                yield Tile("tile-nowplaying", classes="-action")
                yield Tile("tile-add", classes="-action")
                yield Tile("tile-import", classes="-action")
                yield Tile("tile-settings", classes="-action")
            yield Static(id="empty-hint")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#tile-Movies").focus()
        self.refresh_all()
        self.set_interval(1.0, self._draw_nowplaying)

    def on_screen_resume(self) -> None:
        self.query_one(Rainbow).resume()
        self.refresh_all()

    def on_screen_suspend(self) -> None:
        self.query_one(Rainbow).pause()  # no animation work while another screen is on top

    def refresh_all(self) -> None:
        self._draw_logo()
        self._draw_tiles()

    def _draw_logo(self) -> None:
        c1, c2 = theme_pair(self.app)
        t = Text(no_wrap=True)
        width = len(LOGO[0])
        for row in LOGO:
            for x, ch in enumerate(row):
                t.append(ch, style=f"bold {_lerp_hex(c1, c2, x / width)}")
            t.append("\n")
        t.rstrip()
        self.query_one("#logo", Static).update(t)

    def _draw_tiles(self) -> None:
        app = self.app
        total_items = total_size = done = eps = 0
        for cat in CATEGORIES:
            items = app.library.get(cat, [])
            d = sum(app.progress(i)[0] for i in items)
            n = sum(app.progress(i)[1] for i in items)
            if cat == "Music":
                d = n = 0  # music has no "watched" percentage in the overall tagline
            size = sum(i.total_size for i in items)
            total_items += len(items)
            total_size += size
            done += d
            eps += n
            frac = d / n if n else 0
            noun = "title" if len(items) == 1 else "titles"
            if cat == "Music":
                tracks = sum(len(i.episodes) for i in items)
                noun = "album" if len(items) == 1 else "albums"
                lines = [f"{len(items)} {noun} · {human_size(size)}", f"{tracks} track{'s' if tracks != 1 else ''}"]
            else:
                lines = [f"{len(items)} {noun} · {human_size(size)}", f"{bar(frac)} {round(frac*100)}%"]
            self.query_one(f"#tile-{cat}", Tile).update(
                tile_text(
                    " ".join(cat.upper()),
                    lines,
                    glyph={"Movies": "▶", "Series": "≡", "Shows": "◈", "Music": "♪"}[cat],
                    dim=not items,
                )
            )
        cont = app.continue_target()
        tile = self.query_one("#tile-continue", Tile)
        tile.display = cont is not None  # only offered when there is something to resume
        if cont:
            item, idx = cont
            label = item.episodes[idx].label if item.is_collection else "pick up the film"
            tile.update(tile_text("Continue", [item.title[:18], label[:18]]))
        self._draw_nowplaying()
        self.query_one("#tile-add", Tile).update(tile_text("Add a file", ["pick a file, name", "it, add a poster"]))
        self.query_one("#tile-import", Tile).update(
            tile_text("Import", ["move / copy from", "Downloads or any", "directory"])
        )
        self.query_one("#tile-settings", Tile).update(
            tile_text("Settings", ["theme, VLC options", "and compatibility"])
        )
        frac_all = round(100 * done / eps) if eps else 0
        self.query_one("#tagline", Static).update(
            f"{total_items} titles · {human_size(total_size)} · {frac_all}% watched · {_tilde(app.root)}"
        )
        hint = self.query_one("#empty-hint", Static)
        if total_items == 0:
            hint.update(
                "Your library is empty.\n"
                f"Press [b]i[/b] to import, or drop a folder per film (video + poster.jpg) or album into\n"
                f"{app.root / 'Movies'}"
            )
        else:
            hint.update("")

    def _draw_nowplaying(self) -> None:
        music = self.app.music
        tile = self.query_one("#tile-nowplaying", Tile)
        tile.display = music is not None  # only after you have started some music
        if music is not None:
            icon = {"playing": "▶", "paused": "❚❚"}.get(music.state, "■")
            tile.update(tile_text("Now playing", [f"{icon} {music.current.title[:15]}", music.item.title[:18]]))

    def on_tile_pressed(self, event: Tile.Pressed) -> None:
        tid = event.tile.id or ""
        if tid.startswith("tile-") and tid[5:] in CATEGORIES:
            self.action_category(CATEGORIES.index(tid[5:]))
        elif tid == "tile-continue":
            self.action_continue_watching()
        elif tid == "tile-nowplaying":
            self.action_now_playing()
        elif tid == "tile-add":
            self.action_add_file()
        elif tid == "tile-import":
            self.action_import_files()
        elif tid == "tile-settings":
            self.app.action_settings()

    def action_category(self, index: int) -> None:
        self.app.push_screen(LibraryScreen(CATEGORIES[index]))

    def action_continue_watching(self) -> None:
        cont = self.app.continue_target()
        if not cont:
            self.app.notify("Nothing to continue yet - play a series episode or a film first.", severity="warning")
            return
        item, idx = cont
        self.app.play(item, idx)
        self._draw_tiles()

    def action_now_playing(self) -> None:
        self.app.show_now_playing()

    def action_add_file(self) -> None:
        from .add_screen import AddScreen

        self.app.push_screen(AddScreen())

    def action_import_files(self) -> None:
        self.app.push_screen(ImportScreen())


# --------------------------------------------------------------------------- #
# Library (poster grid)
# --------------------------------------------------------------------------- #

class LibraryScreen(Screen):
    BINDINGS = [
        Binding("left", "move(-1, 0)", "Move", show=False),
        Binding("right", "move(1, 0)", "Move", show=False),
        Binding("up", "move(0, -1)", "Move", show=False),
        Binding("down", "move(0, 1)", "Move", show=False),
        Binding("pageup", "page(-1)", "Prev page", show=False),
        Binding("pagedown", "page(1)", "Next page", show=False),
        Binding("home", "jump(0)", "First", show=False),
        Binding("end", "jump(-1)", "Last", show=False),
        Binding("enter", "open", "Play/Open"),
        Binding("p", "quick_play", "Quick play"),
        Binding("w", "toggle_watched", "Watched"),
        Binding("f", "toggle_favorite", "Favorite"),
        Binding("slash", "search", "Search"),
        Binding("s", "cycle_sort", "Sort"),
        Binding("v", "cycle_view", "View"),
        Binding("o", "open_folder", "Folder"),
        Binding("i", "import_files", "Import"),
        Binding("escape,backspace", "back", "Back"),
    ]

    def __init__(self, category: str) -> None:
        super().__init__()
        self.category = category
        self.items: list[Item] = []
        self.index = 0
        self.page = 0
        self.query_text = ""
        self.sort = "name"
        self.view = "all"
        self._geom: tuple[int, int, int, int] = (0, 0, 0, 0)  # cols, rows, poster cols, poster rows
        self.cards: list[PosterCard] = []
        self._durations: dict[str, float | None] = {}
        self._detail_dims = (24, 18)

    # ----- composition -----
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="lib-body"):
            with Vertical(id="grid-wrap"):
                search = Input(placeholder="search titles…   (Enter keeps the filter, Esc clears it)", id="search")
                search.can_focus = False  # only focusable while the search box is open
                yield search
                yield Static(id="status-bar")
                yield Vertical(id="grid")
            with Vertical(id="detail"):
                with Vertical(id="detail-poster-box"):
                    yield make_poster(None, 24, 18, id="detail-poster")
                yield Label(id="detail-title", markup=False)
                yield Static(id="detail-info")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.category
        self._reload()
        self.call_after_refresh(self._layout)

    def on_screen_resume(self) -> None:
        self._reload()

    def on_resize(self) -> None:
        self.call_after_refresh(self._layout)

    # ----- data -----
    @property
    def current(self) -> Item | None:
        return self.items[self.index] if self.items else None

    def _reload(self) -> None:
        keep = self.current.key if self.current else None
        app = self.app
        items = list(app.library.get(self.category, []))
        if self.query_text:
            toks = self.query_text.lower().split()
            items = [i for i in items if all(t in f"{i.title} {i.year}".lower() for t in toks)]
        if self.view == "unwatched":
            items = [i for i in items if app.progress(i)[0] < app.progress(i)[1]]
        elif self.view == "watched":
            items = [i for i in items if app.progress(i)[1] and app.progress(i)[0] >= app.progress(i)[1]]
        elif self.view == "favorites":
            items = [i for i in items if i.key in app.state.favorites]
        self.items = sort_items(items, self.sort, app.state.last_played)
        self.index = next((n for n, i in enumerate(self.items) if i.key == keep), 0)
        self._render_page()
        self._update_detail()

    # ----- layout & rendering -----
    def _layout(self) -> None:
        grid = self.query_one("#grid")
        w, h = grid.size.width, grid.size.height
        if w <= 0 or h <= 0:
            return
        if h >= 16:
            pr, rows = 12, max(1, h // 16)
        else:
            pr, rows = max(5, h - 4), 1
        square = self.category == "Music"  # album covers are square; film posters are portrait
        pc = pr * 2 if square else pr * 4 // 3
        cols = max(1, w // (pc + 3))
        # detail poster follows the screen height
        dr = 18 if self.size.height >= 38 else max(6, self.size.height - 20)
        dims = (min(dr * 2, 34), dr) if square else (dr * 4 // 3, dr)
        if dims != self._detail_dims:
            self._detail_dims = dims
            self._update_detail()
        geom = (cols, rows, pc, pr)
        if geom != self._geom:
            self._geom = geom
            self._render_page()

    def _per_page(self) -> int:
        cols, rows, _, _ = self._geom
        return max(1, cols * rows)

    def _render_page(self) -> None:
        cols, rows, pc, pr = self._geom
        grid = self.query_one("#grid")
        grid.remove_children()
        self.cards = []
        if cols == 0:
            return
        per = self._per_page()
        self.page = self.index // per
        start = self.page * per
        chunk = self.items[start : start + per]
        if not chunk:
            msg = (
                f"Nothing here yet.\n\nPress [b]i[/b] to import, or drop a folder per title into\n"
                f"{self.app.root / self.category}"
                if not self.app.library.get(self.category)
                else "No matches. Press [b]Esc[/b] to clear the search, [b]v[/b] to change the view."
            )
            grid.mount(Static(msg, classes="empty-lib"))
        else:
            rows_w = []
            for r in range(0, len(chunk), cols):
                cards = []
                for it in chunk[r : r + cols]:
                    done, total = self.app.progress(it)
                    cards.append(
                        PosterCard(it, pc, pr, badge_for(it, done, total, it.key in self.app.state.favorites, self.app.state.percent(it.key) if not it.is_music else None))
                    )
                self.cards.extend(cards)
                rows_w.append(Horizontal(*cards, classes="card-row"))
            grid.mount(*rows_w)
            for n, card in enumerate(self.cards):
                card.set_class(start + n == self.index, "-selected")
        self._update_status()

    def _update_status(self) -> None:
        per = self._per_page()
        pages = max(1, -(-len(self.items) // per))
        total = len(self.app.library.get(self.category, []))
        bits = [
            f"{len(self.items)}" + (f" of {total}" if len(self.items) != total else "")
            + (" albums" if self.category == "Music" else " titles"),
            f"sort: {self.sort}",
            f"view: {self.view}",
            f"page {self.page + 1}/{pages}",
        ]
        if self.query_text:
            bits.insert(0, f"search: “{self.query_text}”")
        self.query_one("#status-bar", Static).update("  ·  ".join(bits))

    def _update_detail(self) -> None:
        item = self.current
        dc, dr = self._detail_dims
        set_poster(self.query_one("#detail-poster"), item, dc, dr)
        self.query_one("#detail-title", Label).update(item.title if item else "")
        info = self.query_one("#detail-info", Static)
        if not item:
            info.update("")
            return
        app = self.app
        done, total = app.progress(item)
        t = Text()
        meta = ["Album" if item.is_music else (item.category[:-1] if item.category.endswith("s") else item.category)]
        if item.year:
            meta.insert(0, item.year)
        t.append(" · ".join(meta) + "\n", style="dim")
        size = human_size(item.total_size)
        dur = self._durations.get(item.key)
        if item.is_music:
            n = len(item.episodes)
            t.append(f"{n} track{'s' if n != 1 else ''} · {size}" + (f" · {human_duration(dur)}" if dur else "") + "\n")
        else:
            t.append(f"{size}" + (f" · {human_duration(dur)}" if dur else "") + "\n")
        if item.is_collection:
            t.append(f"{done}/{total} episodes watched\n")
            t.append(bar(done / total if total else 0, 20) + "\n", style="bold")
        elif done:
            t.append("✓ listened\n" if item.is_music else "✓ watched\n", style="bold green")
        elif not item.is_music and app.state.percent(item.key):
            pct = app.state.percent(item.key)
            t.append(f"▶ {pct}% watched · resume at {mmss(app.state.positions[item.key][0])}\n", style="bold yellow")
            t.append(bar(pct / 100, 20) + "\n", style="bold")
        if item.key in app.state.favorites:
            t.append("♥ favorite\n", style="bold #ff6b9d")
        plays = app.state.play_count.get(item.key, 0)
        if plays:
            t.append(f"played {plays}× · last {ago(app.state.last_played.get(item.key))}\n", style="dim")
        subs = [] if item.is_music else item.subtitles
        if subs:
            t.append(f"{len(subs)} subtitle file{'s' if len(subs) != 1 else ''}\n", style="dim")
        if not item.poster:
            t.append("no cover - add cover.jpg to the folder\n" if item.is_music else "no poster - add poster.jpg to the folder\n", style="yellow")
        info.update(t)
        if not item.is_collection and item.key not in self._durations:
            self._probe(item)

    @work(thread=True, exclusive=True, group="duration")
    def _probe(self, item: Item) -> None:
        dur = item_duration(item)
        self._durations[item.key] = dur
        if dur and self.current is item:
            self.app.call_from_thread(self._update_detail)

    # ----- selection -----
    def _select(self, index: int) -> None:
        if not self.items:
            return
        index = max(0, min(len(self.items) - 1, index))
        old_page = self.page
        self.index = index
        per = self._per_page()
        if index // per != old_page:
            self._render_page()
        else:
            start = self.page * per
            for n, card in enumerate(self.cards):
                card.set_class(start + n == self.index, "-selected")
        self._update_detail()

    def action_move(self, dx: int, dy: int) -> None:
        cols = self._geom[0] or 1
        new = self.index + dx + dy * cols
        if dy > 0 and new >= len(self.items):
            new = len(self.items) - 1
        self._select(new)

    def action_page(self, direction: int) -> None:
        per = self._per_page()
        self._select(self.index + direction * per)

    def action_jump(self, where: int) -> None:
        self._select(0 if where == 0 else len(self.items) - 1)

    def on_poster_card_clicked(self, event: PosterCard.Clicked) -> None:
        if event.card.item in self.items:
            idx = self.items.index(event.card.item)
            if idx == self.index:
                self.action_open()
            else:
                self._select(idx)

    # ----- actions -----
    def action_open(self) -> None:
        it = self.current
        if not it:
            return
        if it.is_music:
            self.app.play_music(it)
            self._render_page()
            self._update_detail()
        elif it.is_collection:
            self.app.push_screen(EpisodeScreen(it))
        else:
            self.app.play(it)
            self._render_page()
            self._update_detail()

    def action_quick_play(self) -> None:
        it = self.current
        if not it:
            return
        if it.is_music:
            self.app.play_music(it)
        else:
            self.app.play(it, self.app.next_episode_index(it) if it.is_collection else 0)
        self._render_page()
        self._update_detail()

    def action_toggle_watched(self) -> None:
        it = self.current
        if not it:
            return
        app = self.app
        done, total = app.progress(it)
        new_value = done < total
        for ep in it.episodes if it.is_collection else []:
            app.state.watched.discard(ep.key) if not new_value else app.state.watched.add(ep.key)
        if not it.is_collection:
            (app.state.watched.add if new_value else app.state.watched.discard)(it.key)
        for k in [e.key for e in it.episodes] + [it.key]:
            app.state.positions.pop(k, None)  # marked by hand: forget any half-way point
        app.state.save()
        self.app.notify(f"{'Marked watched' if new_value else 'Marked unwatched'}: {it.title}", markup=False)
        self._reload()

    def action_toggle_favorite(self) -> None:
        it = self.current
        if it:
            on = self.app.state.toggle_favorite(it.key)
            self.app.notify(f"{'♥ Added to' if on else 'Removed from'} favorites: {it.title}", markup=False)
            self._reload()

    def action_search(self) -> None:
        box = self.query_one("#search", Input)
        box.can_focus = True
        box.add_class("-open")
        box.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.query_text = event.value.strip()
            self.index = 0
            self._reload()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self.set_focus(None)
            event.input.can_focus = False
            if not self.query_text:
                event.input.remove_class("-open")

    def action_back(self) -> None:
        box = self.query_one("#search", Input)
        if box.has_class("-open") or self.query_text:
            box.value = ""
            box.remove_class("-open")
            self.set_focus(None)
            box.can_focus = False
            self.query_text = ""
            self._reload()
        else:
            self.app.pop_screen()

    def action_cycle_sort(self) -> None:
        self.sort = SORT_MODES[(SORT_MODES.index(self.sort) + 1) % len(SORT_MODES)]
        self._reload()

    def action_cycle_view(self) -> None:
        self.view = VIEWS[(VIEWS.index(self.view) + 1) % len(VIEWS)]
        self._reload()

    def action_open_folder(self) -> None:
        if self.current:
            self.app.open_in_file_manager(self.current.path)

    def action_import_files(self) -> None:
        self.app.push_screen(ImportScreen(self.category))


# --------------------------------------------------------------------------- #
# Episodes
# --------------------------------------------------------------------------- #

class EpisodeScreen(Screen):
    BINDINGS = [
        Binding("p", "play_one", "Play only this"),
        Binding("w", "toggle_watched", "Watched"),
        Binding("n", "next_unwatched", "Next unwatched"),
        Binding("o", "open_folder", "Folder"),
        Binding("escape,backspace", "app.pop_screen", "Back"),
    ]

    def __init__(self, item: Item) -> None:
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="ep-body"):
            with Vertical(id="ep-side"):
                yield make_poster(self.item, 24, 18)
                yield Label(self.item.title, id="ep-title", markup=False)
                yield Static(id="ep-info")
            yield OptionList(id="ep-list")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.item.title
        self._fill(self.app.next_episode_index(self.item))
        self.query_one("#ep-list").focus()

    def _fill(self, highlight: int) -> None:
        app = self.app
        ol = self.query_one("#ep-list", OptionList)
        ol.clear_options()
        opts = []
        for n, ep in enumerate(self.item.episodes):
            seen = ep.key in app.state.watched
            line = Text()
            line.append("✓ " if seen else "  ", style="bold green" if seen else "")
            line.append(f"{n + 1:>3}  ", style="dim")
            line.append(ep.label, style="dim" if seen else "")
            pct = None if seen else app.state.percent(ep.key)
            if pct:
                line.append(f"   ▶ {pct}%", style="bold yellow")
            opts.append(Option(line, id=ep.key))
        ol.add_options(opts)
        ol.highlighted = max(0, min(highlight, len(opts) - 1))
        done, total = app.progress(self.item)
        last = app.state.last_played.get(self.item.key)
        self.query_one("#ep-info", Static).update(
            f"{done}/{total} watched\n{bar(done / total if total else 0, 24)}\n"
            f"{human_size(self.item.total_size)}\nlast played: {ago(last)}\n\n"
            "Enter plays from here and queues the rest in VLC."
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.app.play(self.item, event.option_index)
        self._fill(self.app.next_episode_index(self.item))

    def action_play_one(self) -> None:
        ol = self.query_one("#ep-list", OptionList)
        if ol.highlighted is not None:
            self.app.play(self.item, ol.highlighted, only_one=True)
            self._fill(self.app.next_episode_index(self.item))

    def action_toggle_watched(self) -> None:
        ol = self.query_one("#ep-list", OptionList)
        i = ol.highlighted
        if i is None:
            return
        self.app.state.toggle_watched(self.item.episodes[i].key)  # also forgets its half-way point
        self._fill(i)

    def action_next_unwatched(self) -> None:
        self.query_one("#ep-list", OptionList).highlighted = self.app.next_episode_index(self.item)

    def action_open_folder(self) -> None:
        self.app.open_in_file_manager(self.item.path)


# --------------------------------------------------------------------------- #
# Import (cut / copy from anywhere into the library)
# --------------------------------------------------------------------------- #

class VisibleTree(DirectoryTree):
    """Directory tree without hidden files, with ←/→ to collapse/expand."""

    ICON_FILE = ""  # no little file glyph in front of file names

    BINDINGS = [
        Binding("right", "expand_node", "Open folder", show=False),
        Binding("left", "collapse_node", "Close folder", show=False),
    ]

    def filter_paths(self, paths):
        return [p for p in paths if not p.name.startswith(".")]

    def action_expand_node(self) -> None:
        node = self.cursor_node
        if node is not None and node.allow_expand:
            node.expand()

    def action_collapse_node(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded:
            node.collapse()
        elif node.parent is not None:
            self.move_cursor(node.parent)


class ImportScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("ctrl+g", "import_now", "Import"),
        Binding("u", "up_folder", "Parent folder"),
    ]

    def __init__(self, category: str = "Movies") -> None:
        super().__init__()
        self.default_category = category if category in CATEGORIES else "Movies"
        self.selected: Path | None = None

    def compose(self) -> ComposeResult:
        downloads = Path.home() / "Downloads"
        start = downloads if downloads.is_dir() else Path.home()
        yield Header()
        with Horizontal(id="imp-body"):
            with Vertical(id="imp-left"):
                yield Label("Move / copy from Downloads or any directory   (↑↓ move · → open folder · ← close · u parent folder)")
                yield Input(placeholder="jump to folder, e.g. ~/Downloads  (Enter)", id="imp-goto")
                yield Static(f"in: {start}", id="imp-path")
                tree = VisibleTree(start, id="imp-tree")
                tree.show_root = False
                yield tree
            with Vertical(id="imp-form"):
                yield Static("Nothing selected", id="imp-selected")
                yield Static(
                    "Highlight a video, a music file or a folder - then move (cut) or copy it into your library, "
                    "from Downloads or any directory. A lone file gets its own folder; "
                    "same-named subtitles and covers come along.",
                    id="imp-hint",
                )
                yield Label("Put it in", classes="imp-label")
                yield RadioSet(*[RadioButton(c, value=(c == self.default_category)) for c in CATEGORIES], id="imp-cat")
                yield Label("Title (folder name)", classes="imp-label")
                yield Input(id="imp-title")
                yield Label("Poster / cover image (optional path)", classes="imp-label")
                yield Input(placeholder="auto-detected if it sits next to the file", id="imp-poster")
                yield Label("Mode", classes="imp-label")
                yield RadioSet(RadioButton("Move (cut)", value=True), RadioButton("Copy"), id="imp-mode")
                yield Button("Import  (Ctrl+G)", id="imp-go", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Import"
        self.query_one("#imp-tree").focus()

    def on_tree_node_highlighted(self, event: DirectoryTree.NodeHighlighted) -> None:
        data = event.node.data
        if data is None:
            return
        path = Path(data.path)
        self.selected = path
        self.query_one("#imp-selected", Static).update(f"▶ {path.name or path}")
        self.query_one("#imp-title", Input).value = guess_title(path.stem if path.is_file() else path.name)
        # guess the category from the name
        low = path.name.lower()
        kind = sniff_kind(path)
        if kind == "audio":
            self._set_cat("Music")
        elif kind == "video" and re.search(r"s\d{1,2}e\d{1,2}|season|complete series|\bs\d{2}\b", low):
            self._set_cat("Series")
        elif kind == "video" and self._current_cat() == "Music":
            self._set_cat("Movies")
        # auto-detect a poster
        poster = ""
        try:
            if path.is_file():
                for e in IMAGE_EXTS:
                    cand = path.with_suffix(e)
                    if cand.exists():
                        poster = str(cand)
                        break
            elif path.is_dir():
                imgs = sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
                poster = str(imgs[0]) if imgs else ""
        except OSError:
            pass
        self.query_one("#imp-poster", Input).value = poster

    def _current_cat(self) -> str:
        return CATEGORIES[self.query_one("#imp-cat", RadioSet).pressed_index]

    def _set_cat(self, name: str) -> None:
        rs = self.query_one("#imp-cat", RadioSet)
        for b in rs.query(RadioButton):
            if str(b.label) == name:
                b.value = True

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "imp-goto":
            target = Path(event.value).expanduser()
            if target.is_dir():
                tree = self.query_one("#imp-tree", VisibleTree)
                tree.path = target
                tree.reload()
                tree.focus()
                self.query_one("#imp-path", Static).update(f"in: {target}")
            else:
                self.app.notify("That folder doesn't exist.", severity="warning")
        else:
            self.action_import_now()

    def action_up_folder(self) -> None:
        tree = self.query_one("#imp-tree", VisibleTree)
        if tree.has_focus:
            tree.path = Path(tree.path).parent
            tree.reload()
            self.query_one("#imp-path", Static).update(f"in: {tree.path}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "imp-go":
            self.action_import_now()

    @work(exclusive=True)
    async def action_import_now(self) -> None:
        if not self.selected:
            self.app.notify("Highlight a video, music file or folder first.", severity="warning")
            return
        cat = CATEGORIES[self.query_one("#imp-cat", RadioSet).pressed_index]
        move = self.query_one("#imp-mode", RadioSet).pressed_index == 0
        title = self.query_one("#imp-title", Input).value.strip() or None
        poster = self.query_one("#imp-poster", Input).value.strip() or None
        btn = self.query_one("#imp-go", Button)
        btn.disabled = True
        self.app.notify("Importing... big files can take a while.", markup=False)
        try:
            dest = await asyncio.to_thread(
                import_into_library,
                self.selected,
                self.app.root,
                cat,
                move=move,
                title=title,
                poster=Path(poster) if poster else None,
            )
        except (ImportError_, OSError) as e:
            self.app.notify(f"Import failed: {e}", severity="error", markup=False)
            btn.disabled = False
            return
        self.app.rescan()
        self.app.notify(f"{'Moved' if move else 'Copied'} to {cat}/{dest.name}", markup=False)
        self.app.pop_screen()


# --------------------------------------------------------------------------- #
# Help
# --------------------------------------------------------------------------- #

HELP = """\
[b]Home[/b]
  arrows / Tab   move between tiles          Enter   open
  1 2 3 4        Movies / Series / Shows / Music
  c  continue    n  now playing (music)      i  import    s  settings
  a  add a file: pick it, choose where, name it, optional poster

[b]Library[/b]
  arrows         move over the posters       PgUp/PgDn  flip pages
  Enter          play a movie / open a series / play an album
  p              quick play (series: next unwatched episode)
  w              toggle watched              f  toggle favorite
  /              search                      s  cycle sort
  v              cycle view (all/unwatched/watched/favorites)
  o              open folder                 i  import files      Esc  back

[b]Episodes[/b]
  Enter          play from here (rest is queued in VLC)
  p              play only this episode      w  toggle watched
  n              jump to next unwatched

[b]Now playing (music)[/b]
  Space          pause / play                n / p      next / previous
  Left / Right   seek -10s / +10s            + / -      volume
  l              repeat off / album / track  s          shuffle
  Enter          play the highlighted track  x          stop      Esc  back (music keeps playing)

[b]Everywhere[/b]
  t              cycle theme                 T  theme picker
  ,              settings                    z  toggle VLC fullscreen
  ?              this help                   q  quit
"""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape,question_mark,q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-box"):
            yield Label("NyRun keys", id="modal-title")
            yield Static(HELP, id="help-text")
            with Horizontal(id="modal-buttons"):
                yield Button("Close", id="help-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
