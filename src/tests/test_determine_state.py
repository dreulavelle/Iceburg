from RTN import Torrent
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from program.media import MediaItem, Movie, Show, Season, Episode, States
from program.media.stream import Stream

@pytest.fixture
def past_date():
    return datetime.now() - timedelta(days=30)

@pytest.fixture
def future_date():
    return datetime.now() + timedelta(days=30)

@pytest.fixture
def mock_stream():
    return Stream(Torrent(infohash="da32fba90633bd84a1e8aed909cda5e07f819023", raw_title="Mock Stream", data={
        "raw_title": "Mock Stream",
    }))

class TestMovieStateDetermination:
    def test_completed_state_with_key(self):
        movie = Movie({"type": "movie"})
        movie.key = "some_key"
        assert movie._determine_state() == States.Completed

    def test_completed_state_with_update_folder(self):
        movie = Movie({"type": "movie"})
        movie.update_folder = "updated"
        assert movie._determine_state() == States.Completed

    def test_symlinked_state(self):
        movie = Movie({"type": "movie"})
        movie.symlinked = True
        assert movie._determine_state() == States.Symlinked

    def test_downloaded_state(self):
        movie = Movie({"type": "movie"})
        movie.file = "path/to/file"
        movie.folder = "path/to/folder"
        assert movie._determine_state() == States.Downloaded

    def test_scraped_state(self, mock_stream):
        movie = Movie({"type": "movie"})
        movie.streams = [mock_stream]
        
        with patch.object(movie, 'is_scraped', return_value=True):
            assert movie._determine_state() == States.Scraped

    def test_indexed_state(self):
        movie = Movie({"type": "movie", "title": "Test Movie"})
        assert movie._determine_state() == States.Indexed

    def test_requested_state(self):
        movie = Movie({
            "type": "movie",
            "imdb_id": "tt1234567",
            "requested_by": "user"
        })
        assert movie._determine_state() == States.Requested

    def test_unknown_state(self):
        movie = Movie({"type": "movie"})
        assert movie._determine_state() == States.Unknown

class TestShowStateDetermination:
    @pytest.fixture
    def basic_show(self):
        return Show({"type": "show", "title": "Test Show"})

    def test_show_all_seasons_completed(self, basic_show):
        season1 = Season({"type": "season", "number": 1})
        season2 = Season({"type": "season", "number": 2})
        
        # Set up episodes
        for season in [season1, season2]:
            episode = Episode({"type": "episode", "number": 1})
            episode.parent = season
            episode.update_folder = "updated"
            episode.store_state(States.Completed)
            season.episodes = [episode]
            season.parent = basic_show
            season.store_state(States.Completed)
        
        basic_show.seasons = [season1, season2]
        assert basic_show._determine_state() == States.Completed

    def test_show_mixed_season_states(self, basic_show):
        season1 = Season({"type": "season", "number": 1})
        season2 = Season({"type": "season", "number": 2})
        
        # Season 1 completed
        episode1 = Episode({"type": "episode", "number": 1})
        episode1.parent = season1
        episode1.update_folder = "updated"
        episode1.store_state(States.Completed)
        season1.episodes = [episode1]
        season1.parent = basic_show
        season1.store_state(States.Completed)
        
        # Season 2 in progress
        episode2 = Episode({"type": "episode", "number": 1})
        episode2.parent = season2
        episode2.store_state(States.Downloaded)
        season2.episodes = [episode2]
        season2.parent = basic_show
        season2.store_state(States.Downloaded)
        
        basic_show.seasons = [season1, season2]
        assert basic_show._determine_state() == States.PartiallyCompleted

    def test_show_with_unreleased_season(self, basic_show, future_date):
        season = Season({"type": "season", "number": 1})
        episode = Episode({"type": "episode", "number": 1, "aired_at": future_date})
        episode.parent = season
        episode.store_state(States.Unreleased)
        season.episodes = [episode]
        season.parent = basic_show
        season.store_state(States.Unreleased)
        
        basic_show.seasons = [season]
        assert basic_show._determine_state() == States.Unreleased

    def test_show_ongoing_state(self, basic_show, past_date, future_date):
        season = Season({"type": "season", "number": 1})
        
        # One episode released, one unreleased
        episode1 = Episode({"type": "episode", "number": 1, "aired_at": past_date})
        episode2 = Episode({"type": "episode", "title": "Future Episode", "number": 2, "aired_at": future_date})
        
        episode1.parent = season
        episode2.parent = season
        episode1.update_folder = "updated"
        episode1.store_state(States.Completed)
        episode2.store_state(States.Unreleased)
        
        season.episodes = [episode1, episode2]
        season.parent = basic_show
        season.store_state(States.Ongoing)
        
        basic_show.seasons = [season]
        assert basic_show._determine_state() == States.Ongoing

    def test_show_empty_seasons(self, basic_show):
        basic_show.seasons = []
        assert basic_show._determine_state() == States.Unknown

