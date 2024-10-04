import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Self

import sqlalchemy
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

import utils.websockets.manager as ws_manager
from program.db.db import db
from program.media.state import States
from program.media.subtitle import Subtitle
from program.settings.models import RivenSettingsModel
from utils.logger import logger

from ..db.db_functions import blacklist_stream
from .stream import Stream

EPOCH = datetime.fromtimestamp(0)


class ProfileDataLink(db.Model):
    __tablename__ = 'profiledatalink'
    id: Mapped[int] = mapped_column(sqlalchemy.Integer, primary_key=True)
    data_id = Column(Integer, ForeignKey('profiledata.id'))
    profile_name = Column(String, ForeignKey('profile.name'))

class Profile(db.Model):
    __tablename__ = 'profile'
    name: Mapped[str] = mapped_column(sqlalchemy.String, primary_key=True)
    profile_data = relationship("ProfileData", secondary="profiledatalink", back_populates="profile")
    model = mapped_column(JSONB, nullable=False)

    def __init__(self, model: RivenSettingsModel) -> None:
        self.model = model.to_dict()
        self.name = model.profile

    @property
    def settings_model(self) -> RivenSettingsModel:
        return RivenSettingsModel(**self.model)

    @settings_model.setter
    def settings_model(self, value: RivenSettingsModel):
        self.model = value.model_dump_json()

class ProfileData(db.Model):
    __tablename__ = 'profiledata'
    id: Mapped[int] = mapped_column(sqlalchemy.Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey('mediaitem.id', ondelete="CASCADE"))
    parent: Mapped["MediaItem"] = relationship("MediaItem", back_populates="profiles")

    profile: Mapped[Profile] = relationship("Profile", secondary="profiledatalink", back_populates="profile_data")
    last_state = Column(sqlalchemy.Enum(States))
    last_try = Column(DateTime)

    scraped_at = Column(DateTime)
    scraped_times = Column(Integer)
    streams: Mapped[List["Stream"]] = relationship(secondary="streamrelation", back_populates="parents")
    blacklisted_streams: Mapped[List["Stream"]] = relationship(secondary="streamblacklistrelation", back_populates="blacklisted_parents")

    active_stream_id = Column(Integer, ForeignKey('stream.id'))
    active_stream: Mapped[Optional["Stream"]] = relationship("Stream")
    download_path = Column(String)

    symlink_path = Column(String)
    symlinked_times = Column(Integer)

    subtitles: Mapped[List["Subtitle"]] = relationship(back_populates="parent")

    def __init__(self, profile: Profile) -> None:
        self.last_state: States = States.Requested

        self.profile: Profile = profile

        self.scraped_at: datetime = EPOCH
        self.scraped_times: int = 0
        self.streams: List[Stream] = []
        self.blacklisted_streams: List[Stream] = []

        self.active_stream: Stream | None = None
        self.download_path: Path | None = None

        self.symlink_path: Path | None = None
        self.symlinked_times: int = 0

        self.subtitles: List[Subtitle] = []

    @property
    def state(self):
        return self._determine_state()

    @property
    def is_scraped(self):
        session = object_session(self)
        if session:
            session.refresh(self, attribute_names=['blacklisted_streams']) # Prom: Ensure these reflect the state of whats in the db.
        return (len(self.streams) > 0
            and any(not stream in self.blacklisted_streams for stream in self.streams))

    @property
    def log_string(self):
        return self.parent.log_string

    def _determine_state(self) -> States:
        if self.symlink_path:
            return States.Completed
        elif self.download_path:
            return States.Downloaded
        elif self.is_scraped():
            return States.Scraped
        return States.Requested

    def is_stream_blacklisted(self, stream: Stream):
        """Check if a stream is blacklisted for this item."""
        session = object_session(self)
        if session:
            session.refresh(self, attribute_names=['blacklisted_streams'])
        return stream in self.blacklisted_streams

    def blacklist_stream(self, stream: Stream):
        value = blacklist_stream(self, stream)
        if value:
            logger.debug(f"Blacklisted stream {stream.infohash} for {self.parent.log_string}")
        return value

    def reset(self, soft_reset: bool = False):
        self.type = "profiledata"
        self.scraped_at = EPOCH
        self.scraped_times = 0
        self.streams = []
        self.blacklisted_streams = []
        if not soft_reset:
            self.active_stream = None
        self.download_path = None
        self.symlink_path = None
        self.symlinked_times = 0
        self.subtitles = []

