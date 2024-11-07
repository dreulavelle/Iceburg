import sys
import pytest
from datetime import datetime, timedelta
from program.media import MediaItem, Movie, Show, Season, Episode, States
from program.types import Service, ProcessedEvent
from program.services.downloaders import Downloader
from program.services.indexers.trakt import TraktIndexer
from program.services.post_processing import PostProcessing
from program.services.scrapers import Scraping
from program.services.updaters import Updater
from program.state_transition import process_event
from program.symlink import Symlinker
from program.settings.manager import settings_manager
from unittest.mock import Mock, patch
from sqlalchemy.exc import SQLAlchemyError


@pytest.fixture
def mock_settings():
    settings_manager.settings.post_processing.subliminal.enabled = True
    yield settings_manager.settings

@pytest.fixture
def reverted_settings():
    settings_manager.settings.post_processing.subliminal.enabled = False
    yield settings_manager.settings

@pytest.fixture
def future_date():
    return datetime.now() + timedelta(days=30)

@pytest.fixture
def past_date():
    return datetime.now() - timedelta(days=30)

@pytest.fixture
def movie():
    return Movie({
        "type": "movie",
        "title": "Test Movie",
        "imdb_id": "tt1234567",
        "trakt_id": 1234567,
        "requested_by": "test_user",
        "aired_at": past_date
    })

@pytest.fixture
def unreleased_movie(future_date):
    return Movie({
        "type": "movie",
        "title": "Future Movie",
        "imdb_id": "tt7654321",
        "trakt_id": 7654321,
        "requested_by": "test_user",
        "aired_at": future_date
    })

@pytest.fixture
def show():
    return Show({
        "type": "show",
        "title": "Test Show",
        "imdb_id": "tt2345678",
        "trakt_id": 2345678,
        "requested_by": "test_user",
        "aired_at": past_date
    })

@pytest.fixture
def season(show):
    season = Season({
        "type": "season",
        "number": 1,
        "aired_at": past_date,
        "trakt_id": 3456789
    })
    season.parent = show
    show.seasons.append(season)
    return season

@pytest.fixture
def episode(season):
    episode = Episode({
        "type": "episode",
        "number": 1,
        "aired_at": past_date,
        "trakt_id": 4567890
    })
    episode.parent = season
    season.episodes.append(episode)
    return episode

