"""MediaItem class"""
from datetime import datetime
from typing import List, Optional, Self

from program.media.state import States
from RTN import Torrent, parse
# from RTN.patterns import extract_episodes
from utils.logger import logger

from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
import sqlalchemy
from sqlalchemy import orm

from program.db.db import db

class MediaItem(db.Model):
    """MediaItem class"""
    __tablename__ = "MediaItem"
    _id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(sqlalchemy.String, nullable=False)
    number: Mapped[Optional[int]] = mapped_column(sqlalchemy.Integer, nullable=True)
    type: Mapped[str] = mapped_column(sqlalchemy.String, nullable=False)
    requested_at: Mapped[Optional[datetime]] = mapped_column(sqlalchemy.DateTime, default=datetime.now())
    requested_by: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(sqlalchemy.DateTime, nullable=True)
    scraped_at: Mapped[Optional[datetime]] = mapped_column(sqlalchemy.DateTime, nullable=True)
    scraped_times: Mapped[Optional[int]] = mapped_column(sqlalchemy.Integer, default=0)
    active_stream: Mapped[Optional[dict[str, str]]] = mapped_column(sqlalchemy.JSON, nullable=True)
    symlinked: Mapped[Optional[bool]] = mapped_column(sqlalchemy.Boolean, default=False)
    symlinked_at: Mapped[Optional[datetime]] = mapped_column(sqlalchemy.DateTime, nullable=True)
    symlinked_times: Mapped[Optional[int]] = mapped_column(sqlalchemy.Integer, default=0)
    file: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    folder: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    alternative_folder: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    is_anime: Mapped[Optional[bool]] = mapped_column(sqlalchemy.Boolean, default=False)
    title: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    imdb_id: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    tvdb_id: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    tmdb_id: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    network: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    country: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    aired_at: Mapped[Optional[datetime]] = mapped_column(sqlalchemy.DateTime, nullable=True)
    year: Mapped[Optional[int]] = mapped_column(sqlalchemy.Integer, nullable=True)
    genres: Mapped[Optional[List[str]]] = mapped_column(sqlalchemy.JSON, nullable=True)
    key: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    guid: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    update_folder: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, nullable=True)
    overseerr_id: Mapped[Optional[int]] = mapped_column(sqlalchemy.Integer, nullable=True)
    last_state: Mapped[Optional[str]] = mapped_column(sqlalchemy.String, default="Unknown")

    __mapper_args__ = {
        "polymorphic_identity": "mediaitem",
        "polymorphic_on":"type",
        "with_polymorphic":"*",
    }
    @orm.reconstructor
    def init_on_load(self):
        self.streams: Optional[dict[str, Torrent]] = {}
    def __init__(self, item: dict) -> None:
        # id: Mapped[int] = mapped_column(primary_key=True)
        # name: Mapped[str] = mapped_column(String(30))
        # fullname: Mapped[Optional[str]]
        # addresses: Mapped[List["Address"]] = relationship(lazy=False, 
        # back_populates="user", cascade="all, delete-orphan"
        # )
        # user_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("user_account.id"))
        # user: Mapped["User"] = relationship(lazy=False, back_populates="addresses")
        self.requested_at = item.get("requested_at", datetime.now())
        self.requested_by = item.get("requested_by", None)

        self.indexed_at = None

        self.scraped_at  = None
        self.scraped_times = 0
        self.active_stream = item.get("active_stream", {})
        self.streams: Optional[dict[str, Torrent]] = {}

        self.symlinked = False
        self.symlinked_at = None
        self.symlinked_times = 0

        self.file  = None
        self.folder = None
        self.is_anime = item.get("is_anime", False)

        # Media related
        self.title = item.get("title", None)
        self.imdb_id =  item.get("imdb_id", None)
        if self.imdb_id:
            self.imdb_link = f"https://www.imdb.com/title/{self.imdb_id}/"
            if not hasattr(self, "item_id"):
                self.item_id = self.imdb_id
        self.tvdb_id = item.get("tvdb_id", None)
        self.tmdb_id = item.get("tmdb_id", None)
        self.network = item.get("network", None)
        self.country = item.get("country", None)
        self.language = item.get("language", None)
        self.aired_at = item.get("aired_at", None)
        self.year = item.get("year" , None)
        self.genres = item.get("genres", [])

        # Plex related
        self.key = item.get("key", None)
        self.guid = item.get("guid", None)
        self.update_folder = item.get("update_folder", None)

        # Overseerr related
        self.overseerr_id = item.get("overseerr_id", None)

    def store_state(self) -> None:
        self.last_state = self._determine_state().name
        
    @property
    def is_released(self) -> bool:
        """Check if an item has been released."""
        if not self.aired_at:
            return False
        now = datetime.now()
        if self.aired_at > now:
            time_until_release = self.aired_at - now
            days, seconds = time_until_release.days, time_until_release.seconds
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            time_message = f"{self.log_string} will be released in {days} days, {hours:02}:{minutes:02}:{seconds:02}"
            logger.log("ITEM", time_message)
            return False
        return True
    
    @property
    def is_released_nolog(self):
        """Check if an item has been released."""
        if not self.aired_at:
            return False
        return True

    @property
    def state(self):
        return self._determine_state()

    def _determine_state(self):
        if self.key or self.update_folder == "updated":
            return States.Completed
        elif self.symlinked:
            return States.Symlinked
        elif self.file and self.folder:
            return States.Downloaded
        elif self.is_scraped():
            return States.Scraped
        elif self.title:
            return States.Indexed
        elif self.imdb_id and self.requested_by:
            return States.Requested
        return States.Unknown

    def copy_other_media_attr(self, other):
        """Copy attributes from another media item."""
        self.title = getattr(other, "title", None)
        self.tvdb_id = getattr(other, "tvdb_id", None)
        self.tmdb_id = getattr(other, "tmdb_id", None)
        self.network = getattr(other, "network", None)
        self.country = getattr(other, "country", None)
        self.language = getattr(other, "language", None)
        self.aired_at = getattr(other, "aired_at", None)
        self.genres = getattr(other, "genres", [])
        self.is_anime = getattr(other, "is_anime", False)
        self.overseerr_id = getattr(other, "overseerr_id", None)

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

    def has_complete_metadata(self) -> bool:
        """Check if the item has complete metadata."""
        return self.title is not None and self.aired_at is not None

    def to_dict(self):
        """Convert item to dictionary (API response)"""
        return {
            "item_id": str(self.item_id),
            "title": self.title,
            "type": self.__class__.__name__,
            "imdb_id": self.imdb_id if hasattr(self, "imdb_id") else None,
            "tvdb_id": self.tvdb_id if hasattr(self, "tvdb_id") else None,
            "tmdb_id": self.tmdb_id if hasattr(self, "tmdb_id") else None,
            "state": self.state.value,
            "imdb_link": self.imdb_link if hasattr(self, "imdb_link") else None,
            "aired_at": self.aired_at,
            "genres": self.genres if hasattr(self, "genres") else None,
            "is_anime": self.is_anime if hasattr(self, "is_anime") else False,
            "guid": self.guid,
            "requested_at": str(self.requested_at),
            "requested_by": self.requested_by,
            "scraped_at": self.scraped_at,
            "scraped_times": self.scraped_times,
        }

    def to_extended_dict(self, abbreviated_children=False):
        """Convert item to extended dictionary (API response)"""
        dict = self.to_dict()
        match self:
            case Show():
                dict["seasons"] = (
                    [season.to_extended_dict() for season in self.seasons]
                    if not abbreviated_children
                    else self.represent_children
                )
            case Season():
                dict["episodes"] = (
                    [episode.to_extended_dict() for episode in self.episodes]
                    if not abbreviated_children
                    else self.represent_children
                )
        dict["language"] = self.language if hasattr(self, "language") else None
        dict["country"] = self.country if hasattr(self, "country") else None
        dict["network"] = self.network if hasattr(self, "network") else None
        dict["active_stream"] = (
            self.active_stream if hasattr(self, "active_stream") else None
        )
        dict["symlinked"] = self.symlinked if hasattr(self, "symlinked") else None
        dict["symlinked_at"] = (
            self.symlinked_at if hasattr(self, "symlinked_at") else None
        )
        dict["symlinked_times"] = (
            self.symlinked_times if hasattr(self, "symlinked_times") else None
        )
        dict["is_anime"] = self.is_anime if hasattr(self, "is_anime") else None
        dict["update_folder"] = (
            self.update_folder if hasattr(self, "update_folder") else None
        )
        dict["file"] = self.file if hasattr(self, "file") else None
        dict["folder"] = self.folder if hasattr(self, "folder") else None
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

    def get_top_title(self) -> str:
        """Get the top title of the item."""
        match self.__class__.__name__:
            case "Season":
                return self.parent.title
            case "Episode":
                return self.parent.parent.title
            case _:
                return self.title

    def __hash__(self):
        return hash(self.item_id)

    @property
    def log_string(self):
        return self.title or self.imdb_id

    @property
    def collection(self):
        return self.parent.collection if self.parent else self.item_id