class TestSeasonStateDetermination:
    @pytest.fixture
    def basic_season(self):
        return Season({"type": "season", "number": 1})

    def test_season_all_episodes_completed(self, basic_season):
        episode1 = Episode({"type": "episode", "number": 1})
        episode2 = Episode({"type": "episode", "number": 2})
        
        for episode in [episode1, episode2]:
            episode.parent = basic_season
            episode.update_folder = "updated"
            episode.store_state(States.Completed)
        
        basic_season.episodes = [episode1, episode2]
        assert basic_season._determine_state() == States.Completed

    def test_season_mixed_episode_states(self, basic_season):
        episode1 = Episode({"type": "episode", "number": 1})
        episode2 = Episode({"type": "episode", "number": 2})
        
        episode1.parent = basic_season
        episode2.parent = basic_season
        
        episode1.update_folder = "updated"
        episode1.store_state(States.Completed)
        episode2.store_state(States.Downloaded)
        
        basic_season.episodes = [episode1, episode2]
        assert basic_season._determine_state() == States.PartiallyCompleted

    def test_season_ongoing_state(self, basic_season, past_date, future_date):
        episode1 = Episode({"type": "episode", "number": 1, "aired_at": past_date})
        episode2 = Episode({"type": "episode", "title": "Future Episode", "number": 2, "aired_at": future_date})
        
        episode1.parent = basic_season
        episode2.parent = basic_season
        
        episode1.update_folder = "updated"
        episode1.store_state(States.Completed)
        episode2.store_state(States.Unreleased)
        
        basic_season.episodes = [episode1, episode2]
        assert basic_season._determine_state() == States.Ongoing

    def test_season_empty_episodes(self, basic_season):
        basic_season.episodes = []
        assert basic_season._determine_state() == States.Unreleased

    def test_season_all_episodes_unreleased(self, basic_season, future_date):
        episode1 = Episode({"type": "episode", "title": "Future Episode", "number": 1, "aired_at": future_date})
        episode2 = Episode({"type": "episode", "title": "Future Episode", "number": 2, "aired_at": future_date})
        
        for episode in [episode1, episode2]:
            episode.parent = basic_season
            episode.store_state(States.Unreleased)
        
        basic_season.episodes = [episode1, episode2]
        assert basic_season._determine_state() == States.Unreleased

class TestEpisodeStateDetermination:
    def test_completed_state_with_key(self):
        episode = Episode({"type": "episode", "number": 1})
        episode.key = "some_key"
        assert episode._determine_state() == States.Completed

    def test_completed_state_with_update_folder(self):
        episode = Episode({"type": "episode", "number": 1})
        episode.update_folder = "updated"
        assert episode._determine_state() == States.Completed

    def test_symlinked_state(self):
        episode = Episode({"type": "episode", "number": 1})
        episode.symlinked = True
        assert episode._determine_state() == States.Symlinked

    def test_downloaded_state(self):
        episode = Episode({"type": "episode", "number": 1})
        episode.file = "path/to/file"
        episode.folder = "path/to/folder"
        assert episode._determine_state() == States.Downloaded

    def test_scraped_state(self, mock_stream):
        episode = Episode({"type": "episode", "number": 1})
        episode.streams = [mock_stream]
        
        with patch.object(episode, 'is_scraped', return_value=True):
            assert episode._determine_state() == States.Scraped

