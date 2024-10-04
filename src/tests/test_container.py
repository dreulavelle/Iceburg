# from queue import Queue
# from unittest.mock import MagicMock
# 
# import pytest
# from program import Program
# from program.media.item import Episode, Movie, Season, Show, States
# 
# 
# @pytest.fixture
# def test_show():
#     # Setup Show with a Season and an Episode
#     show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     season = Season({"number": 1})
#     episode1 = Episode({"number": 1})
#     episode2 = Episode({"number": 2})
#     season.add_episode(episode1)
#     season.add_episode(episode2)
#     show.add_season(season)
#     return show
# 
# @pytest.fixture
# def test_movie():
#     return Movie({"imdb_id": "tt1375666", "requested_by": "user", "title": "Inception"})
# 
# @pytest.fixture
# def program():
#     args = MagicMock()
#     program = Program(args)
#     program.event_queue = Queue()  # Reset the event queue
#     program.media_items = MediaItemContainer()
#     return program
# 
# def test_incomplete_items_retrieval(program, test_show):
#     program.media_items.upsert(test_show)
#     incomplete_items = program.media_items.get_incomplete_items()
#     assert len(incomplete_items) == len(program.media_items)
#     assert incomplete_items[next(iter(incomplete_items))].state == States.Unknown
# 
# def test_upsert_show_with_season_and_episodes():
#     container = MediaItemContainer()
#     show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     season = Season({"number": 1})
#     episode1 = Episode({"number": 1})
#     episode2 = Episode({"number": 2})
#     season.add_episode(episode1)
#     season.add_episode(episode2)
#     show.add_season(season)
# 
#     container.upsert(show)
# 
#     assert len(container._shows) == 1
#     assert len(container._seasons) == 1
#     assert len(container._episodes) == 2
#     assert len(container._items) == 4
# 
# def test_remove_show_with_season_and_episodes():
#     container = MediaItemContainer()
#     show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     season = Season({"number": 1})
#     episode1 = Episode({"number": 1})
#     episode2 = Episode({"number": 2})
#     season.add_episode(episode1)
#     season.add_episode(episode2)
#     show.add_season(season)
# 
#     container.upsert(show)
#     container.remove(show)
# 
#     assert len(container._shows) == 1
#     assert len(container._seasons) == 1
#     assert len(container._episodes) == 2
#     assert len(container._items) == 1
# 
# def test_merge_items():
#     container = MediaItemContainer()
#     show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     season = Season({"number": 1})
#     episode1 = Episode({"number": 1})
#     episode2 = Episode({"number": 2})
#     season.add_episode(episode1)
#     season.add_episode(episode2)
#     show.add_season(season)
#     container.upsert(show)
# 
#     new_show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     new_season = Season({"number": 1})
#     new_episode = Episode({"number": 3})
#     new_season.add_episode(new_episode)
#     new_show.add_season(new_season)
#     container.upsert(new_show)
# 
#     assert len(container._items) == 5, "Items should be merged"
#     assert len(container._shows) == 1, "Shows should be merged"
#     assert len(container._seasons) == 1, "Seasons should be merged"
# 
# def test_upsert_movie():
#     container = MediaItemContainer()
#     movie = Movie({"imdb_id": "tt1375666", "requested_by": "user", "title": "Inception"})
#     container.upsert(movie)
# 
#     assert len(container._movies) == 1
#     assert len(container._items) == 1
# 
# def test_save_and_load_container(tmpdir):
#     container = MediaItemContainer()
#     show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     season = Season({"number": 1})
#     episode1 = Episode({"number": 1})
#     episode2 = Episode({"number": 2})
#     season.add_episode(episode1)
#     season.add_episode(episode2)
#     show.add_season(season)
#     container.upsert(show)
# 
#     filepath = tmpdir.join("container.pkl")
#     container.save(str(filepath))
# 
#     new_container = MediaItemContainer()
#     new_container.load(str(filepath))
# 
#     assert len(new_container._shows) == 1
#     assert len(new_container._seasons) == 1
#     assert len(new_container._episodes) == 2
#     assert len(new_container._items) == 4
# 
# def test_get_missing_items():
#     container = MediaItemContainer()
#     show = Show({"imdb_id": "tt1405406", "requested_by": "user", "title": "Test Show"})
#     season = Season({"number": 1})
#     episode1 = Episode({"number": 1})
#     episode2 = Episode({"number": 2})
#     season.add_episode(episode1)
#     season.add_episode(episode2)
#     show.add_season(season)
#     container.upsert(show)
# 
#     missing_items = container.get_incomplete_items()
#     
#     assert len(missing_items) == 4
#     assert missing_items[next(iter(missing_items))].state == States.Unknown
#     assert missing_items[next(iter(missing_items))].ids["imdb_id"] == "tt1405406"
#     assert missing_items[next(iter(missing_items))].title == "Test Show"