class TestStateTransitions:
    def test_requested_to_indexed(self, movie):
        """Test transition from Requested to Indexed state"""
        movie.store_state(States.Requested)
        next_service, items = process_event(None, movie)
        
        assert next_service == TraktIndexer
        assert len(items) == 1
        assert items[0] == movie

    def test_indexed_to_scraped(self, movie):
        """Test transition from Indexed to Scraped state"""
        movie.store_state(States.Indexed)
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, movie)
            
            assert next_service == Scraping
            assert len(items) == 1
            assert items[0] == movie

    def test_scraped_to_downloaded(self, movie):
        """Test transition from Scraped to Downloaded state"""
        movie.store_state(States.Scraped)
        next_service, items = process_event(None, movie)
        
        assert next_service == Downloader
        assert len(items) == 1
        assert items[0] == movie

    def test_downloaded_to_symlinked(self, movie):
        """Test transition from Downloaded to Symlinked state"""
        movie.store_state(States.Downloaded)
        next_service, items = process_event(None, movie)
        
        assert next_service == Symlinker
        assert len(items) == 1
        assert items[0] == movie

    def test_symlinked_to_completed(self, movie):
        """Test transition from Symlinked to Completed state"""
        movie.store_state(States.Symlinked)
        next_service, items = process_event(None, movie)
        
        assert next_service == Updater
        assert len(items) == 1
        assert items[0] == movie

    def test_completed_to_post_processing(self, movie, mock_settings):
        """Test transition from Completed to Post Processing state"""
        movie.store_state(States.Completed)

        with patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
            next_service, items = process_event(None, movie)
            
            assert next_service == PostProcessing
            assert len(items) == 1
            assert items[0] == movie

    def test_completed_to_nothing(self, movie, reverted_settings):
        """Test transition from Completed to Post Processing state"""
        movie.store_state(States.Completed)

        with patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
            next_service, items = process_event(None, movie)
            
            assert next_service == None

    def test_unreleased_movie_processing(self, unreleased_movie):
        """Test that unreleased movies are handled correctly"""
        unreleased_movie.store_state(States.Unreleased)
        next_service, items = process_event(None, unreleased_movie)
        
        assert next_service is None
        assert len(items) == 0

    def test_show_partial_completion(self, show, season, episode):
        """Test handling of partially completed shows"""
        show.store_state(States.PartiallyCompleted)
        season.store_state(States.PartiallyCompleted)
        episode.store_state(States.Indexed)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, show)
            
            assert len(items) > 0
            # Should process non-completed episodes
            assert episode in items

    def test_season_partial_completion(self, season, episode):
        """Test handling of partially completed seasons"""
        season.store_state(States.PartiallyCompleted)
        episode.store_state(States.Indexed)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, season)
            
            assert len(items) > 0
            assert episode in items

    def test_show_all_episodes_completed(self, show, season, episode, mock_settings):
        """Test handling of shows where all episodes are completed"""
        show.store_state(States.Completed)
        season.store_state(States.Completed)
        episode.store_state(States.Completed)
        
        with patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
            next_service, items = process_event(None, show)
            
            assert next_service == PostProcessing
            assert len(items) == 1
            assert episode in items

    def test_failed_state_handling(self, movie):
        """Test handling of items in Failed state"""
        movie.store_state(States.Failed)
        next_service, items = process_event(None, movie)
        
        assert next_service is None
        assert len(items) == 0

    def test_ongoing_show_processing(self, show, season, episode):
        """Test processing of ongoing shows"""
        show.store_state(States.Ongoing)
        season.store_state(States.PartiallyCompleted)
        episode.store_state(States.Indexed)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, show)
            
            assert len(items) > 0
            assert episode in items

    def test_retry_completed_item(self, movie, mock_settings):
        """Test handling of manually retried completed items"""
        movie.store_state(States.Completed)
        
        with patch('program.services.post_processing.notify') as mock_notify, \
             patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
                next_service, items = process_event("RetryItem", movie)
                
                assert next_service == PostProcessing
                mock_notify.assert_not_called()

    def test_multiple_post_processing_prevention(self, movie):
        """Test prevention of multiple post-processing runs"""
        movie.store_state(States.Completed)
        
        next_service, items = process_event(PostProcessing, movie)
        
        assert next_service is None
        assert len(items) == 0

    def test_show_with_mixed_episode_states(self, show, season):
        """Test handling of shows with episodes in different states"""
        show.store_state(States.PartiallyCompleted)
        season.store_state(States.PartiallyCompleted)
        
        # Create episodes in different states
        episode1 = Episode({"type": "episode", "number": 1, "aired_at": past_date, "trakt_id": 1234567})
        episode2 = Episode({"type": "episode", "number": 2, "aired_at": past_date, "trakt_id": 2345678})
        episode3 = Episode({"type": "episode", "number": 3, "aired_at": future_date, "trakt_id": 3456789})
        
        episode1.parent = season
        episode2.parent = season
        episode3.parent = season
        
        season.episodes = [episode1, episode2, episode3]
        
        episode1.store_state(States.Completed)
        episode2.store_state(States.Indexed)
        episode3.store_state(States.Unreleased)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, show)
            
            assert len(items) == 1
            assert episode2 in items
            assert episode1 not in items
            assert episode3 not in items

    @pytest.mark.parametrize("current_state,expected_service", [
        (States.Requested, TraktIndexer),
        (States.Indexed, Scraping),
        (States.Scraped, Downloader),
        (States.Downloaded, Symlinker),
        (States.Symlinked, Updater),
        (States.Completed, PostProcessing),
        (States.Failed, None),
        (States.Unknown, None)
    ])
    def test_state_transitions_parametrized(self, movie, current_state, expected_service, mock_settings):
        """Test all possible state transitions using parametrization"""
        movie.store_state(current_state)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True), \
             patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
            next_service, items = process_event(None, movie)
            
            assert next_service == expected_service
            if expected_service:
                assert len(items) == 1
                assert items[0] == movie
            else:
                assert len(items) == 0

    def test_post_processing_disabled(self, movie):
        """Test behavior when post-processing is disabled"""
        movie.store_state(States.Completed)
        
        with patch('program.settings.manager.settings_manager.settings.post_processing.subliminal.enabled', False):
            next_service, items = process_event(None, movie)
            
            assert next_service is None
            assert len(items) == 0

    def test_season_with_mixed_release_dates(self, season):
        """Test handling of seasons with mixed release dates"""
        season.store_state(States.PartiallyCompleted)
        
        past = datetime.now() - timedelta(days=30)
        future = datetime.now() + timedelta(days=30)
        
        episode1 = Episode({"type": "episode", "number": 1, "aired_at": past, "trakt_id": 1234567})
        episode2 = Episode({"type": "episode", "number": 2, "aired_at": future, "trakt_id": 2345678})
        
        episode1.parent = season
        episode2.parent = season
        season.episodes = [episode1, episode2]
        
        episode1.store_state(States.Indexed)
        episode2.store_state(States.Unreleased)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, season)
            
            assert len(items) == 1
            assert episode1 in items
            assert episode2 not in items

