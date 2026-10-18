"""Add a file: pick a video, say where it belongs, name it, optionally give it a poster."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import DirectoryTree, Footer, Header, Input, Label, OptionList, RadioButton, RadioSet, Static
from textual.widgets.option_list import Option

from .config import AUDIO_EXTS, CATEGORIES, IMAGE_EXTS, VIDEO_EXTS
from .library import ImportError_, guess_title, import_into_library
from .screens import VisibleTree

STEPS = ["file", "where", "name", "image", "confirm"]
TITLES = {
    "file": "1/5  Pick the file   (↑↓ move · → open folder · ← close · Enter selects)",
    "where": "2/5  Where does it go?   (Enter)",
    "name": "3/5  What is it called?   (a folder with this name is created)",
    "image": "4/5  Poster / cover image   (optional - Enter with an empty box to skip)",
    "confirm": "5/5  Move or copy?   (Space picks, Enter does it)",
}


def suggest_category(path: Path) -> str:
    low = path.name.lower()
    if path.suffix.lower() in AUDIO_EXTS:
        return "Music"
    if any(k in low for k in ("s01", "season", "episode")) or __import__("re").search(r"s\d{1,2}e\d{1,2}", low):
        return "Series"
    return "Movies"


def find_image(path: Path) -> str:
    for e in IMAGE_EXTS:
        cand = path.with_suffix(e)
        if cand.exists():
            return str(cand)
    return ""


class AddScreen(Screen):
    BINDINGS = [Binding("escape", "back", "Back / cancel")]

    def __init__(self, start: Path | None = None) -> None:
        super().__init__()
        self.step = "file"
        self.file: Path | None = Path(start).expanduser() if start else None
        self.category = "Movies"
        self._started_with_file = bool(self.file and self.file.is_file())

    def compose(self) -> ComposeResult:
        downloads = Path.home() / "Downloads"
        yield Header()
        with Vertical(id="add-body"):
            yield Label(id="add-title")
            yield Static(id="add-info")
            tree = VisibleTree(downloads if downloads.is_dir() else Path.home(), id="add-tree")
            tree.show_root = False
            yield tree
            yield OptionList(*[Option(c, id=c) for c in CATEGORIES], id="add-cat")
            yield Input(id="add-name")
            yield Input(placeholder="path to a .jpg / .png (or leave empty)", id="add-image")
            yield RadioSet(RadioButton("Move (cut) - the original disappears", value=True), RadioButton("Copy - keep the original"), id="add-mode")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = "Add a file"
        if self.file and self.file.is_file():
            self._go("where")
        else:
            self._go("file")

    # ----- steps -----
    def _go(self, step: str) -> None:
        self.step = step
        self.query_one("#add-title", Label).update(TITLES[step])
        for wid, s in (("#add-tree", "file"), ("#add-cat", "where"), ("#add-name", "name"), ("#add-image", "image"), ("#add-mode", "confirm")):
            self.query_one(wid).display = step == s
        info = self.query_one("#add-info", Static)
        info.update(f"▶ {self.file.name}" if self.file else "")
        if step == "file":
            self.query_one("#add-tree").focus()
        elif step == "where":
            ol = self.query_one("#add-cat", OptionList)
            ol.highlighted = CATEGORIES.index(suggest_category(self.file))
            ol.focus()
        elif step == "name":
            box = self.query_one("#add-name", Input)
            if not box.value:
                box.value = guess_title(self.file.stem)
            box.focus()
            box.cursor_position = len(box.value)
        elif step == "image":
            box = self.query_one("#add-image", Input)
            if not box.value:
                box.value = find_image(self.file)
            box.focus()
        else:
            self.query_one("#add-mode").focus()
            info.update(f"▶ {self.file.name}  →  {self.category}/{self.query_one('#add-name', Input).value.strip()}")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        p = Path(event.path)
        if p.suffix.lower() not in VIDEO_EXTS | AUDIO_EXTS:
            self.app.notify("That is not a video or music file.", severity="warning", markup=False)
            return
        self.file = p
        self.query_one("#add-name", Input).value = ""
        self.query_one("#add-image", Input).value = ""
        self._go("where")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.category = str(event.option.id)
        self._go("name")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add-name":
            if not event.value.strip():
                self.app.notify("Give it a name.", severity="warning", markup=False)
                return
            self._go("image")
        elif event.input.id == "add-image":
            img = event.value.strip()
            if img and not (Path(img).expanduser().is_file() and Path(img).suffix.lower() in IMAGE_EXTS):
                self.app.notify("That image doesn't exist (jpg, jpeg or png). Fix it or empty the box.", severity="warning", markup=False)
                return
            self._go("confirm")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        pass

    def on_key(self, event) -> None:
        if event.key == "enter" and self.step == "confirm":
            event.stop()
            self.do_add()

    @work(exclusive=True)
    async def do_add(self) -> None:
        move = self.query_one("#add-mode", RadioSet).pressed_index == 0
        name = self.query_one("#add-name", Input).value.strip()
        img = self.query_one("#add-image", Input).value.strip()
        self.app.notify("Adding... big files can take a while.", markup=False)
        try:
            dest = await asyncio.to_thread(
                import_into_library, self.file, self.app.root, self.category,
                move=move, title=name, poster=Path(img).expanduser() if img else None,
            )
        except (ImportError_, OSError) as e:
            self.app.notify(f"Could not add it: {e}", severity="error", markup=False)
            return
        self.app.rescan()
        self.app.notify(f"{'Moved' if move else 'Copied'} to {self.category}/{dest.name}" + (" with its image" if img else ""), markup=False)
        self.app.pop_screen()

    def action_back(self) -> None:
        i = STEPS.index(self.step)
        if i == 0 or (self._started_with_file and i == 1):
            self.app.pop_screen()
        else:
            self._go(STEPS[i - 1])