class TestEdgeCases:
    def test_partial_download_state(self):
        """Test state when only file or only folder is set"""
        episode = Episode({"type": "episode", "number": 1})
        episode.file = "path/to/file"
        assert episode._determine_state() != States.Downloaded
        
        episode.file = None
        episode.folder = "path/to/folder"
        assert episode._determine_state() != States.Downloaded

    def test_state_priority_order(self):
        """Test that states are determined in the correct priority order"""
        episode = Episode({"type": "episode", "number": 1})
        episode.key = "some_key"  # Completed
        episode.symlinked = True  # Symlinked
        episode.file = "path/to/file"  # Downloaded
        episode.folder = "path/to/folder"
        
        # Should return Completed as it has highest priority
        assert episode._determine_state() == States.Completed

    def test_null_values_handling(self):
        """Test handling of null/None values"""
        episode = Episode({"type": "episode", "number": 1})
        episode.file = None
        episode.folder = None
        episode.symlinked = None
        episode.key = None
        episode.update_folder = None
        
        # Should handle null values gracefully
        assert episode._determine_state() == States.Unknown

    def test_empty_streams_handling(self, mock_stream):
        """Test handling of empty streams list"""
        episode = Episode({"type": "episode", "number": 1})
        episode.streams = []
        episode.blacklisted_streams = []
        
        with patch.object(episode, 'is_scraped', return_value=False):
            assert episode._determine_state() != States.Scraped

    def test_all_streams_blacklisted(self, mock_stream):
        """Test state when all streams are blacklisted"""
        episode = Episode({"type": "episode", "number": 1})
        episode.streams = [mock_stream]
        episode.blacklisted_streams = [mock_stream]
        
        with patch.object(episode, 'is_scraped', return_value=False):
            assert episode._determine_state() != States.Scraped

class TestStateTransitionEdgeCases:
    def test_show_state_with_empty_season(self):
        """Test show state determination with an empty season"""
        show = Show({"type": "show", "title": "Test Show"})
        season = Season({"type": "season", "number": 1})
        season.parent = show
        show.seasons = [season]
        assert show._determine_state() != States.Completed

    def test_season_state_with_mixed_episode_types(self, past_date, future_date):
        """Test season state with episodes in various states and release dates"""
        season = Season({"type": "season", "number": 1})
        
        episodes = [
            # Released, completed
            Episode({"type": "episode", "number": 1, "aired_at": past_date}),
            # Released, downloaded
            Episode({"type": "episode", "number": 2, "aired_at": past_date}),
            # Unreleased
            Episode({"type": "episode", "title": "Future Episode", "number": 3, "aired_at": future_date}),
            # Released, no state
            Episode({"type": "episode", "number": 4, "aired_at": past_date}),
        ]
        
        for episode in episodes:
            episode.parent = season
        
        episodes[0].update_folder = "updated"
        episodes[0].store_state(States.Completed)
        episodes[1].store_state(States.Downloaded)
        episodes[2].store_state(States.Unreleased)
        
        season.episodes = episodes
        assert season._determine_state() == States.Ongoing

    def test_complex_show_hierarchy_state(self, past_date, future_date):
        """Test state determination in a complex show hierarchy"""
        show = Show({"type": "show", "title": "Complex Show"})
        
        # Season 1: All completed
        season1 = Season({"type": "season", "number": 1})
        season1.parent = show
        ep1 = Episode({"type": "episode", "number": 1, "aired_at": past_date})
        ep1.parent = season1
        ep1.update_folder = "updated"
        ep1.store_state(States.Completed)
        season1.episodes = [ep1]
        
        # Season 2: Mixed states
        season2 = Season({"type": "season", "number": 2})
        season2.parent = show
        ep2_1 = Episode({"type": "episode", "number": 1, "aired_at": past_date})
        ep2_2 = Episode({"type": "episode", "title": "Future Episode", "number": 2, "aired_at": future_date})
        ep2_1.parent = season2
        ep2_2.parent = season2

        ep2_1.update_folder = "updated"
        ep2_1.store_state(States.Completed)
        ep2_2.store_state(States.Unreleased)
        season2.episodes = [ep2_1, ep2_2]
        
        show.seasons = [season1, season2]
        assert show._determine_state() == States.Ongoing