class MediaItem(db.Model):
    __tablename__ = 'mediaitem'

    id: Mapped[int] = mapped_column(sqlalchemy.Integer, primary_key=True)
    type = Column(String)
    last_state = Column(sqlalchemy.Enum(States))

    title = Column(String)
    year = Column(Integer)
    genres = Column(JSONB)
    language = Column(String)

    ids = Column(JSONB, default={"imdb": "", "tvdb": "", "tmdb": ""})
    network = Column(String)
    country = Column(String)
    aired_at = Column(DateTime)

    requested_at = Column(DateTime)
    requested_by = Column(String)
    requested_id = Column(Integer)
    indexed_at = Column(DateTime)

    aliases = Column(JSONB)
    is_anime = Column(Boolean)

    overseerr_id = Column(Integer)

    profiles: Mapped[List["ProfileData"]] = relationship("ProfileData", back_populates="parent")

    __mapper_args__ = {
        "polymorphic_identity": "mediaitem",
        "polymorphic_on":"type",
        "with_polymorphic":"*",
    }

    __table_args__ = (
        Index('ix_mediaitem_id', 'id'),
        Index('ix_mediaitem_type', 'type'),
        Index('ix_mediaitem_requested_by', 'requested_by'),
        Index('ix_mediaitem_title', 'title'),
        Index('ix_mediaitem_ids_imdb_id', func.cast(func.jsonb_extract_path_text(ids, 'imdb'), sqlalchemy.String)),
        Index('ix_mediaitem_ids_tvdb_id', func.cast(func.jsonb_extract_path_text(ids, 'tvdb'), sqlalchemy.String)),
        Index('ix_mediaitem_ids_tmdb_id', func.cast(func.jsonb_extract_path_text(ids, 'tmdb'), sqlalchemy.String)),
        Index('ix_mediaitem_network', 'network'),
        Index('ix_mediaitem_country', 'country'),
        Index('ix_mediaitem_language', 'language'),
        Index('ix_mediaitem_aired_at', 'aired_at'),
        Index('ix_mediaitem_year', 'year'),
        Index('ix_mediaitem_overseerr_id', 'overseerr_id'),
        Index('ix_mediaitem_type_aired_at', 'type', 'aired_at'),  # Composite index
    )

    def __init__(self, item: dict | None) -> None:
        if item is None:
            return
        self.requested_at = item.get("requested_at", datetime.now())
        self.requested_by = item.get("requested_by", "unknown")
        self.requested_id = item.get("requested_id", None)
        self.indexed_at = None
        self.is_anime = item.get("is_anime", False)

        self.title = item.get("title")
        self.ids = { "imdb_id": item.get("imdb_id"),
                     "tvdb_id": item.get("tvdb_id", ""),
                     "tmdb_id": item.get("tmdb_id", "") }

        self.network = item.get("network", "")
        self.country = item.get("country", "")
        self.language = item.get("language", "")
        self.aired_at = item.get("aired_at", EPOCH)
        self.year = item.get("year", 1970)
        self.genres = item.get("genres", [])
        self.aliases = item.get("aliases", {})

        self.overseerr_id = item.get("overseerr_id")

        self.profiles: list[ProfileData] = []
        with db.Session() as session:
            db_profiles = session.query(Profile).all()
            for profile in db_profiles:
                data = ProfileData(profile)
                self.profiles.append(data)

    def store_state(self) -> None:
        _state = self._determine_state()
        if self.last_state and self.last_state != _state:
            ws_manager.send_item_update(json.dumps(self.to_dict()))
        self.last_state = _state

    @property
    def is_released(self) -> bool:
        """Check if an item has been released."""
        if self.aired_at != EPOCH and self.aired_at <= datetime.now():
            return True
        return False

    @property
    def state(self):
        return self._determine_state()

    def _determine_state(self):
        if all(profile.last_state == States.Completed for profile in self.profiles):
            return States.Completed
        elif any(profile.last_state == States.Completed for profile in self.profiles) and any(profile.last_state != States.Completed for profile in self.profiles):
            return States.PartiallyCompleted
        elif not self.is_released:
            return States.Unreleased
        else:
            return States.Requested

    def copy_other_media_attr(self, other):
        """Copy attributes from another media item."""
        self.title = getattr(other, "title")
        self.ids = getattr(other, "ids")
        self.network = getattr(other, "network")
        self.country = getattr(other, "country")
        self.language = getattr(other, "language")
        self.aired_at = getattr(other, "aired_at")
        self.genres = getattr(other, "genres")
        self.is_anime = getattr(other, "is_anime")
        self.overseerr_id = getattr(other, "overseerr_id")

    def to_dict(self):
        """Convert item to dictionary (API response)"""
        return json.dumps(self.__dict__)

    def __eq__(self, other):
        return type(self) == type(other) and self.id == other.id

    def copy(self, other: "MediaItem"):
        self.id = getattr(other, "id", None)
        self.ids = getattr(other, "ids", None)
        if hasattr(self, "number"):
            self.number = getattr(other, "number", None)
        return self

    def get_top_title(self) -> str:
        """Get the top title of the item."""
        if self.type == "season":
            return self.parent.title
        elif self.type == "episode":
            return self.parent.parent.title
        else:
            return self.title

    def get_top_imdb_id(self) -> str:
        """Get the imdb_id of the item at the top of the hierarchy."""
        if self.type == "season":
            return self.parent.ids["imdb_id"]
        elif self.type == "episode":
            return self.parent.parent.ids["imdb_id"]
        return self.ids["imdb_id"]

    def get_aliases(self) -> dict:
        """Get the aliases of the item."""
        if self.type == "season":
            return self.parent.aliases
        elif self.type == "episode":
            return self.parent.parent.aliases
        else:
            return self.aliases


    def reset(self, soft_reset: bool = False):
        """Reset item attributes."""
        if self.type == "show":
            for season in self.seasons:
                for episode in season.episodes:
                    episode._reset(soft_reset)
                season._reset(soft_reset)
        elif self.type == "season":
            for episode in self.episodes:
                episode._reset(soft_reset)
        self._reset(soft_reset)
        self.store_state()

    def _reset(self, soft_reset):
        """Reset item attributes for rescraping."""
        for profile in self.profiles:
            profile.reset(soft_reset)

        logger.debug(f"Item {self.log_string} reset for rescraping")

    @property
    def log_string(self):
        return self.title or self.ids["imdb_id"]

    @property
    def collection(self):
        return self.parent.collection if self.parent else self.ids["imdb_id"]