class Movie(MediaItem):
    """Movie class"""
    __tablename__ = "Movie"
    _id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("MediaItem._id"), primary_key=True)
    __mapper_args__ = {
        "polymorphic_identity": "movie",
        "polymorphic_load": "inline",
    }
    @orm.reconstructor
    def init_on_load(self):
        self.streams: Optional[dict[str, Torrent]] = {}
    
    def __init__(self, item):
        self.type = "movie"
        self.file = item.get("file", None)
        super().__init__(item)
        self.item_id = self.imdb_id

    def __repr__(self):
        return f"Movie:{self.log_string}:{self.state.name}"

    def __hash__(self):
        return super().__hash__()

class Season(MediaItem):
    """Season class"""
    __tablename__ = "Season"
    _id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("MediaItem._id"), primary_key=True)
    parent_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("Show._id"), use_existing_column=True)
    parent: Mapped["Show"] = relationship(lazy=False, back_populates="seasons", foreign_keys="Season.parent_id")
    episodes: Mapped[List["Episode"]] = relationship(lazy=False, back_populates="parent", single_parent=True, cascade="all, delete-orphan", foreign_keys="Episode.parent_id")
    @orm.reconstructor
    def init_on_load(self):
        self.streams: Optional[dict[str, Torrent]] = {}

    __mapper_args__ = {
        "polymorphic_identity": "season",
        "polymorphic_load": "inline",
    }
    def store_state(self) -> None:
        for episode in self.episodes:
            episode.store_state()
        self.last_state = self._determine_state().name
    def __init__(self, item):
        self.type = "season"
        self.number = item.get("number", None)
        self.episodes: list[Episode] = item.get("episodes", [])
        self.item_id = self.number
        self.parent = item.get("parent", None)
        super().__init__(item)
        if self.parent and isinstance(self.parent, Show):
            self.is_anime = self.parent.is_anime

    def _determine_state(self):
        if len(self.episodes) > 0:
            if all(episode.state == States.Completed for episode in self.episodes):
                return States.Completed
            if all(episode.state == States.Symlinked for episode in self.episodes):
                return States.Symlinked
            if all(episode.file and episode.folder for episode in self.episodes):
                return States.Downloaded
            if self.is_scraped():
                return States.Scraped
            if any(episode.state == States.Completed for episode in self.episodes):
                return States.PartiallyCompleted
            if any(episode.state == States.Indexed for episode in self.episodes):
                return States.Indexed
            if any(episode.state == States.Requested for episode in self.episodes):
                return States.Requested
        return States.Unknown

    @property
    def is_released(self) -> bool:
        return any(episode.is_released for episode in self.episodes)

    def __eq__(self, other):
        if (
            type(self) == type(other)
            and self.parent_id == other.parent_id
        ):
            return self.number == other.get("number", None)

    def __repr__(self):
        return f"Season:{self.number}:{self.state.name}"

    def __hash__(self):
        return super().__hash__()

    def fill_in_missing_children(self, other: Self):
        existing_episodes = [s.number for s in self.episodes]
        for e in other.episodes:
            if e.number not in existing_episodes:
                self.add_episode(e)

    def get_episode_index_by_id(self, item_id):
        """Find the index of an episode by its item_id."""
        for i, episode in enumerate(self.episodes):
            if episode.item_id == item_id:
                return i
        return None

    def represent_children(self):
        return [e.log_string for e in self.episodes]

    def add_episode(self, episode):
        """Add episode to season"""
        if episode.number in [e.number for e in self.episodes]:
            return

        episode.is_anime = self.is_anime
        self.episodes.append(episode)
        episode.parent = self
        self.episodes = sorted(self.episodes, key=lambda e: e.number)

    @property
    def log_string(self):
        return self.parent.log_string + " S" + str(self.number).zfill(2)

    def get_top_title(self) -> str:
        return self.parent.title