@pytest.fixture
def complex_show(past_date, future_date):
    """Create a show with multiple seasons and episodes in various states"""
    show = Show({
        "type": "show",
        "title": "Complex Show",
        "imdb_id": "tt9876543",
        "requested_by": "test_user",
        "aired_at": past_date
    })

    show.store_state(States.PartiallyCompleted)
    
    # Season 1 - Completed
    season1 = Season({"type": "season", "number": 1, "aired_at": past_date, "trakt_id": 1234567})
    season1.parent = show
    season1.store_state(States.Completed)
    for i in range(1, 4):
        episode = Episode({"type": "episode", "number": i, "aired_at": past_date, "trakt_id": 2345678 + i})
        episode.parent = season1
        episode.store_state(States.Completed)
        season1.episodes.append(episode)
    
    # Season 2 - Partially Complete
    season2 = Season({"type": "season", "number": 2, "aired_at": past_date, "trakt_id": 3456789})
    season2.parent = show
    season2.store_state(States.PartiallyCompleted)
    for i in range(1, 4):
        episode = Episode({"type": "episode", "number": i, "aired_at": past_date, "trakt_id": 4567890 + i})
        episode.parent = season2
        episode.store_state(States.Completed if i < 3 else States.Downloaded)
        season2.episodes.append(episode)
    
    # Season 3 - Mixed Released/Unreleased
    season3 = Season({"type": "season", "number": 3, "trakt_id": 5678901})
    season3.parent = show
    season3.store_state(States.Ongoing)
    for i in range(1, 4):
        episode = Episode({
            "type": "episode", 
            "number": i, 
            "aired_at": past_date if i < 3 else future_date,
            "trakt_id": 6789012 + i
        })
        episode.parent = season3
        episode.store_state(States.Indexed if i < 3 else States.Unreleased)
        season3.episodes.append(episode)
    
    # Season 4 - Unreleased
    season4 = Season({"type": "season", "number": 4, "aired_at": future_date, "trakt_id": 7890123})
    season4.parent = show
    season4.store_state(States.Unreleased)
    for i in range(1, 4):
        episode = Episode({"type": "episode", "number": i, "aired_at": future_date, "trakt_id": 8901234 + i})
        episode.parent = season4
        episode.store_state(States.Unreleased)
        season4.episodes.append(episode)
    
    show.seasons.extend([season1, season2, season3, season4])
    
    return show

