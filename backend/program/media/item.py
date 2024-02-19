import threading
from datetime import datetime
from dataclasses import dataclass
from program.media.state import States
from typing import Self, Optional
from utils.parser import parser
from pydantic import BaseModel


@dataclass(frozen=True)
class ItemId:
    parent_id: Optional[Self]
    value: str

    def __repr__(self):
        if not self.parent_id:
            return self.value
        return f"{self.parent_id}/{self.value}"

    def __hash__(self):
        return hash(self.__repr__())
    

class MediaItem:
    """MediaItem class"""

    def __init__(self, item):
        self.scraped_at = datetime(1970, 1, 1)
        self.scraped_times = 0
        self.active_stream = item.get("active_stream", None)
        self.streams = {}
        self.symlinked = False
        self.requested_at = item.get("requested_at", None) or datetime.now()
        self.requested_by = item.get("requested_by", None)
        self.file = None
        self.folder = None
        self.is_anime = item.get("is_anime", False)
        self.parsed_data = item.get("parsed_data", [])
        # Maintained by the MediaItemContainer
        self.parent = None

        # Media related
        self.title = item.get("title", None)
        self.imdb_id = item.get("imdb_id", None)
        if self.imdb_id:
            self.imdb_link = f"https://www.imdb.com/title/{self.imdb_id}/"
            if not hasattr(self, 'item_id'):
                self.item_id = ItemId(None, self.imdb_id)
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

    @property
    def state(self):
        return self._determine_state()

    def _determine_state(self):
        if self.key or self.update_folder == "updated":
            return States.Library
        elif self.symlinked:
            return States.Symlink
        elif self.file and self.folder:
            return States.Download
        elif self.is_scraped() and self.is_checked_for_availability():
            if any(stream.get('cached') for stream in self.streams.values()):
                return States.Scrape
            else:
                return States.Content
        elif self.title:
            return States.Content
        else:
            return States.Unknown

    def is_scraped(self):
        return len(self.streams) > 0

    def is_checked_for_availability(self):
        """Check if item has been checked for availability."""
        if self.streams:
            return all(
                stream.get("cached", None) is not None
                for stream in self.streams.values()
            )
        return False

    def to_dict(self):
        """Convert item to dictionary (API response)"""
        return {
            "item_id": self.item_id,
            "title": self.title,
            "type": self.type,
            "imdb_id": self.imdb_id if hasattr(self, "imdb_id") else None,
            "tvdb_id": self.tvdb_id if hasattr(self, "tvdb_id") else None,
            "tmdb_id": self.tmdb_id if hasattr(self, "tmdb_id") else None,
            "state": self.state.__name__,
            "imdb_link": self.imdb_link if hasattr(self, "imdb_link") else None,
            "aired_at": self.aired_at,
            "genres": self.genres if hasattr(self, "genres") else None,
            "guid": self.guid,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by.__name__,
            "scraped_at": self.scraped_at,
            "scraped_times": self.scraped_times,
        }

    def to_extended_dict(self):
        """Convert item to extended dictionary (API response)"""
        dict = self.to_dict()
        if self.type == "show":
            dict["seasons"] = [season.to_extended_dict() for season in self.seasons]
        if self.type == "season":
            dict["episodes"] = [episode.to_extended_dict() for episode in self.episodes]
        dict["language"] = (self.language if hasattr(self, "language") else None,)
        dict["country"] = (self.country if hasattr(self, "country") else None,)
        dict["network"] = (self.network if hasattr(self, "network") else None,)
        dict["active_stream"] = (
            self.active_stream if hasattr(self, "active_stream") else None,
        )
        dict["symlinked"] = (self.symlinked if hasattr(self, "symlinked") else None,)
        dict["parsed"] = (self.parsed if hasattr(self, "parsed") else None,)
        dict["parsed_data"] = (
            self.parsed_data if hasattr(self, "parsed_data") else None,
        )
        dict["is_anime"] = (self.is_anime if hasattr(self, "is_anime") else None,)
        dict["update_folder"] = (
            self.update_folder if hasattr(self, "update_folder") else None,
        )
        dict["file"] = (self.file if hasattr(self, "file") else None,)
        dict["folder"] = (self.folder if hasattr(self, "folder") else None,)
        return dict

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

    def __init__(self, item, parent_item_id: Optional[ItemId] = None):
        self.type = "movie"
        self.file = item.get("file", None)
        super().__init__(item)
        self.item_id = ItemId(parent_item_id, self.imdb_id)

    def __repr__(self):
        return f"Movie:{self.title}:{self.state.__name__}"

    @property
    def log_string(self):
        return self.title


