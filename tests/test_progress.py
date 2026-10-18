"""Real watch progress: a film counts as watched only when you get far enough."""

from __future__ import annotations

import asyncio
import time

from nyrun.app import NyRunApp
from nyrun.config import Settings, State
from nyrun.library import scan_category
from nyrun.screens import HomeScreen, VisibleTree

SIZE = (150, 48)
ALIEN = "Movies/Alien (1979)"


def make_app(library, **kw):
    return NyRunApp(Settings(default_folder=str(library), **kw), State())


def movie(app, title="Alien"):
    return next(i for i in app.library["Movies"] if i.title == title)


async def wait_for(pilot, cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        await pilot.pause(0.1)
        if cond():
            return True
    return False


def test_half_watched_film_is_not_marked_watched(library, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=50, speed=40)

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: "EXIT" in (log.read_text() if log.exists() else ""))
            await pilot.pause(0.6)
            st = app.state
            assert ALIEN not in st.watched
            pos, total = st.positions[ALIEN]
            assert 40 <= pos <= 70 and total == 100
            assert 40 <= st.percent(ALIEN) <= 70
            # it can be continued, and the home tile says so
            assert app.continue_target() is not None
            app.screen.refresh_home() if hasattr(app.screen, "refresh_home") else None
            return app

    run = asyncio.run(go())
    saved = State()  # written to disk as well
    assert ALIEN not in saved.watched and ALIEN in saved.positions


def test_finished_film_is_marked_watched(library, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=96, speed=60)

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: ALIEN in app.state.watched)
            assert ALIEN not in app.state.positions
            assert app.continue_target() is None

    asyncio.run(go())
    assert ALIEN in State().watched


def test_threshold_is_a_setting(library, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=60, speed=60)

    async def go():
        app = make_app(library, watched_at=50)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: ALIEN in app.state.watched)

    asyncio.run(go())


def test_playing_again_resumes_where_you_stopped(library, fake_watch_vlc):
    log = fake_watch_vlc(length=1000, stop=300, speed=150)

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            await pilot.pause(0.5)
            first = app.state.positions[ALIEN][0]
            assert first >= 200
            log.unlink()
            fake_watch_vlc(length=1000, stop=700, speed=150)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            text = log.read_text()
            assert f"CMD seek {int(first)}" in text or any(
                ln.startswith("CMD seek") and abs(int(ln.split()[2]) - first) <= 1 for ln in text.splitlines()
            )

    asyncio.run(go())


def test_resume_can_be_turned_off(library, fake_watch_vlc):
    log = fake_watch_vlc(length=1000, stop=300, speed=150)

    async def go():
        app = make_app(library, resume=False)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.state.set_position(ALIEN, 400, 1000)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            assert "CMD seek" not in log.read_text()

    asyncio.run(go())


def test_tracking_off_launches_plain_vlc_and_marks_nothing(library, fake_vlc):
    async def go():
        app = make_app(library, track_progress=False)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play(movie(app))
            await pilot.pause(0.5)
            assert not app.state.watched

    asyncio.run(go())
    line = fake_vlc.read_text().splitlines()[0]
    assert "--extraintf" not in line and "--rc-host" not in line


def test_series_resumes_the_episode_you_left(library, fake_watch_vlc):
    log = fake_watch_vlc(length=100, stop=40, speed=40)

    async def go():
        app = make_app(library)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            show = next(i for i in app.library["Series"] if i.title == "Cowboy Bebop")
            app.play(show, 1)  # start on episode 2 (index 1)
            assert await wait_for(pilot, lambda: log.exists() and "EXIT" in log.read_text())
            await pilot.pause(0.5)
            ep2 = show.episodes[1].key
            assert ep2 not in app.state.watched and app.state.percent(ep2)
            item, idx = app.continue_target()
            assert item.key == show.key and idx == 1  # same episode, not the next one
            assert app.next_episode_index(show) == 1

    asyncio.run(go())


def test_marking_by_hand_forgets_the_halfway_point(library):
    st = State()
    st.set_position(ALIEN, 50, 100)
    assert st.percent(ALIEN) == 50 and st.resume_point(ALIEN) == 50
    st.toggle_watched(ALIEN)
    assert st.percent(ALIEN) is None and st.resume_point(ALIEN) is None
    st.set_position("x", 5, 100)  # barely started: nothing worth resuming
    assert st.resume_point("x") is None


def test_add_tree_has_no_file_icon(library):
    assert VisibleTree.ICON_FILE == ""


def test_vlc_that_rejects_tracking_options_still_plays(library, tmp_path, monkeypatch):
    """Real-world bug: a VLC without --rc-quiet exited with 'unknown option'. NyRun must retry plain."""
    import stat

    log = tmp_path / "calls.log"
    script = tmp_path / "picky-vlc"
    script.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(log) + '"\n'
        'case "$*" in *--rc-host*) echo "vlc: unknown option or missing mandatory argument \\`--rc-host\'" >&2; exit 1;; esac\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("NYRUN_VLC", str(script))
    seen = []

    async def go():
        app = make_app(library)
        orig = app.notify
        app.notify = lambda msg, **kw: (seen.append((str(msg), kw.get("severity"))), orig(msg, **kw))[1]
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            app.play(movie(app))
            assert await wait_for(pilot, lambda: any(sev == "warning" for _, sev in seen), timeout=12)
            assert app._no_tracking
            app.play(movie(app))  # the next time it does not even try tracking
            await pilot.pause(0.6)

    asyncio.run(go())
    lines = log.read_text().splitlines()
    assert "--rc-host" in lines[0] and "--rc-host" not in lines[1]
    assert "--rc-host" not in lines[2]
    assert not any(sev == "error" for _, sev in seen)


def test_tracking_options_are_ones_vlc_3_knows():
    from nyrun import player

    args = player.watch_args(1234, Settings())
    assert args[:2] == ["--extraintf=rc", "--rc-host=127.0.0.1:1234"]
    assert "--rc-quiet" not in args


def _tile_text(app, cat: str) -> str:
    from nyrun.screens import HomeScreen

    home = next(s for s in app.screen_stack if isinstance(s, HomeScreen))
    return str(home.query_one(f"#tile-{cat}").render())


def test_progress_units_count_part_watched_films(library):
    app = make_app(library)
    film = movie(app)
    assert app.progress_units(film) == 0
    app.state.set_position(ALIEN, 74, 100)
    assert abs(app.progress_units(film) - 0.74) < 0.001
    app.state.set_watched(ALIEN, True)
    assert app.progress_units(film) == 1


def test_home_category_percentage_follows_part_watched_film(tmp_path):
    from conftest import make_poster

    root = tmp_path / "NyRun"
    for cat in ("Movies", "Series", "Shows", "Music"):
        (root / cat).mkdir(parents=True)
    d = root / "Movies" / "Alien (1979)"
    d.mkdir()
    (d / "film.mp4").write_bytes(b"0" * 2048)
    make_poster(d / "poster.jpg")

    async def go():
        app = make_app(root)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause(0.3)
            assert "0%" in _tile_text(app, "Movies")
            app.state.set_position(ALIEN, 74, 100)
            app.screen._draw_tiles()
            await pilot.pause(0.1)
            assert "74%" in _tile_text(app, "Movies")

    asyncio.run(go())