class TestStateTransitionsEdgeCases:
    def test_empty_show(self):
        """Test handling of a show with no seasons"""
        show = Show({
            "type": "show",
            "title": "Empty Show",
            "imdb_id": "tt0000000",
            "requested_by": "test_user",
            "trakt_id": 1234567,
        })
        show.store_state(States.Requested)
        
        next_service, items = process_event(None, show)
        assert next_service == TraktIndexer
        assert len(items) == 1

    def test_empty_season(self, show):
        """Test handling of a season with no episodes"""
        empty_season = Season({
            "type": "season",
            "number": 99,
            "aired_at": datetime.now(),
            "trakt_id": 2345678
        })
        empty_season.parent = show
        empty_season.store_state(States.Indexed)
        
        next_service, items = process_event(None, empty_season)
        assert next_service == Scraping
        assert len(items) == 0

    def test_duplicate_episode_numbers(self, season):
        """Test handling of duplicate episode numbers in a season"""
        episode1 = Episode({"type": "episode", "number": 1, "aired_at": datetime.now(), "trakt_id": 3456789})
        episode2 = Episode({"type": "episode", "number": 1, "aired_at": datetime.now(), "trakt_id": 4567890})
        
        episode1.parent = season
        episode2.parent = season
        season.episodes = [episode1, episode2]
        
        season.store_state(States.PartiallyCompleted)
        episode1.store_state(States.Indexed)
        episode2.store_state(States.Indexed)

        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, season)
            assert len(items) == 2

    def test_missing_parent_references(self):
        """Test handling of episodes and seasons with missing parent references"""
        episode = Episode({
            "type": "episode",
            "number": 1,
            "aired_at": datetime.now(),
            "trakt_id": 5678901
        })
        episode.store_state(States.Indexed)
        
        next_service, items = process_event(None, episode)
        assert next_service == Scraping
        assert len(items) == 1

    def test_invalid_state_transitions(self, movie):
        """Test handling of invalid state transitions"""
        movie.store_state(States.Downloaded)
        movie.symlinked = True  # Inconsistent state
        
        next_service, items = process_event(None, movie)
        assert next_service == Symlinker  # Should still try to proceed normally
        assert len(items) == 1

    def test_circular_processing_prevention(self, movie):
        """Test prevention of circular processing"""
        movie.store_state(States.Completed)
        movie.scraped_times = 100  # Excessive processing attempts
        
        with patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
            next_service, items = process_event(None, movie)
            assert next_service == PostProcessing
            assert len(items) == 1

class TestComplexShowScenarios:
    def test_mixed_state_processing(self, complex_show):
        """Test processing of a show with episodes in various states"""
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, complex_show)
            
            # Should only process released, non-completed episodes
            assert len(items) > 0
            assert all(item.is_released for item in items)
            assert all(item.state != States.Completed for item in items)

    def test_season_state_propagation(self, complex_show, mock_settings):
        """Test state propagation through season completion"""
        season = complex_show.seasons[1]  # Season 2 (Partially Complete)
        episode = season.episodes[2]  # Last incomplete episode
        
        # Complete the last episode
        season.store_state(States.Completed)
        episode.store_state(States.Completed)
        
        with patch('program.services.post_processing.subliminal.Subliminal.should_submit', return_value=True):
            next_service, items = process_event("RetryItem", season)
            assert next_service == PostProcessing
            assert all(item.last_state == States.Completed for item in items)

    def test_mixed_release_dates(self, complex_show):
        """Test processing of seasons with mixed release dates"""
        season = complex_show.seasons[2]  # Season 3 (Mixed Released/Unreleased)
        
        with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
            next_service, items = process_event(None, season)
            assert len(items) == 2  # Should only process released episodes
            assert all(item.is_released for item in items)

