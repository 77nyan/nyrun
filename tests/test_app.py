"""Headless end-to-end tests: drive the real TUI with simulated key presses."""

import asyncio

import pytest

from nyrun.app import NyRunApp
from nyrun.config import Settings, State
from nyrun.screens import EpisodeScreen, HomeScreen, ImportScreen, LibraryScreen

from conftest import make_poster

pytestmark = pytest.mark.asyncio if False else []  # plain asyncio.run, no plugin needed

SIZE = (150, 48)


def run(coro):
    return asyncio.run(coro)


def make_app(library):
    return NyRunApp(Settings(default_folder=str(library)), State())


def calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_home_to_movie_playback(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            assert isinstance(app.screen, HomeScreen)
            await pilot.press("1")
            await pilot.pause(0.5)
            assert isinstance(app.screen, LibraryScreen)
            scr = app.screen
            assert scr.items[0].title == "Alien"  # sorted by name
            await pilot.press("right")
            await pilot.press("enter")
            await pilot.pause(0.5)
            return app, scr

    app, scr = run(go())
    lines = calls(fake_vlc)
    assert len(lines) == 1 and "Blade Runner 2049" in lines[0] and "--fullscreen" in lines[0]
    assert "Movies/Blade Runner 2049 (2017)" in State().watched  # auto-marked


def test_arrow_keys_navigate_and_pages(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=(60, 24)) as pilot:  # tiny terminal -> few cards per page
            await pilot.press("1")
            await pilot.pause(0.5)
            scr = app.screen
            per = scr._per_page()
            assert per >= 1
            await pilot.press("end")
            assert scr.index == len(scr.items) - 1
            await pilot.press("home")
            assert scr.index == 0
            await pilot.press("pagedown")
            assert scr.index == min(per, len(scr.items) - 1)
            # every title is reachable and the page follows the selection
            for _ in range(len(scr.items)):
                await pilot.press("right")
            assert scr.index == len(scr.items) - 1
            assert scr.page == scr.index // per

    run(go())


def test_series_opens_episodes_and_queues_rest(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("2", "enter")
            await pilot.pause(0.5)
            assert isinstance(app.screen, EpisodeScreen)
            await pilot.press("down")  # episode 2
            await pilot.press("enter")
            await pilot.pause(0.5)
            # highlight advances to the next episode
            assert app.screen.query_one("#ep-list").highlighted == 2

    run(go())
    (line,) = calls(fake_vlc)
    assert line.count(".mkv") == 3  # ep2, ep10, season 2 ep1 queued
    assert "ep1.mkv" in line and line.index("ep2.mkv") < line.index("ep10.mkv")
    st = State()
    assert any(k.endswith("ep2.mkv") for k in st.watched)


def test_play_only_this_episode(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("2", "enter", "p")
            await pilot.pause(0.5)

    run(go())
    (line,) = calls(fake_vlc)
    assert line.count(".mkv") == 1


def test_continue_watching_resumes_next_episode(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("2", "enter", "p")  # play ep1 only
            await pilot.pause(0.4)
            await pilot.press("escape", "escape")
            await pilot.pause(0.3)
            assert isinstance(app.screen, HomeScreen)
            await pilot.press("c")
            await pilot.pause(0.4)

    run(go())
    first, second = calls(fake_vlc)
    assert "Season 1/ep1.mkv" in first
    assert "Season 1/ep2.mkv" in second  # moved on to the next episode


def test_search_filter_and_clear(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("1")
            await pilot.pause(0.4)
            scr = app.screen
            await pilot.press("slash")
            await pilot.press(*"blade")
            await pilot.pause(0.3)
            assert [i.title for i in scr.items] == ["Blade Runner 2049"]
            await pilot.press("enter")  # keep filter, return focus to grid
            await pilot.press("enter")  # play the only result
            await pilot.pause(0.4)
            await pilot.press("escape")  # clears the filter instead of leaving
            await pilot.pause(0.3)
            assert isinstance(app.screen, LibraryScreen) and len(scr.items) == 5
            await pilot.press("escape")
            await pilot.pause(0.3)
            assert isinstance(app.screen, HomeScreen)

    run(go())
    assert "Blade Runner 2049" in calls(fake_vlc)[0]


def test_watched_favorite_views_and_sort(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("1")
            await pilot.pause(0.4)
            scr = app.screen
            first = scr.current.key
            await pilot.press("w", "f")
            assert first in app.state.watched and first in app.state.favorites
            await pilot.press("v")  # unwatched
            assert all(i.key != first for i in scr.items)
            await pilot.press("v")  # watched
            assert [i.key for i in scr.items] == [first]
            await pilot.press("v")  # favorites
            assert [i.key for i in scr.items] == [first]
            await pilot.press("v")  # back to all
            assert len(scr.items) == 5
            await pilot.press("s")
            assert scr.sort == "added"

    run(go())
    st = State()  # persisted to disk
    assert st.watched and st.favorites


def test_missing_vlc_shows_error_not_crash(library, monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("shutil.which", lambda *_: None)

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("1", "enter")
            await pilot.pause(0.4)
            assert isinstance(app.screen, LibraryScreen)  # still alive
            assert not app.state.watched  # nothing marked when VLC failed

    run(go())


def test_missing_video_file_is_reported(library, fake_vlc):
    for f in (library / "Movies" / "Alien (1979)").glob("*.mp4"):
        pass

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("1")
            await pilot.pause(0.4)
            item = app.screen.current
            item.main_video.unlink()  # drive unplugged / file deleted after scan
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert not app.state.watched

    run(go())
    assert calls(fake_vlc) == []


def test_surprise_me_is_gone(library, fake_vlc):
    from textual.css.query import NoMatches

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            with pytest.raises(NoMatches):
                app.screen.query_one("#tile-surprise")
            await pilot.press("r")  # used to open "Surprise me"
            await pilot.pause(0.3)
            assert isinstance(app.screen, HomeScreen)

    run(go())
    from nyrun import screens

    assert not hasattr(screens, "SurpriseScreen")
    assert "urprise" not in screens.HELP
    assert calls(fake_vlc) == []


def test_theme_cycle_persists(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.2)
            await pilot.press("t")
            await pilot.pause(0.2)
            return app.settings.theme

    chosen = run(go())
    assert chosen != "auto"
    assert Settings.load().theme == chosen


def test_fullscreen_toggle_changes_vlc_args(library, fake_vlc):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("z", "1", "enter")
            await pilot.pause(0.4)

    run(go())
    assert "--fullscreen" not in calls(fake_vlc)[0]


def test_empty_library_screens_do_not_crash(tmp_path, fake_vlc):
    from nyrun.library import create_structure

    root = tmp_path / "Empty"
    create_structure(root)

    async def go():
        app = make_app(root)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.2)
            for key in ("1", "enter", "p", "w", "f", "r", "s", "v", "escape", "r", "escape", "c", "question_mark"):
                await pilot.press(key)
                await pilot.pause(0.1)

    run(go())
    assert calls(fake_vlc) == []


def test_import_screen_moves_highlighted_file(library, fake_vlc, isolated_env):
    dl = isolated_env / "Downloads"
    (dl / "Heat.1995.1080p.BluRay.mkv").write_bytes(b"0" * 100)
    make_poster(dl / "Heat.1995.1080p.BluRay.jpg")

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("i")
            await pilot.pause(0.5)
            scr = app.screen
            assert isinstance(scr, ImportScreen)
            for _ in range(40):  # tree loads asynchronously
                await pilot.pause(0.1)
                if scr.query_one("#imp-tree").cursor_node is not None:
                    break
            tree = scr.query_one("#imp-tree")
            # walk the cursor until the video is highlighted
            for _ in range(10):
                if scr.selected and scr.selected.suffix == ".mkv":
                    break
                await pilot.press("down")
                await pilot.pause(0.1)
            assert scr.selected and scr.selected.name.startswith("Heat")
            assert scr.query_one("#imp-title").value == "Heat (1995)"
            assert scr.query_one("#imp-poster").value.endswith(".jpg")
            await pilot.press("ctrl+g")
            for _ in range(40):
                await pilot.pause(0.1)
                if not isinstance(app.screen, ImportScreen):
                    break
            assert not isinstance(app.screen, ImportScreen)
            assert any(i.title == "Heat" for i in app.library["Movies"])

    run(go())
    assert (library / "Movies" / "Heat (1995)" / "Heat.1995.1080p.BluRay.mkv").exists()
    assert not (isolated_env / "Downloads" / "Heat.1995.1080p.BluRay.mkv").exists()


def test_import_without_selection_warns(library):
    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("i")
            await pilot.pause(0.4)
            app.screen.selected = None
            await pilot.press("ctrl+g")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ImportScreen)

    run(go())
