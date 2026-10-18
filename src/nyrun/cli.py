"""Command line entry point:  nyrun [-createfolder [PATH]] [-default PATH] ..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .config import CATEGORIES, Settings
from .library import create_structure, human_size, scan_library

console = Console()

BANNER = r"""
 _   _       ____
| \ | |_   _|  _ \ _   _ _ __
|  \| | | | | |_) | | | | '_ \
| |\  | |_| |  _ <| |_| | | | |
|_| \_|\__, |_| \_\\__,_|_| |_|
       |___/
"""

DEFAULT_FOLDER_NAME = "NyRun"


def _print_banner() -> None:
    console.print(Text(BANNER, style="bold #ffb7d5"), end="")
    console.print(f"  [dim]v{__version__} - a cozy video & music selector for VLC[/]\n")


def _help() -> None:
    from rich.table import Table

    _print_banner()
    rows = [
        ("nyrun", "open the library (TUI)"),
        ("nyrun -createfolder [PATH]", f"create the NyRun folder (default: ~/{DEFAULT_FOLDER_NAME})"),
        ("nyrun -default PATH", "choose which folder nyrun opens"),
        ("nyrun -add [FILE]", "guided: pick a file, choose where it goes, name it, optional poster"),
        ("nyrun -import PATH", "move / copy a file or folder in from Downloads or any directory"),
        ("    -to Movies|Series|Shows|Music", "where to put it (default: Movies)"),
        ("    -copy  /  -title NAME", "copy instead of move  /  folder name"),
        ("nyrun -vlc", "VLC settings & compatibility options"),
        ("nyrun -vlc PATH", "just tell nyrun where VLC is"),
        ("nyrun -theme", "theme picker with live preview"),
        ("nyrun -theme NAME", "set a theme (auto, omarchy, terminal, tokyo-night ...)"),
        ("nyrun -theme list", "show every theme name"),
        ("nyrun -list", "print your library without the TUI"),
        ("nyrun -doctor", "check VLC, ffprobe, poster support"),
        ("nyrun -version", "show the version"),
    ]
    grid = Table.grid(padding=(0, 2))
    for cmd, desc in rows:
        grid.add_row(f"[bold]{cmd}[/]", f"[dim]{desc}[/]")
    console.print(Panel.fit(grid, title="usage", border_style="#ffb7d5"))
    console.print(
        "\n[dim]Layout:  <NyRun>/Movies/<Film Name (2010)>/film.mp4 + poster.jpg\n"
        "         <NyRun>/Series/<Show>/Season 1/ep01.mkv + poster.png\n"
        "         <NyRun>/Music/<Album>/01 Song.mp3 + cover.jpg[/]\n"
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nyrun", add_help=False, allow_abbrev=False)
    p.add_argument("-createfolder", "--createfolder", nargs="?", const="", metavar="PATH")
    p.add_argument("-default", "--default", metavar="PATH")
    p.add_argument("-vlc", "--vlc", nargs="?", const="", metavar="PATH")
    p.add_argument("-theme", "--theme", nargs="?", const="", metavar="NAME")
    p.add_argument("-add", "--add", nargs="?", const="", metavar="FILE")
    p.add_argument("-import", "--import", dest="import_path", metavar="PATH")
    p.add_argument("-to", "--to", metavar="CATEGORY", default="Movies")
    p.add_argument("-title", "--title", metavar="NAME")
    p.add_argument("-copy", "--copy", action="store_true")
    p.add_argument("-list", "--list", action="store_true")
    p.add_argument("-doctor", "--doctor", action="store_true")
    p.add_argument("-version", "--version", action="store_true")
    p.add_argument("-h", "-help", "--help", action="store_true", dest="help")
    return p


def cmd_createfolder(arg: str) -> int:
    settings = Settings.load()
    path = Path(arg).expanduser() if arg else Path.home() / DEFAULT_FOLDER_NAME
    path = path.resolve()
    try:
        created = create_structure(path)
    except OSError as e:
        console.print(f"[red]Could not create {path}:[/] {e}")
        return 1
    if created:
        console.print(f"[green]Created[/] [bold]{path}[/]")
        for c in CATEGORIES:
            console.print(f"  [dim]|-[/] {c}")
    else:
        console.print(f"[yellow]Already set up:[/] {path}")
    if not settings.default_folder:
        settings.default_folder = str(path)
        settings.save()
        console.print("[green]Set as your default folder.[/] Now just run [bold]nyrun[/].")
    else:
        console.print(
            f"[dim]Your default folder is still {settings.default_folder}. "
            f"To switch: nyrun -default \"{path}\"[/]"
        )
    return 0


def cmd_default(arg: str) -> int:
    path = Path(arg).expanduser().resolve()
    if not path.exists():
        console.print(f"[red]That folder doesn't exist:[/] {path}")
        console.print(f"[dim]Create it with:  nyrun -createfolder \"{path}\"[/]")
        return 1
    if not path.is_dir():
        console.print(f"[red]Not a folder:[/] {path}")
        return 1
    missing = [c for c in CATEGORIES if not (path / c).is_dir()]
    if missing:
        create_structure(path)
        console.print(f"[dim]Added missing sub-folders: {', '.join(missing)}[/]")
    settings = Settings.load()
    settings.default_folder = str(path)
    settings.save()
    console.print(f"[green]Default folder set to[/] [bold]{path}[/]")
    return 0


def _run_screen(factory) -> int:
    """Run a single screen as its own little app (no library needed)."""
    from . import widgets as _widgets  # noqa: F401  (terminal probing must happen before Textual starts)
    from .app import ScreenApp

    ScreenApp(Settings.load(), factory).run()
    return 0


def cmd_vlc(arg: str) -> int:
    if arg:  # nyrun -vlc /path/to/vlc
        s = Settings.load()
        s.vlc_path = arg
        s.save()
        console.print(f"[green]VLC path saved:[/] {arg}")
        return 0
    from .settings_screens import SettingsScreen

    return _run_screen(SettingsScreen)


def cmd_theme(arg: str) -> int:
    from . import themes

    if not arg:
        from .settings_screens import ThemePickerScreen

        return _run_screen(ThemePickerScreen)
    names = themes.choice_names()
    if arg == "list":
        from . import omarchy

        for e in themes.catalog():
            console.print(f"  [bold]{e.choice}[/]  [dim]{e.note}[/]")
        if omarchy.detected():
            console.print(f"\n[dim]Omarchy detected: {omarchy.theme_name()}[/]")
        return 0
    path = Path(arg).expanduser()
    if arg not in names and path.is_file() and path.suffix == ".toml":
        # nyrun -theme ~/somewhere/colors.toml  ->  install it as a custom theme
        import shutil

        dest_dir = themes.user_theme_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        stem = path.parent.name if path.stem == "colors" and path.parent.name else path.stem
        shutil.copy2(path, dest_dir / f"{stem}.toml")
        arg = stem
        if arg not in themes.choice_names():
            console.print("[red]That file has no usable colours.[/] It needs at least background and foreground.")
            (dest_dir / f"{stem}.toml").unlink(missing_ok=True)
            return 1
        console.print(f"[green]Installed theme[/] {stem}")
    elif arg not in names:
        console.print(f"[red]Unknown theme[/] {arg!r}. Run [bold]nyrun -theme list[/] to see them, or [bold]nyrun -theme[/] for the picker.")
        return 1
    s = Settings.load()
    s.theme = arg
    s.save()
    console.print(f"[green]Theme set to[/] {arg}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from .library import ImportError_, import_into_library

    settings = Settings.load()
    root = settings.root
    if not root or not root.is_dir():
        console.print("[red]No default folder.[/] Run: nyrun -createfolder")
        return 1
    cat = next((c for c in CATEGORIES if c.lower() == args.to.lower()), None)
    if cat is None:
        console.print(f"[red]Unknown category[/] {args.to!r}. Choose: {', '.join(CATEGORIES)}")
        return 1
    try:
        dest = import_into_library(
            Path(args.import_path), root, cat, move=not args.copy, title=args.title
        )
    except (ImportError_, OSError) as e:
        console.print(f"[red]Import failed:[/] {e}")
        return 1
    console.print(f"[green]{'Copied' if args.copy else 'Moved'}[/] -> {dest}")
    return 0


def cmd_doctor() -> int:
    import shutil

    from . import omarchy, player, themes
    from .player import VLCNotFound, find_vlc

    settings = Settings.load()
    ok = True
    console.print("[bold]NyRun doctor[/]\n")
    try:
        cmd = find_vlc(settings.vlc_path, settings.vlc_launcher)
        console.print(f"[green]ok[/]   VLC: {' '.join(cmd)}")
        try:
            console.print(f"[green]ok[/]   {player.version(settings)}")
        except VLCNotFound as e:
            ok = False
            console.print(f"[red]FAIL[/] VLC did not answer: {e}")
        if player.is_flatpak(cmd):
            console.print(
                "[yellow]info[/] Flatpak VLC only sees folders it is allowed to. If nothing plays, run:\n"
                f"       flatpak override --user --filesystem={settings.default_folder or '<your NyRun folder>'} org.videolan.VLC"
            )
    except VLCNotFound as e:
        ok = False
        console.print(f"[red]FAIL[/] {e}")
    if shutil.which("ffprobe"):
        console.print("[green]ok[/]   ffprobe found (movie durations will be shown)")
    else:
        console.print("[yellow]info[/] ffprobe not found - durations hidden (install ffmpeg to enable)")
    try:
        import mutagen

        console.print(f"[green]ok[/]   mutagen {mutagen.version_string} (music tags, track lengths, embedded covers)")
    except Exception:  # noqa: BLE001
        console.print("[yellow]info[/] mutagen missing - music works, but without tags, lengths and embedded covers (pip install mutagen)")
    root = settings.root
    if root and root.is_dir():
        console.print(f"[green]ok[/]   default folder: {root}")
    else:
        ok = False
        console.print("[red]FAIL[/] no default folder - run: nyrun -createfolder")
    res = themes.resolve(settings.theme)
    console.print(f"[green]ok[/]   theme: {settings.theme} -> {'terminal colours' if res.palette is None else res.label}")
    if omarchy.detected():
        console.print(f"[green]ok[/]   Omarchy detected (theme: {omarchy.theme_name()}), NyRun follows it with theme 'auto'")
    else:
        console.print("[dim]info[/] Omarchy not detected (fine - pick any theme with: nyrun -theme)")
    console.print(f"[dim]info[/] command NyRun will run: {player.preview_command(settings)}")
    try:
        from textual_image.renderable import Image as _R

        console.print(f"[green]ok[/]   poster renderer: {_R.__module__.split('.')[-1]}")
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]info[/] poster renderer unavailable: {e}")
    return 0 if ok else 1


def cmd_list() -> int:
    settings = Settings.load()
    root = settings.root
    if not root or not root.is_dir():
        console.print("[red]No default folder.[/] Run: nyrun -createfolder")
        return 1
    lib = scan_library(root)
    for cat, items in lib.items():
        console.print(f"\n[bold #ffb7d5]{cat}[/] [dim]({len(items)})[/]")
        for it in items:
            extra = (f" - {len(it.episodes)} tracks" if it.is_music else
                     f" - {len(it.episodes)} episodes" if it.is_collection else "")
            poster = "" if it.poster else (" [yellow](no cover)[/]" if it.is_music else " [yellow](no poster)[/]")
            console.print(f"  {it.title} [dim]{it.year}{extra} {human_size(it.total_size)}[/]{poster}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    if args.help:
        _help()
        return 0
    if args.version:
        console.print(f"nyrun {__version__}")
        return 0
    if args.createfolder is not None:
        return cmd_createfolder(args.createfolder)
    if args.default:
        return cmd_default(args.default)
    if args.vlc is not None:
        return cmd_vlc(args.vlc)
    if args.theme is not None:
        return cmd_theme(args.theme)
    if args.import_path:
        return cmd_import(args)
    if args.doctor:
        return cmd_doctor()
    if args.list:
        return cmd_list()

    settings = Settings.load()
    root = settings.root
    if not root:
        _print_banner()
        console.print(
            "[bold]Welcome![/] You haven't set up a folder yet.\n\n"
            "  1. [bold]nyrun -createfolder[/]            create ~/NyRun with Movies / Series / Shows / Music\n"
            "     [dim]or:[/] [bold]nyrun -default /path/to/your/folder[/]\n"
            "  2. Drop a folder per film in [bold]Movies[/]: the video + a poster (jpg/png)\n"
            "  3. [bold]nyrun[/]\n"
        )
        return 1
    if not root.is_dir():
        console.print(f"[red]Your default folder is missing:[/] {root}")
        console.print("[dim]Plug the drive back in, or run: nyrun -default PATH[/]")
        return 1

    from . import widgets as _widgets  # noqa: F401  (terminal probing must happen before Textual starts)
    from .app import NyRunApp

    app = NyRunApp(settings)
    if args.add is not None:
        app.open_add = Path(args.add).expanduser() if args.add else ""
    try:
        app.run()
    finally:
        app.shutdown_music()  # never leave a hidden VLC playing after NyRun is gone
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