class Movie(MediaItem):
    __tablename__ = 'movie'
    id = Column(Integer, ForeignKey('mediaitem.id'), primary_key=True)

    __mapper_args__ = {
        "polymorphic_identity": "movie",
    }

    def copy(self, other):
        super().copy(other)
        return self

    def __init__(self, item):
        self.type = "movie"
        super().__init__(item)

    def __repr__(self):
        return f"Movie:{self.log_string}:{self.last_state.name}"

class Show(MediaItem):
    __tablename__ = 'show'
    id = Column(Integer, ForeignKey('mediaitem.id'), primary_key=True)
    seasons: Mapped[List["Season"]] = relationship(back_populates="parent", foreign_keys="Season.parent_id", lazy="joined", cascade="all, delete-orphan", order_by="Season.number")

    __mapper_args__ = {
        "polymorphic_identity": "show",
    }

    def __init__(self, item):
        super().__init__(item)
        self.type = "show"
        self.seasons: list[Season] = item.get("seasons", [])
        self.propagate_attributes_to_childs()

    def _determine_state(self):
        if all(season.state == States.Completed for season in self.seasons):
            return States.Completed
        if any(season.state in [States.Ongoing, States.Unreleased] for season in self.seasons):
            return States.Ongoing
        if any(
            season.state in (States.Completed, States.PartiallyCompleted)
            for season in self.seasons
        ):
            return States.PartiallyCompleted
        if not self.is_released or all(not season.is_released for season in self.seasons):
            return States.Unreleased
        return States.Requested

    def store_state(self) -> None:
        for season in self.seasons:
            season.store_state()
        super().store_state()

    def __repr__(self):
        return f"Show:{self.log_string}:{self.last_state.name}"

    def copy(self, other):
        super(Show, self).copy(other)
        self.seasons = []
        for season in other.seasons:
            new_season = Season(item={}).copy(season, False)
            new_season.parent = self
            self.seasons.append(new_season)
        return self

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


