from pathlib import Path

import pytest
from pyfakefs.fake_filesystem_unittest import Patcher

from program.libraries.symlink import SymlinkLibrary
from program.media.item import Episode, Movie, Season, Show
from program.media.state import States
from program.settings.manager import settings_manager


class MockSettings:
    def __init__(self, library_path):
        self.force_refresh = False
        self.symlink = type("symlink", (), {
            "library_path": Path(library_path),
            "separate_anime_dirs": True,
        })

@pytest.fixture
def symlink_library(fs):
    library_path = "/fake/library"
    fs.create_dir(f"{library_path}/movies")
    fs.create_dir(f"{library_path}/shows")
    fs.create_dir(f"{library_path}/anime_movies")
    fs.create_dir(f"{library_path}/anime_shows")
    settings_manager.settings = MockSettings(library_path)
    return SymlinkLibrary()


def test_valid_library_structure(symlink_library):
    assert symlink_library.initialized, "Library should be initialized successfully."


def test_invalid_library_structure(fs):
    incorrect_path = "/invalid/library"
    fs.create_dir(incorrect_path)
    settings_manager.settings = MockSettings(incorrect_path)
    library = SymlinkLibrary()
    assert not library.initialized, "Library should fail initialization with incorrect structure."


def test_movie_detection(symlink_library):
    with Patcher() as patcher:
        fs = patcher.fs
        movie_path = "/fake/library/movies"
        fs.create_file(f"{movie_path}/Top Gun (1986) tt0092099.mkv")
        fs.create_file(f"{movie_path}/The Matrix (1999) tt0133093.mkv")
        fs.create_file(f"{movie_path}/The Matrix Reloaded (2003) tt0234215.mkv")

        movies = list(symlink_library.run())
        assert len(movies) == 3, "Should detect 3 movies."
        assert all(isinstance(movie, Movie) for movie in movies), "Detected objects should be of type Movie."
        assert all(movie.state == States.Completed for movie in movies), "Detected objects should be in the Completed state."


def test_show_detection(symlink_library, fs):
    shows_path = "/fake/library/shows"
    fs.create_dir(f"{shows_path}/Vikings (2013) tt2306299/Season 01")
    fs.create_file(f"{shows_path}/Vikings (2013) tt2306299/Season 01/Vikings (2013) - s01e01 - Rites of Passage.mkv")
    fs.create_file(f"{shows_path}/Vikings (2013) tt2306299/Season 01/Vikings (2013) - s01e02 - Wrath of the Northmen.mkv")
    fs.create_dir(f"{shows_path}/The Mandalorian (2019) tt8111088/Season 01")
    fs.create_file(f"{shows_path}/The Mandalorian (2019) tt8111088/Season 01/The Mandalorian (2019) - s01e01 - Chapter 1.mkv")
    fs.create_file(f"{shows_path}/The Mandalorian (2019) tt8111088/Season 01/The Mandalorian (2019) - s01e02 - Chapter 2.mkv")

    shows = list(symlink_library.run())
    assert len(shows) == 2, "Should detect 2 shows."
    assert all(isinstance(show, Show) for show in shows), "Detected objects should be of type Show."
    assert all(season.state == States.Completed for show in shows for season in show.seasons), "Detected seasons should be in the Completed state."


def test_season_detection(symlink_library, fs):
    shows_path = "/fake/library/shows"
    fs.create_dir(f"{shows_path}/Vikings (2013) tt2306299/Season 01")
    fs.create_file(f"{shows_path}/Vikings (2013) tt2306299/Season 01/Vikings (2013) - s01e01 - Rites of Passage.mkv")

    shows = list(symlink_library.run())
    assert len(shows[0].seasons) == 1, "Should detect one season."
    assert all(isinstance(season, Season) for season in shows[0].seasons), "Detected objects should be of type Season."
    assert all(season.state == States.Completed for season in shows[0].seasons), "Detected objects should be in the Completed state."


def test_episode_detection(symlink_library, fs):
    shows_path = "/fake/library/shows"
    fs.create_dir(f"{shows_path}/Vikings (2013) tt2306299/Season 01")
    fs.create_file(f"{shows_path}/Vikings (2013) tt2306299/Season 01/Vikings (2013) - s01e01 - Rites of Passage.mkv")

    shows = list(symlink_library.run())
    assert len(shows[0].seasons[0].episodes) == 1, "Should detect one episode."
    assert all(isinstance(episode, Episode) for episode in shows[0].seasons[0].episodes), "Detected objects should be of type Episode."
    assert all(episode.state == States.Completed for episode in shows[0].seasons[0].episodes), "Detected objects should be in the Completed state."


def test_media_item_creation(symlink_library, fs):
    fs.create_file("/fake/library/movies/Top Gun (1986) tt0092099.mkv")
    items = list(symlink_library.run())
    assert len(items) == 1, "Should create one media item."
    assert items[0].ids["imdb_id"] == "tt0092099", "Media item should have the correct IMDb ID."
    assert isinstance(items[0], Movie), "The created item should be a Movie."
    assert items[0].state == States.Completed, "The created item should be in the Completed state."
