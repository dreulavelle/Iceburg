import pytest

from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.media.state import States
from program.media.stream import Stream

from program.program import Program
from program.services.downloaders.realdebrid import RealDebridDownloader
from program.services.indexers.trakt import TraktIndexer
from program.services.scrapers import Scraping
from program.services.updaters.plex import PlexUpdater
from program.state_transition import process_event
from program.symlink import Symlinker


@pytest.fixture
def movie():
    return Movie({"imdb_id": "tt1375666", "requested_by": "Iceberg"})

@pytest.fixture
def show():
    show = Show({"imdb_id": "tt0903747", "requested_by": "Iceberg"})
    season = Season({"number": 1})
    episode = Episode({"number": 1})
    season.add_episode(episode)
    show.add_season(season)
    return show

@pytest.fixture
def media_item_movie():
    return MediaItem({"imdb_id": "tt1375666", "requested_by": "Iceberg"})

@pytest.fixture
def media_item_show():
    show = MediaItem({"imdb_id": "tt0903747", "requested_by": "Iceberg"})
    season = MediaItem({"number": 1})
    episode = MediaItem({"number": 1})
    season.add_episode(episode)
    show.add_season(season)
    return show

@pytest.fixture
def season(show):
    return show.seasons[0]

@pytest.fixture
def episode(season):
    return season.episodes[0]

def test_initial_state(movie, show, season, episode):
    """Test that items start in the Unknown state."""
    # Given: A new media item (movie, episode, season, show)
    # When: The item is first created
    # Then: The item's state should be Unknown

    # As long as we initialize Movies with an imdb_id and requested_by,
    # it should end up as Requested. 
    assert movie.state == States.Requested, "Movie should start in Requested state"
    
    # Show, Season and Episode are Unknown until they are added to a Show.
    assert show.state == States.Unknown, "Show should start in Unknown state"
    assert season.state == States.Unknown, "Season should start in Unknown state"
    assert episode.state == States.Unknown, "Episode should start in Unknown state"

def test_requested_state(movie):
    """Test transition to the Requested state."""
    # Given: A media item (movie)
    movie.set("requested_by", "user")
    # When: The item is requested by a user
    # Then: The item's state should be Requested
    assert movie.state == States.Requested, "Movie should transition to Requested state"

def test_indexed_state(movie):
    """Test transition to the Indexed state."""
    # Given: A media item (movie)
    movie.set("title", "Inception")
    # When: The item has a title set
    # Then: The item's state should be Indexed
    assert movie.state == States.Indexed, "Movie should transition to Indexed state"

# def test_scraped_state(episode):
#     """Test transition to the Scraped state."""
#     # Given: A media item (episode)
#     episode.set("streams", [Stream()])
#     # When: The item has streams available
#     # Then: The item's state should be Scraped
#     assert episode.state == States.Scraped, "Episode should transition to Scraped state"

def test_downloaded_state(episode):
    """Test transition to the Downloaded state."""
    # Given: A media item (episode)
    episode.set("file", "/path/to/file")
    episode.set("folder", "/path/to/folder")
    # When: The item has file and folder set
    # Then: The item's state should be Downloaded
    assert episode.state == States.Downloaded, "Episode should transition to Downloaded state"

def test_symlinked_state(episode):
    """Test transition to the Symlinked state."""
    # Given: A media item (episode)
    episode.set("symlinked", True)
    # When: The item is symlinked
    # Then: The item's state should be Symlinked
    assert episode.state == States.Symlinked, "Episode should transition to Symlinked state"

def test_completed_state(movie):
    """Test transition to the Completed state."""
    # Given: A media item (movie)
    movie.set("key", "some_key")
    movie.set("update_folder", "updated")
    # When: The item has a key and update_folder set
    # Then: The item's state should be Completed
    assert movie.state == States.Completed, "Movie should transition to Completed state"

def test_show_state_transitions(show):
    """Test full state transitions of a show."""
    # Given: A media item (show)
    # When: The show has various states set for its episodes and seasons
    show.seasons[0].episodes[0].set("file", "/path/to/file")
    show.seasons[0].episodes[0].set("folder", "/path/to/folder")
    show.seasons[0].episodes[0].set("symlinked", True)
    show.seasons[0].episodes[0].set("key", "some_key")
    show.seasons[0].episodes[0].set("update_folder", "updated")

    # Then: The show's state should transition based on its episodes and seasons
    assert show.state == States.Completed, "Show should transition to Completed state"