class Episode(MediaItem):
    """Episode class"""
    __tablename__ = "Episode"
    _id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("MediaItem._id"), primary_key=True)
    parent_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("Season._id"), use_existing_column=True)
    parent: Mapped["Season"] = relationship(lazy=False, back_populates='episodes', foreign_keys="Episode.parent_id")
    @orm.reconstructor
    def init_on_load(self):
        self.streams: Optional[dict[str, Torrent]] = {}

    __mapper_args__ = {
        "polymorphic_identity": "episode",
        "polymorphic_load": "inline",
    }

    def __init__(self, item):
        self.type = "episode"
        self.number = item.get("number", None)
        self.file = item.get("file", None)
        self.item_id = self.number# , parent_id=item.get("parent_id"))
        super().__init__(item)
        if self.parent and isinstance(self.parent, Season):
            self.is_anime = self.parent.parent.is_anime

    def __eq__(self, other):
        if (
            type(self) == type(other)
            and self.item_id == other.item_id
            and self.parent.parent.item_id == other.parent.parent.item_id
        ):
            return self.number == other.get("number", None)

    def __repr__(self):
        return f"Episode:{self.number}:{self.state.name}"

    def __hash__(self):
        return super().__hash__()

    def get_file_episodes(self) -> List[int]:
        if not self.file or not isinstance(self.file, str):
            raise ValueError("The file attribute must be a non-empty string.")
        # return list of episodes
        return parse(self.file).episode

    @property
    def log_string(self):
        return f"{self.parent.log_string}E{self.number:02}"

    def get_top_title(self) -> str:
        return self.parent.parent.title

    def get_top_year(self) -> Optional[int]:
        return self.parent.parent.year

    def get_season_year(self) -> Optional[int]:
        return self.parent.year