#     def test_batch_release_handling(self, complex_show):
#         """Test handling of batch episode releases"""
#         season = complex_show.seasons[3]  # Season 4 (Unreleased)
#         season.store_state(States.Indexed)

#         # Simulate batch release
#         release_time = datetime.now() - timedelta(hours=1)
#         for episode in season.episodes:
#             episode.aired_at = release_time
        
#         with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
#             next_service, items = process_event(None, season)
#             assert len(items) == 3  # Should process all episodes
#             assert all(item.is_released for item in items)

# class TestErrorConditions:
#     def test_database_error_handling(self, movie):
#         """Test handling of database errors during state transitions"""
#         movie.store_state(States.Indexed)
        
#         with patch('program.services.scrapers.Scraping.can_we_scrape', side_effect=SQLAlchemyError):
#             next_service, items = process_event(None, movie)
#             assert next_service == Scraping  # Should still return next service
#             assert len(items) == 1  # Should include item for retry

#     def test_invalid_date_handling(self, movie):
#         """Test handling of invalid dates"""
#         movie.aired_at = "invalid_date"  # Invalid date format
#         movie.store_state(States.Indexed)
        
#         next_service, items = process_event(None, movie)
#         assert next_service == Scraping
#         assert len(items) == 1

#     def test_concurrent_modification(self, complex_show):
#         """Test handling of concurrent modifications"""
#         season = complex_show.seasons[1]
        
#         # Simulate concurrent modification
#         with patch('sqlalchemy.orm.Session.refresh', side_effect=SQLAlchemyError):
#             next_service, items = process_event(None, season)
#             assert next_service is not None  # Should handle error gracefully
            
#     def test_missing_required_attributes(self, movie):
#         """Test handling of missing required attributes"""
#         delattr(movie, 'type')  # Remove required attribute
        
#         next_service, items = process_event(None, movie)
#         assert next_service is None
#         assert len(items) == 0

#     def test_invalid_state_value(self, movie):
#         """Test handling of invalid state values"""
#         movie.last_state = "InvalidState"  # Set invalid state
        
#         next_service, items = process_event(None, movie)
#         assert next_service is None
#         assert len(items) == 0

#     @pytest.mark.parametrize("error_condition", [
#         SQLAlchemyError,
#         ValueError,
#         AttributeError,
#         TypeError
#     ])
#     def test_various_error_types(self, movie, error_condition):
#         """Test handling of various types of errors"""
#         movie.store_state(States.Indexed)
        
#         with patch('program.services.scrapers.Scraping.can_we_scrape', side_effect=error_condition):
#             next_service, items = process_event(None, movie)
#             assert next_service == Scraping  # Should still return next service
#             assert len(items) == 1  # Should include item for retry

# class TestRecoveryScenarios:
#     def test_recovery_from_failed_state(self, movie):
#         """Test recovery from failed state"""
#         movie.store_state(States.Failed)
#         movie.scraped_times = 0  # Reset retry counter
        
#         next_service, items = process_event("RetryItem", movie)
#         assert next_service is not None
#         assert len(items) == 1

#     def test_partial_completion_recovery(self, complex_show):
#         """Test recovery from partial completion"""
#         season = complex_show.seasons[1]  # Partially completed season
        
#         for episode in season.episodes:
#             episode.store_state(States.Failed)
        
#         with patch('program.services.scrapers.Scraping.can_we_scrape', return_value=True):
#             next_service, items = process_event("RetryItem", season)
#             assert next_service is not None
#             assert len(items) > 0

#     def test_interrupted_processing_recovery(self, movie):
#         """Test recovery from interrupted processing"""
#         movie.store_state(States.Downloaded)
#         movie.folder = None  # Simulate interrupted download
        
#         next_service, items = process_event(None, movie)
#         assert next_service == Symlinker
#         assert len(items) == 1