@pytest.mark.parametrize("state, service, next_service", [
    (States.Unknown, Program, TraktIndexer),
    # (States.Requested, TraktIndexer, TraktIndexer),
    (States.Indexed, TraktIndexer, Scraping),
    (States.Scraped, Scraping, RealDebridDownloader),
    (States.Downloaded, RealDebridDownloader, Symlinker),
    (States.Symlinked, Symlinker, PlexUpdater),
    (States.Completed, PlexUpdater, None)
])
def test_process_event_transitions_movie(state, service, next_service, movie):
    """Test processing events for state transitions."""
    # Given: A media item (movie) and a service
    movie._determine_state = lambda: state  # Manually override the state

    # When: The event is processed
    next_service_result, items_to_submit = process_event(None, service, movie)

    # Then: The next service should be as expected based on the current service
    if next_service is None:
        assert next_service_result is None, f"Next service should be None for {service}"
    else:
        assert next_service_result == next_service, f"Next service should be {next_service} for {service}"


@pytest.mark.parametrize("state, service, next_service", [
    (States.Unknown, Program, TraktIndexer),
    # (States.Requested, TraktIndexer, TraktIndexer),
    (States.Indexed, TraktIndexer, Scraping),
    (States.Scraped, Scraping, RealDebridDownloader),
    (States.Downloaded, RealDebridDownloader, Symlinker),
    (States.Symlinked, Symlinker, PlexUpdater),
    (States.Completed, PlexUpdater, None)
])
def test_process_event_transition_shows(state, service, next_service, show):
    """Test processing events for state transitions with shows."""
    # Given: A media item (show) and a service
    show._determine_state = lambda: state  # Manually override the state

    # Ensure the show has seasons and episodes
    if not hasattr(show, "seasons"):
        show.seasons = []
    for season in show.seasons:
        if not hasattr(season, "episodes"):
            season.episodes = []

    # When: The event is processed
    updated_item, next_service_result, items_to_submit = process_event(None, service, show)

    # Then: The next service should be as expected based on the current service
    if next_service is None:
        assert next_service_result is None, f"Next service should be None for {service}"
    else:
        assert next_service_result == next_service, f"Next service should be {next_service} for {service}"

# test media item movie
@pytest.mark.parametrize("state, service, next_service", [
    (States.Unknown, Program, TraktIndexer),
    # (States.Requested, TraktIndexer, TraktIndexer),
    (States.Indexed, TraktIndexer, Scraping),
    (States.Scraped, Scraping, RealDebridDownloader),
    (States.Downloaded, RealDebridDownloader, Symlinker),
    (States.Symlinked, Symlinker, PlexUpdater),
    (States.Completed, PlexUpdater, None)
])
def test_process_event_transitions_media_item_movie(state, service, next_service, media_item_movie):
    """Test processing events for state transitions."""
    # Given: A media item (movie) and a service
    media_item_movie._determine_state = lambda: state

    # When: The event is processed
    updated_item, next_service_result, items_to_submit = process_event(None, service, media_item_movie)

    # Then: The next service should be as expected based on the current service
    if next_service is None:
        assert next_service_result is None, f"Next service should be None for {service}"
    else:
        assert next_service_result == next_service, f"Next service should be {next_service} for {service}"

# test media item show
# @pytest.mark.parametrize("state, service, next_service", [
#     (States.Unknown, Program, TraktIndexer),
#     # (States.Requested, TraktIndexer, TraktIndexer),
#     (States.Indexed, TraktIndexer, Scraping),
#     (States.Scraped, Scraping, Debrid),
#     (States.Downloaded, Debrid, Symlinker),
#     (States.Symlinked, Symlinker, PlexUpdater),
#     (States.Completed, PlexUpdater, None)
# ])
# def test_process_event_transitions_media_item_show(state, service, next_service, media_item_show):
#     """Test processing events for state transitions."""
#     # Given: A media item (movie) and a service
#     media_item_show._determine_state = lambda: state

#     # When: The event is processed
#     updated_item, next_service_result, items_to_submit = process_event(None, service, media_item_show)

#     if next_service is Scraping:
#         assert isinstance(updated_item, Show), "Updated item should be of type Show"

#     # Then: The next service should be as expected based on the current service
#     if next_service is None:
#         assert next_service_result is None, f"Next service should be None for {service}"
#     else:
#         assert next_service_result == next_service, f"Next service should be {next_service} for {service}"