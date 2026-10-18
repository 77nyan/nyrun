from pathlib import Path

import pytest

from nyrun.library import (
    ImportError_,
    create_structure,
    guess_title,
    human_duration,
    human_size,
    import_into_library,
    natural_key,
    scan_category,
    scan_library,
    sort_items,
    thumbnail,
)

from conftest import make_poster


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Inception.2010.1080p.BluRay.x264", "Inception (2010)"),
        ("Blade Runner 2049 (2017)", "Blade Runner 2049 (2017)"),
        ("The_Matrix_1999_720p", "The Matrix (1999)"),
        ("Some Movie 1080p WEB-DL", "Some Movie"),
        ("Plain Name", "Plain Name"),
        ("Wonder Woman 1984", "Wonder Woman (1984)"),
    ],
)
def test_guess_title(raw, expected):
    assert guess_title(raw) == expected


def test_natural_sort():
    names = ["ep10", "ep2", "ep1"]
    assert sorted(names, key=natural_key) == ["ep1", "ep2", "ep10"]


def test_human_formats():
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_duration(3 * 3600 + 5 * 60) == "3h 05m"
    assert human_duration(42 * 60) == "42m"
    assert human_duration(None) == ""


def test_create_structure(tmp_path):
    created = create_structure(tmp_path / "x" / "NyRun")
    assert len(created) == 5
    for c in ("Movies", "Series", "Shows"):
        assert (tmp_path / "x" / "NyRun" / c).is_dir()
    assert create_structure(tmp_path / "x" / "NyRun") == []  # idempotent


def test_scan_movies(library):
    items = {i.title: i for i in scan_category(library, "Movies")}
    assert "Inception" in items and items["Inception"].year == "2010"
    assert items["Inception"].poster.name == "poster.jpg"
    assert items["Alien"].subtitles  # .srt detected
    # sample file skipped, biggest file is the main video, no poster -> None
    np = items["No Poster"]
    assert np.poster is None and np.main_video.name == "movie.mkv"
    # loose file picks up the same-named image
    loose = items["Loose Film"]
    assert loose.poster.name == "Loose Film (2001).png" and loose.year == "2001"
    # hidden entries are ignored
    assert not any(".hidden" in i.path.name for i in items.values())
    # title with a year inside keeps the right year
    assert items["Blade Runner 2049"].year == "2017"


def test_scan_series_order_and_collection(library):
    (bebop,) = scan_category(library, "Series")
    labels = [e.label for e in bebop.episodes]
    assert labels == [
        "Season 1 / ep1", "Season 1 / ep2", "Season 1 / ep10", "Season 2 / ep1",
    ]
    assert bebop.is_collection and bebop.poster.name == "cover.png"
    (earth,) = scan_category(library, "Shows")
    assert earth.is_collection and earth.poster is None


def test_scan_missing_category(tmp_path):
    assert scan_category(tmp_path, "Movies") == []
    assert set(scan_library(tmp_path)) == {"Movies", "Series", "Shows", "Music"}


def test_sort_modes(library):
    items = scan_category(library, "Movies")
    by_name = [i.title for i in sort_items(items, "name", {})]
    assert by_name == sorted(by_name, key=str.lower)
    by_year = sort_items(items, "year", {})
    assert by_year[0].year == "2017"
    played = sort_items(items, "played", {items[2].key: 100.0})
    assert played[0] is items[2]


def test_thumbnail_is_cached_and_small(tmp_path):
    big = tmp_path / "big.png"
    make_poster(big, size=(3000, 4500))
    t = thumbnail(big, max_px=300)
    assert t and t.exists()
    from PIL import Image

    with Image.open(t) as im:
        assert im.width <= 300
    assert thumbnail(big, max_px=300) == t  # cache hit


def test_thumbnail_survives_corrupt_image(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    assert thumbnail(bad) is None


# ------------------------------ import -------------------------------------

def test_import_single_video_gets_folder_and_sidecars(tmp_path, library):
    dl = tmp_path / "home" / "Downloads"
    video = dl / "Heat.1995.mkv"
    video.write_bytes(b"0" * 100)
    (dl / "Heat.1995.srt").write_text("sub")
    make_poster(dl / "Heat.1995.jpg")
    (dl / "unrelated.txt").write_text("keep me")
    dest = import_into_library(video, library, "Movies", title="Heat (1995)")
    assert dest == library / "Movies" / "Heat (1995)"
    assert (dest / "Heat.1995.mkv").exists()
    assert (dest / "Heat.1995.srt").exists() and (dest / "Heat.1995.jpg").exists()
    assert not video.exists()  # cut
    assert (dl / "unrelated.txt").exists()
    (item,) = [i for i in scan_category(library, "Movies") if i.title == "Heat"]
    assert item.poster is not None


def test_import_copy_keeps_source(tmp_path, library):
    src = tmp_path / "home" / "Downloads" / "Cool Show"
    (src / "Season 1").mkdir(parents=True)
    (src / "Season 1" / "e1.mkv").write_bytes(b"0")
    dest = import_into_library(src, library, "Series", move=False)
    assert src.exists() and (dest / "Season 1" / "e1.mkv").exists()


def test_import_dir_move(tmp_path, library):
    src = tmp_path / "home" / "Downloads" / "Moved Film"
    src.mkdir()
    (src / "m.mp4").write_bytes(b"0")
    dest = import_into_library(src, library, "Movies")
    assert not src.exists() and (dest / "m.mp4").exists()


def test_import_never_overwrites(tmp_path, library):
    a = tmp_path / "home" / "Downloads" / "a.mkv"
    a.write_bytes(b"1")
    first = import_into_library(a, library, "Movies", title="Dup")
    b = tmp_path / "home" / "Downloads" / "b.mkv"
    b.write_bytes(b"2")
    second = import_into_library(b, library, "Movies", title="Dup")
    assert first != second and first.exists() and second.exists()


def test_import_explicit_poster(tmp_path, library):
    v = tmp_path / "home" / "Downloads" / "v.mkv"
    v.write_bytes(b"0")
    p = tmp_path / "home" / "Downloads" / "art.png"
    make_poster(p)
    dest = import_into_library(v, library, "Movies", title="With Art", poster=p)
    assert (dest / "poster.png").exists() and p.exists()


def test_import_errors(tmp_path, library):
    with pytest.raises(ImportError_):
        import_into_library(tmp_path / "nope.mkv", library, "Movies")
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(ImportError_):
        import_into_library(txt, library, "Movies")
    with pytest.raises(ImportError_):
        import_into_library(library / "Movies" / "Alien (1979)", library, "Movies")  # already there
    with pytest.raises(ImportError_):
        import_into_library(library, library, "Movies")  # folder into itself
    with pytest.raises(ImportError_):
        import_into_library(txt, library, "Nope")


def test_import_sanitises_title(tmp_path, library):
    v = tmp_path / "x.mkv"
    v.write_bytes(b"0")
    dest = import_into_library(v, library, "Movies", title='Bad:/Name?"')
    assert dest.parent == library / "Movies" and ":" not in dest.name and "?" not in dest.name
