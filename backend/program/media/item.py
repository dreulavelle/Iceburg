from datetime import datetime
import threading
from program.media.state import (
    Unknown,
    Content,
    Scrape,
    Download,
    Symlink,
    Library,
    LibraryPartial,
)
from utils.utils import parser
from program.scrapers import Scraping
from program.realdebrid import Debrid
from program.symlink import Symlinker


class MediaItem:
    """MediaItem class"""

    def __init__(self, item):
        self._lock = threading.Lock()
        self.itemid = item_id.get_next_value()
        self.scraped_at = datetime(1970, 1, 1)
        self.scraped_times = 0
        self.active_stream = item.get("active_stream", None)
        self.streams = {}
        self.symlinked = False
        self.requested_at = item.get("requested_at", None) or datetime.now()
        self.requested_by = item.get("requested_by", None)
        self.file = None
        self.folder = None

        # Media related
        self.title = item.get("title", None)
        self.imdb_id = item.get("imdb_id", None)
        if self.imdb_id:
            self.imdb_link = f"https://www.imdb.com/title/{self.imdb_id}/"
        self.tvdb_id = item.get("tvdb_id", None)
        self.tmdb_id = item.get("tmdb_id", None)
        self.network = item.get("network", None)
        self.country = item.get("country", None)
        self.language = item.get("language", None)
        self.aired_at = item.get("aired_at", None)
        self.genres = item.get("genres", [])

        # Plex related
        self.key = item.get("key", None)
        self.guid = item.get("guid", None)
        self.update_folder = item.get("update_folder", None)
        self.state.set_context(self)

    def perform_action(self, modules):
        with self._lock:
            self.state.perform_action(modules)

    @property
    def state(self):
        _state = self._determine_state()
        _state.set_context(self)
        return _state

    def _determine_state(self):
        if self.key or self.update_folder == "updated":
            return Library()
        if self.symlinked:
            return Symlink()
        if self.file and self.folder:
            return Download()
        if len(self.streams) > 0:
            return Scrape()
        if self.title:
            return Content()
        return Unknown()

    def is_cached(self):
        if self.streams:
            return any(stream.get("cached", None) for stream in self.streams.values())
        return False

    def is_scraped(self):
        return len(self.streams) > 0

    def is_checked_for_availability(self):
        if self.streams:
            return all(
                stream.get("cached", None) is not None
                for stream in self.streams.values()
            )
        return False

    def to_dict(self):
        return {
            "item_id": self.itemid,
            "title": self.title,
            "type": self.type,
            "imdb_id": self.imdb_id if hasattr(self, "imdb_id") else None,
            "tvdb_id": self.tvdb_id if hasattr(self, "tvdb_id") else None,
            "tmdb_id": self.tmdb_id if hasattr(self, "tmdb_id") else None,
            "state": self.state.__class__.__name__,
            "imdb_link": self.imdb_link if hasattr(self, "imdb_link") else None,
            "aired_at": self.aired_at,
            "genres": self.genres if hasattr(self, "genres") else None,
            "guid": self.guid,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "scraped_at": self.scraped_at,
            "scraped_times": self.scraped_times,
        }

    def to_extended_dict(self):
        dict = self.to_dict()
        if self.type == "show":
            dict["seasons"] = [season.to_extended_dict() for season in self.seasons]
        if self.type == "season":
            dict["episodes"] = [episode.to_extended_dict() for episode in self.episodes]
        dict["language"] = (self.language if hasattr(self, "language") else None,)
        dict["country"] = (self.country if hasattr(self, "country") else None,)
        dict["network"] = (self.network if hasattr(self, "network") else None,)
        return dict

    def is_not_cached(self):
        return not self.is_cached()

    def __iter__(self):
        for attr, _ in vars(self).items():
            yield attr

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return self.imdb_id == other.imdb_id
        return False

    def get(self, key, default=None):
        """Get item attribute"""
        return getattr(self, key, default)

    def set(self, key, value):
        """Set item attribute"""
        _set_nested_attr(self, key, value)