class Season(MediaItem):
    __tablename__ = "season"
    id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("mediaitem.id"), primary_key=True)
    number: Mapped[Optional[int]] = mapped_column(sqlalchemy.Integer, nullable=True)
    parent_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("show.id"), use_existing_column=True)
    parent: Mapped["Show"] = relationship(lazy=False, back_populates="seasons", foreign_keys="Season.parent_id")
    episodes: Mapped[List["Episode"]] = relationship(back_populates="parent", foreign_keys="Episode.parent_id", lazy="joined", cascade="all, delete-orphan", order_by="Episode.number")

    __mapper_args__ = {
        "polymorphic_identity": "season",
    }

    def store_state(self) -> None:
        for episode in self.episodes:
            episode.store_state()
        super().store_state()

    def __init__(self, item):
        self.type = "season"
        self.number = item.get("number", None)
        self.episodes: list[Episode] = item.get("episodes", [])
        self.parent = item.get("parent", None)
        super().__init__(item)
        if self.parent:
            self.parent.is_anime = self.is_anime

    def _determine_state(self):
        if all(episode.state == States.Completed for episode in self.episodes):
            return States.Completed
        if any(episode.state in [States.Ongoing, States.Unreleased] for episode in self.episodes):
            return States.Ongoing
        if any(
            episode.state in (States.Completed, States.PartiallyCompleted)
            for episode in self.episodes
        ):
            return States.PartiallyCompleted
        if not self.is_released or all(not episode.is_released for episode in self.episodes):
            return States.Unreleased
        return States.Requested

    @property
    def is_released(self) -> bool:
        return len(self.episodes) > 0 and any(episode.is_released for episode in self.episodes)

    def __repr__(self):
        return f"Season:{self.number}:{self.last_state.name}"

    def copy(self, other, copy_parent=True):
        super(Season, self).copy(other)
        for episode in other.episodes:
            new_episode = Episode(item={}).copy(episode, False)
            new_episode.parent = self
            self.episodes.append(new_episode)
        if copy_parent and other.parent:
            self.parent = Show(item={}).copy(other.parent)
        return self

    def fill_in_missing_children(self, other: Self):
        existing_episodes = [s.number for s in self.episodes]
        for e in other.episodes:
            if e.number not in existing_episodes:
                self.add_episode(e)

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
    __tablename__ = 'episode'
    id = Column(Integer, ForeignKey('mediaitem.id'), primary_key=True)
    number = Column(Integer)
    parent_id = Column(Integer, ForeignKey('season.id'))
    parent: Mapped["Season"] = relationship(back_populates="episodes", foreign_keys="Episode.parent_id", lazy="joined")

    __mapper_args__ = {
        "polymorphic_identity": "episode",
    }

    def __init__(self, item):
        self.type = "episode"
        self.number = item.get("number", None)
        super().__init__(item)
        if self.parent:
            self.parent.is_anime = self.is_anime

    def __repr__(self):
        return f"Episode:{self.number}:{self.last_state.name}"

    def copy(self, other, copy_parent=True):
        super(Episode, self).copy(other)
        if copy_parent and other.parent:
            self.parent = Season(item={}).copy(other.parent)
        return self

    @property
    def log_string(self):
        return f"{self.parent.log_string}E{self.number:02}"

    def get_top_title(self) -> str:
        return self.parent.parent.title