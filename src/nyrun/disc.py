"""A spinning vinyl record drawn in chunky half-block pixels, with the album cover as its label."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

FRAMES = 24  # one full turn
FPS = 12  # 24 frames at 12 fps = a calm half turn per second
VINYL = (22, 22, 28)
_SS = 4  # supersampling before the final pixelation


def _hex_rgb(value: str | None, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        v = (value or "").lstrip("#")
        if len(v) == 6:
            return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError:
        pass
    return fallback


def _label_from_cover(path: Path, diameter: int) -> Image.Image | None:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            side = min(im.size)
            left, top = (im.width - side) // 2, (im.height - side) // 2
            return im.crop((left, top, left + side, top + side)).resize((diameter, diameter), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - a broken cover just means the plain label
        return None


def build_base(cover: Path | None, px: int, accent: tuple[int, int, int], secondary: tuple[int, int, int]) -> Image.Image:
    """The record at rest, supersampled. Rotating this gives every animation frame."""
    size = px * _SS
    c = size / 2
    r = c - 2

    def box(radius: float) -> tuple[float, float, float, float]:
        return (c - radius, c - radius, c + radius, c + radius)

    img = Image.new("RGB", (size, size), VINYL)
    d = ImageDraw.Draw(img)
    d.ellipse(box(r), fill=(48, 48, 58))  # rim
    bands = 9
    for k in range(bands):
        radius = r * (0.97 - k * (0.60 / bands))
        d.ellipse(box(radius), fill=(27, 27, 34) if k % 2 == 0 else (17, 17, 22))

    glare = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g = ImageDraw.Draw(glare)
    for start, end in ((-14, 14), (166, 194)):
        g.pieslice(box(r * 0.97), start, end, fill=(255, 255, 255, 26))
    img = Image.alpha_composite(img.convert("RGBA"), glare).convert("RGB")
    d = ImageDraw.Draw(img)

    label = r * 0.44
    art = _label_from_cover(cover, int(label * 2)) if cover else None
    if art is not None:
        mask = Image.new("L", art.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, art.width - 1, art.height - 1), fill=255)
        img.paste(art, (int(c - art.width / 2), int(c - art.height / 2)), mask)
    else:
        d.ellipse(box(label), fill=accent)
        d.pieslice(box(label), 0, 180, fill=secondary)
        d.ellipse((c + label * 0.45 - 3 * _SS, c - 3 * _SS, c + label * 0.45 + 3 * _SS, c + 3 * _SS), fill=(255, 255, 255))
    d.ellipse(box(label), outline=(8, 8, 10), width=max(1, _SS))
    return img


def build_mask(px: int) -> Image.Image:
    """Which pixels belong to the record (round, with the spindle hole)."""
    size = px * _SS
    c = size / 2
    r = c - 2
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.ellipse((c - r, c - r, c + r, c + r), fill=255)
    hole = max(r * 0.05, _SS)
    d.ellipse((c - hole, c - hole, c + hole, c + hole), fill=0)
    return m.resize((px, px), Image.BOX).point(lambda v: 255 if v >= 128 else 0)


_STYLES: dict[tuple, Style] = {}


def _style(fg: tuple[int, int, int] | None, bg: tuple[int, int, int] | None) -> Style | None:
    if fg is None and bg is None:
        return None
    key = (fg, bg)
    st = _STYLES.get(key)
    if st is None:
        st = Style(
            color=Color.from_rgb(*fg) if fg else None,
            bgcolor=Color.from_rgb(*bg) if bg else None,
        )
        if len(_STYLES) > 20000:
            _STYLES.clear()
        _STYLES[key] = st
    return st


def to_text(img: Image.Image, mask: Image.Image) -> Text:
    """Two image rows per terminal row using the upper-half block."""
    px = img.width
    pix, m = img.load(), mask.load()
    out = Text(no_wrap=True, overflow="crop")
    for y in range(0, px - 1, 2):
        run_style: Style | None = None
        run = ""
        started = False
        for x in range(px):
            top = pix[x, y] if m[x, y] else None
            bot = pix[x, y + 1] if m[x, y + 1] else None
            if top is None and bot is None:
                ch, st = " ", None
            elif bot is None:
                ch, st = "▀", _style(top, None)
            elif top is None:
                ch, st = "▄", _style(bot, None)
            else:
                ch, st = "▀", _style(top, bot)
            if started and st == run_style:
                run += ch
            else:
                if started:
                    out.append(run, run_style)
                run, run_style, started = ch, st, True
        if started:
            out.append(run, run_style)
        if y + 2 < px - 1:
            out.append("\n")
    return out


def render_frames(cover: Path | None, px: int, accent: str | None, secondary: str | None, frames: int = FRAMES) -> list[Text]:
    px = max(8, px - px % 2)
    base = build_base(cover, px, _hex_rgb(accent, (255, 183, 213)), _hex_rgb(secondary, (179, 157, 219)))
    mask = build_mask(px)
    out = []
    for i in range(frames):
        angle = 360.0 * i / frames
        rot = base.rotate(-angle, resample=Image.BICUBIC, fillcolor=VINYL)
        out.append(to_text(rot.resize((px, px), Image.BOX), mask))
    return out


class Disc(Static):
    """The record. `set_cover()` prepares the frames in a thread; `spinning` turns it."""

    def __init__(self, px: int = 40, **kw) -> None:
        super().__init__("", **kw)
        self.px = px
        self.frames: list[Text] = []
        self.spinning = False
        self._i = 0
        self._cover: Path | None = None
        self._key: tuple | None = None
        self._timer = None
        self._resize()

    def _resize(self) -> None:
        self.styles.width = self.px
        self.styles.height = self.px // 2

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / FPS, self._step)
        self._request()

    def pause_timer(self) -> None:
        if self._timer:
            self._timer.pause()

    def resume_timer(self) -> None:
        if self._timer:
            self._timer.resume()

    def set_px(self, px: int) -> None:
        px = max(16, px - px % 2)
        if px != self.px:
            self.px = px
            self._resize()
            self._request()

    def set_cover(self, cover: Path | None) -> None:
        if cover != self._cover:
            self._cover = cover
            self._request()

    def refresh_theme(self) -> None:
        self._request()

    def _colors(self) -> tuple[str | None, str | None]:
        th = self.app.current_theme
        accent = str(th.accent or "")
        secondary = str(th.secondary or th.primary or "")
        return (accent if accent.startswith("#") else None), (secondary if secondary.startswith("#") else None)

    def _request(self) -> None:
        if not self.is_mounted:
            return
        accent, secondary = self._colors()
        key = (str(self._cover), self.px, accent, secondary)
        if key == self._key:
            return
        self._key = key
        self.run_worker(lambda: self._build(key, self._cover, self.px, accent, secondary),
                        thread=True, exclusive=True, group="disc", name="disc")

    def _build(self, key: tuple, cover: Path | None, px: int, accent: str | None, secondary: str | None) -> None:
        frames = render_frames(cover, px, accent, secondary)
        self.app.call_from_thread(self._ready, key, frames)

    def _ready(self, key: tuple, frames: list[Text]) -> None:
        if key != self._key:
            return
        self.frames = frames
        self._i %= len(frames)
        self.update(frames[self._i])

    def _step(self) -> None:
        if not self.frames:
            return
        if self.spinning:
            self._i = (self._i + 1) % len(self.frames)
        self.update(self.frames[self._i])