class Movie(MediaItem):
    """Movie class"""

    def __init__(self, item):
        self.type = "movie"
        self.file = item.get("file", None)
        super().__init__(item)

    def __repr__(self):
        return f"Movie:{self.title}:{self.state.__class__.__name__}"
    
    @property
    def log_string(self):
        return self.title

class Show(MediaItem):
    """Show class"""

    def __init__(self, item):
        self.locations = item.get("locations", [])
        self.seasons = item.get("seasons", [])
        self.type = "show"
        super().__init__(item)

    def _determine_state(self):
        if all(season.state == Library for season in self.seasons):
            return Library()
        if any(
            season.state == Library or season.state == LibraryPartial
            for season in self.seasons
        ):
            return LibraryPartial()
        if any(season.state == Symlink for season in self.seasons):
            return Symlink()
        if any(season.state == Download for season in self.seasons):
            return Download()
        if any(season.state == Scrape for season in self.seasons):
            return Scrape()
        if any(season.state == Content for season in self.seasons):
            return Content()
        return Unknown()

    def __repr__(self):
        return f"Show:{self.title}:{self.state.__class__.__name__}"

    def add_season(self, season):
        """Add season to show"""
        self.seasons.append(season)
        season.parent = self
    
    @property
    def log_string(self):
        return self.title


class Season(MediaItem):
    """Season class"""

    def __init__(self, item):
        self.type = "season"
        self.parent = None
        self.number = item.get("number", None)
        self.episodes = item.get("episodes", [])
        super().__init__(item)

    def _determine_state(self):
        if len(self.episodes) > 0:
            if all(episode.state == Library for episode in self.episodes):
                return Library()
            if any(episode.state == Library for episode in self.episodes):
                return LibraryPartial()
            if all(episode.state == Symlink for episode in self.episodes):
                return Symlink()
            if all(episode.file and episode.folder for episode in self.episodes):
                return Download()
            if self.is_scraped() or any(
                episode.state == Scrape for episode in self.episodes
            ):
                return Scrape()
            if any(episode.state == Content for episode in self.episodes):
                return Content()
        return Unknown()

    def __eq__(self, other):
        return self.number == other.number

    def __repr__(self):
        return f"Season:{self.number}:{self.state.__class__.__name__}"

    def add_episode(self, episode):
        """Add episode to season"""
        self.episodes.append(episode)
        episode.parent = self

    @property
    def log_string(self):
        return self.parent.title + " S" + str(self.number).zfill(2)


class Episode(MediaItem):
    """Episode class"""

    def __init__(self, item):
        self.type = "episode"
        self.parent = None
        self.number = item.get("number", None)
        self.file = item.get("file", None)
        super().__init__(item)

    def __eq__(self, other):
        return self.number == other.number

    def __repr__(self):
        return f"Episode:{self.number}:{self.state.__class__.__name__}"

    def get_file_episodes(self):
        return parser.episodes(self.file)
    
    @property
    def log_string(self):
        return self.parent.parent.title + " S" + str(self.parent.number).zfill(2) + "E" + str(self.number).zfill(2)


def _set_nested_attr(obj, key, value):
    if "." in key:
        parts = key.split(".", 1)
        current_key, rest_of_keys = parts[0], parts[1]

        if not hasattr(obj, current_key):
            raise AttributeError(f"Object does not have the attribute '{current_key}'.")

        current_obj = getattr(obj, current_key)
        _set_nested_attr(current_obj, rest_of_keys, value)
    else:
        if isinstance(obj, dict):
            obj[key] = value
        else:
            setattr(obj, key, value)


class ItemId:
    value = 0

    @classmethod
    def get_next_value(cls):
        cls.value += 1
        return cls.value


item_id = ItemId()