class Show(MediaItem):
    """Show class"""

    def __init__(self, item, parent_item_id: Optional[ItemId] = None):
        self.locations = item.get("locations", [])
        self.seasons: list[Season | ItemId] = item.get("seasons", [])
        self.type = "show"
        super().__init__(item)
        self.item_id = ItemId(parent_item_id, self.imdb_id)

    def _determine_state(self):
        if all(season.state == States.Library for season in self.seasons):
            return States.Library
        if any(
            season.state == States.Library or season.state == States.LibraryPartial
            for season in self.seasons
        ):
            return States.LibraryPartial
        if any(season.state == States.Symlink for season in self.seasons):
            return States.Symlink
        if any(season.state == States.Download for season in self.seasons):
            return States.Download
        if any(season.state == States.Scrape for season in self.seasons):
            return States.Scrape
        if any(season.state == States.Content for season in self.seasons):
            return States.Content
        return States.Unknown

    def __repr__(self):
        return f"Show:{self.title}:{self.state.name}"

    def fill_in_missing_info(self, other: Self):
        existing_seasons = [s.number for s in self.seasons]
        for s in other.seasons:
            if s.number not in existing_seasons:
                self.add_season(s)
            else:
                existing_season = next(es for es in self.seasons if s.number == es.number) 
                existing_season.fill_in_missing_info(s)
        
    def add_season(self, season):
        """Add season to show"""
        self.seasons.append(season)
        self.seasons = sorted(self.seasons, key=lambda s: s.number)
    
    @property
    def log_string(self):
        return self.title


class Season(MediaItem):
    """Season class"""

    def __init__(self, item, parent_item_id: ItemId):
        self.type = "season"
        self.number = item.get("number", None)
        self.episodes: list[Episode | ItemId] = item.get("episodes", [])
        self.item_id = ItemId(parent_item_id, self.number)
        super().__init__(item)

    def _determine_state(self):
        if len(self.episodes) > 0:
            if all(episode.state == States.Library for episode in self.episodes):
                return States.Library
            if any(episode.state == States.Library for episode in self.episodes):
                return States.LibraryPartial
            if all(episode.state == States.Symlink for episode in self.episodes):
                return States.Symlink
            if all(episode.file and episode.folder for episode in self.episodes):
                return States.Download
            if self.is_scraped():
                return States.Scrape
            if any(episode.state == States.Content for episode in self.episodes):
                return States.Content
        return States.Unknown

    def __eq__(self, other):
        return self.number == other.get('number', None)

    def __repr__(self):
        return f"Season:{self.number}:{self.state.name}"

    def fill_in_missing_info(self, other: Self):
        existing_episodes = [s.number for s in self.episodes]
        for e in other.episodes:
            if e.number not in existing_episodes:
                self.add_episode(e)

    def add_episode(self, episode):
        """Add episode to season"""
        self.episodes.append(episode)
        self.episodes = sorted(self.episodes, key=lambda e: e.number)


    @property
    def log_string(self):
        return self.parent.title + " S" + str(self.number).zfill(2)


class Episode(MediaItem):
    """Episode class"""

    def __init__(self, item, parent_item_id: ItemId):
        self.type = "episode"
        self.number = item.get("number", None)
        self.file = item.get("file", None)
        self.item_id = ItemId(parent_item_id, self.number)
        super().__init__(item)

    def __eq__(self, other):
        if type(self) == type(other) and self.parent_id == other.parent_id:
            return self.number == other.get('number', None)

    def __repr__(self):
        return f"Episode:{self.number}:{self.state.name}"

    def get_file_episodes(self):
        return parser.episodes(self.file)

    @property
    def log_string(self):
        return f"{self.parent.parent.title} S{self.parent.number:02}E{self.number:02}"


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
