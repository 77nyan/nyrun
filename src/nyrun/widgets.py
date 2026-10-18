"""Reusable widgets: poster images, cards, focusable tiles, the rainbow trail."""

from __future__ import annotations

import colorsys
from functools import lru_cache
from pathlib import Path

from PIL import Image as PILImage
from rich.text import Text
from textual import events
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, Static

# NOTE: textual_image probes the terminal on import, so this module must be
# imported *before* Textual starts (cli.py does that via `from .app import ...`).
from textual_image.widget import Image as TermImage

from .library import Item, human_size, placeholder_image, thumbnail


def _cell_px() -> tuple[int, int]:
    try:
        from textual_image._terminal import get_cell_size

        cs = get_cell_size()
        w, h = int(cs.width), int(cs.height)
        if w > 0 and h > 0:
            return w, h
    except Exception:  # noqa: BLE001
        pass
    return 10, 20


def _cover(im: PILImage.Image, tw: int, th: int) -> PILImage.Image:
    """Resize + crop so the image fully covers tw x th (like CSS object-fit: cover)."""
    scale = max(tw / im.width, th / im.height)
    nw, nh = max(1, round(im.width * scale)), max(1, round(im.height * scale))
    im = im.resize((nw, nh), PILImage.LANCZOS)
    left = (nw - tw) // 2
    top = int((nh - th) * 0.3)  # bias towards the top: faces & titles live there
    return im.crop((left, top, left + tw, top + th))


@lru_cache(maxsize=512)
def _poster_cached(path: str | None, mtime: float, cols: int, rows: int) -> PILImage.Image:
    cw, ch = _cell_px()
    tw, th = cols * cw, rows * ch
    if path:
        thumb = thumbnail(Path(path), max_px=max(420, tw))
        if thumb:
            try:
                with PILImage.open(thumb) as im:
                    return _cover(im.convert("RGB"), tw, th)
            except Exception:  # noqa: BLE001
                pass
    return _cover(placeholder_image(), tw, th)


def poster_for(item: Item | None, cols: int, rows: int) -> PILImage.Image:
    if item is None or item.poster is None:
        return _poster_cached(None, 0.0, cols, rows)
    try:
        mtime = item.poster.stat().st_mtime
    except OSError:
        mtime = 0.0
    return _poster_cached(str(item.poster), mtime, cols, rows)


def make_poster(item: Item | None, cols: int, rows: int, **kw) -> TermImage:
    img = TermImage(poster_for(item, cols, rows), on_error=lambda e: Static("(poster error)"), **kw)
    img.styles.width = cols
    img.styles.height = rows
    return img


def set_poster(widget: TermImage, item: Item | None, cols: int, rows: int) -> None:
    widget.styles.width = cols
    widget.styles.height = rows
    widget.image = poster_for(item, cols, rows)


class PosterCard(Vertical):
    """A poster with the title underneath. Selection is driven by the screen."""

    class Clicked(Message):
        def __init__(self, card: "PosterCard") -> None:
            super().__init__()
            self.card = card

    def __init__(self, item: Item, cols: int, rows: int, badge: str) -> None:
        super().__init__(classes="card")
        self.item = item
        self._cols, self._rows, self._badge = cols, rows, badge
        self.styles.width = cols + 2

    def compose(self):
        yield make_poster(self.item, self._cols, self._rows)
        yield Label(self.item.title, classes="card-title", markup=False)
        yield Label(self._badge, classes="card-sub", markup=False)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Clicked(self))


class Tile(Static, can_focus=True):
    """A big focusable button used on the home screen."""

    class Pressed(Message):
        def __init__(self, tile: "Tile") -> None:
            super().__init__()
            self.tile = tile

    BINDINGS = [("enter", "press", "Open")]

    def __init__(self, tile_id: str, *, classes: str = "") -> None:
        super().__init__("", id=tile_id, classes=f"tile {classes}".strip())

    def action_press(self) -> None:
        self.post_message(self.Pressed(self))

    def on_click(self) -> None:
        self.focus()
        self.post_message(self.Pressed(self))


def bar(fraction: float, width: int = 14) -> str:
    fraction = max(0.0, min(1.0, fraction))
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def tile_text(title: str, lines: list[str], *, glyph: str = "", dim: bool = False) -> Text:
    t = Text(justify="center")
    if glyph:
        t.append(glyph + "\n", style="bold")
    t.append(title + "\n", style="bold" + (" dim" if dim else ""))
    for ln in lines:
        t.append(ln + "\n", style="dim")
    t.rstrip()
    return t


def lerp_hex(a: str, b: str, t: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i : i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i : i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def theme_pair(app) -> tuple[str, str]:
    """The two colours NyRun's logo and line are drawn with: the current theme's accent and secondary."""
    th = app.current_theme
    c1, c2 = str(th.accent or ""), str(th.secondary or th.primary or "")
    return (c1 if c1.startswith("#") else "#ffb7d5"), (c2 if c2.startswith("#") else "#b39ddb")


class Rainbow(Static):
    """The animated line under the logo, exactly as wide as the logo.

    Setting `logo_line`: "rainbow" (RGB) or "theme" (a flowing blend of your theme's colours,
    which includes your Omarchy theme).
    """

    def __init__(self, width: int) -> None:
        super().__init__("", id="rainbow")
        self._w = width
        self._phase = 0.0
        self._timer = None

    def on_mount(self) -> None:
        self._timer = self.set_interval(0.09, self._tick)
        self._tick()

    def pause(self) -> None:
        if self._timer:
            self._timer.pause()

    def resume(self) -> None:
        if self._timer:
            self._timer.resume()

    def line(self) -> Text:
        mode = getattr(self.app.settings, "logo_line", "rainbow")
        t = Text(no_wrap=True)
        if mode == "theme":
            c1, c2 = theme_pair(self.app)
            for i in range(self._w):
                wave = abs(((self._phase + i / self._w) % 1.0) * 2 - 1)  # 0..1..0, so the loop is seamless
                t.append("▬", style=lerp_hex(c1, c2, wave))
        else:
            for i in range(self._w):
                r, g, b = colorsys.hsv_to_rgb((self._phase + i / self._w) % 1.0, 0.45, 1.0)
                t.append("▬", style=f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
        return t

    def _tick(self) -> None:
        self._phase = (self._phase + 0.025) % 1.0
        self.update(self.line())


def badge_for(item: Item, watched_done: int, total: int, favorite: bool) -> str:
    bits = []
    if item.year:
        bits.append(item.year)
    if item.is_music:
        n = len(item.episodes)
        bits.append(f"{n} track{'s' if n != 1 else ''}")
    if item.is_collection:
        bits.append(f"{watched_done}/{total} eps")
    if favorite:
        bits.append("♥")
    if not bits and not (total and watched_done >= total):
        bits.append(human_size(item.total_size))
    mark = "✓ " if total and watched_done >= total else ""
    return mark + " · ".join(bits)