class Show(MediaItem):
    """Show class"""
    __tablename__ = "Show"
    _id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("MediaItem._id"), primary_key=True)
    seasons: Mapped[List["Season"]] = relationship(lazy=False, back_populates="parent", single_parent=True, cascade="all, delete-orphan", foreign_keys="Season.parent_id")
    @orm.reconstructor
    def init_on_load(self):
        self.streams: Optional[dict[str, Torrent]] = {}
    
    __mapper_args__ = {
        "polymorphic_identity": "show",
        "polymorphic_load": "inline",
    }

    def __init__(self, item):
        super().__init__(item)
        self.type = "show"
        self.locations = item.get("locations", [])
        self.seasons: list[Season] = item.get("seasons", [])
        self.item_id = item.get("imdb_id")
        self.propagate_attributes_to_childs()

    def get_season_index_by_id(self, item_id):
        """Find the index of an season by its item_id."""
        for i, season in enumerate(self.seasons):
            if season.item_id == item_id:
                return i
        return None

    def _determine_state(self):
        if all(season.state == States.Completed for season in self.seasons):
            return States.Completed
        if all(season.state == States.Symlinked for season in self.seasons):
            return States.Symlinked
        if all(season.state == States.Downloaded for season in self.seasons):
            return States.Downloaded
        if self.is_scraped():
            return States.Scraped
        if any(
            season.state in (States.Completed, States.PartiallyCompleted)
            for season in self.seasons
        ):
            return States.PartiallyCompleted
        if any(season.state == States.Indexed for season in self.seasons):
            return States.Indexed
        if any(season.state == States.Requested for season in self.seasons):
            return States.Requested
        return States.Unknown
    def store_state(self) -> None:
        for season in self.seasons:
            season.store_state()
        self.last_state = self._determine_state().name
    def __repr__(self):
        return f"Show:{self.log_string}:{self.state.name}"

    def __hash__(self):
        return super().__hash__()

    def fill_in_missing_children(self, other: Self):
        existing_seasons = [s.number for s in self.seasons]
        for s in other.seasons:
            if s.number not in existing_seasons:
                self.add_season(s)
            else:
                existing_season = next(
                    es for es in self.seasons if s.number == es.number
                )
                existing_season.fill_in_missing_children(s)

    def add_season(self, season):
        """Add season to show"""
        if season.number not in [s.number for s in self.seasons]:
            season.is_anime = self.is_anime
            self.seasons.append(season)
            season.parent = self
            #season.item_id.parent_id = self.item_id
            self.seasons = sorted(self.seasons, key=lambda s: s.number)

    def propagate_attributes_to_childs(self):
        """Propagate show attributes to seasons and episodes if they are empty or do not match."""
        # Important attributes that need to be connected.
        attributes = ["genres", "country", "network", "language", "is_anime"]

        def propagate(target, source):
            for attr in attributes:
                source_value = getattr(source, attr, None)
                target_value = getattr(target, attr, None)
                # Check if the attribute source is not falsy (none, false, 0, [])
                # and if the target is not None we set the source to the target
                if (not target_value) and source_value is not None:
                    setattr(target, attr, source_value)

        for season in self.seasons:
            propagate(season, self)
            for episode in season.episodes:
                propagate(episode, self)



def _set_nested_attr(obj, key, value):
    if "." in key:
        parts = key.split(".", 1)
        current_key, rest_of_keys = parts[0], parts[1]

        if not hasattr(obj, current_key):
            raise AttributeError(f"Object does not have the attribute '{current_key}'.")

        current_obj = getattr(obj, current_key)
        _set_nested_attr(current_obj, rest_of_keys, value)
    elif isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)