"""Trakt updater module"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Generator, List, Optional, Union

from program.db.db import db
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.request import get

CLIENT_ID = "0183a05ad97098d87287fe46da4ae286f434f32e8e951caad4cc147c947d79a3"


class TraktIndexer:
    """Trakt updater class"""
    key = "TraktIndexer"

    def __init__(self):
        self.key = "traktindexer"
        self.ids = []
        self.initialized = True
        self.settings = settings_manager.settings.indexer
        self.failed_ids = set()

    @staticmethod
    def copy_attributes(source, target):
        """Copy attributes from source to target."""
        attributes = ["file", "folder", "update_folder", "symlinked", "is_anime", "symlink_path", "subtitles", "requested_by", "requested_at", "overseerr_id", "active_stream", "requested_id"]
        for attr in attributes:
            setattr(target, attr, getattr(source, attr, None))

    def copy_items(self, itema: MediaItem, itemb: MediaItem):
        """Copy attributes from itema to itemb recursively."""
        is_anime = itema.is_anime or itemb.is_anime
        if itema.type == "mediaitem" and itemb.type == "show":
            itema.seasons = itemb.seasons
        if itemb.type == "show" and itema.type != "movie":
            for seasona in itema.seasons:
                for seasonb in itemb.seasons:
                    if seasona.number == seasonb.number:  # Check if seasons match
                        for episodea in seasona.episodes:
                            for episodeb in seasonb.episodes:
                                if episodea.number == episodeb.number:  # Check if episodes match
                                    self.copy_attributes(episodea, episodeb)
                        seasonb.set("is_anime", is_anime)
            itemb.set("is_anime", is_anime)
        elif itemb.type == "movie":
            self.copy_attributes(itema, itemb)
            itemb.is_anime = is_anime
        else:
            logger.error(f"Item types {itema.type} and {itemb.type} do not match cant copy metadata")
        return itemb

    def run(self, in_item: MediaItem, log_msg: bool = True) -> Generator[Union[Movie, Show, Season, Episode], None, None]:
        """Run the Trakt indexer for the given item."""
        if not in_item:
            logger.error("Item is None")
            return
        if not (imdb_id := in_item.ids["imdb_id"]):
            logger.error(f"Item {in_item.log_string} does not have an imdb_id, cannot index it")
            return

        if in_item.ids["imdb_id"] in self.failed_ids:
            return

        item_type = in_item.type if in_item.type != "mediaitem" else None
        item = create_item_from_imdb_id(imdb_id, item_type)

        if item:
            if item.type == "show":
                self._add_seasons_to_show(item, imdb_id)
            elif item.type == "movie":
                pass
            else:
                logger.error(f"Indexed IMDb Id {item.ids['imdb_id']} returned the wrong item type: {item.type}")
                self.failed_ids.add(in_item.ids["imdb_id"])
                return
        else:
            logger.error(f"Failed to index item with imdb_id: {in_item.ids['imdb_id']}")
            self.failed_ids.add(in_item.ids["imdb_id"])
            return

        item = self.copy_items(in_item, item)
        item.indexed_at = datetime.now()

        if log_msg: # used for mapping symlinks to database, need to hide this log message
            logger.debug(f"Indexed IMDb id ({in_item.ids['imdb_id']}) as {item.type.title()}: {item.log_string}")
        yield item

    @staticmethod
    def should_submit(item: MediaItem) -> bool:
        if not item.indexed_at or not item.title:
            return True

        settings = settings_manager.settings.indexer

        try:
            interval = timedelta(seconds=settings.update_interval)
            return datetime.now() - item.indexed_at > interval
        except Exception:
            logger.error(f"Failed to parse date: {item.indexed_at} with format: {interval}")
            return False

    @staticmethod
    def _add_seasons_to_show(show: Show, imdb_id: str):
        """Add seasons to the given show using Trakt API."""
        if not imdb_id or not imdb_id.startswith("tt"):
            logger.error(f"Item {show.log_string} does not have an imdb_id, cannot index it")
            return

        seasons = get_show(imdb_id)
        for season in seasons:
            if season.number == 0:
                continue
            season_item = _map_item_from_data(season, "season", show.genres)
            if season_item:
                for episode in season.episodes:
                    episode_item = _map_item_from_data(episode, "episode", show.genres)
                    if episode_item:
                        season_item.add_episode(episode_item)
                show.add_season(season_item)


def _map_item_from_data(data, item_type: str, show_genres: List[str] = None) -> Optional[MediaItem]:
    """Map trakt.tv API data to MediaItemContainer."""
    if item_type not in ["movie", "show", "season", "episode"]:
        logger.debug(f"Unknown item type {item_type} for {data.title} not found in list of acceptable items")
        return None

    formatted_aired_at = _get_formatted_date(data, item_type)
    genres = getattr(data, "genres", None) or show_genres

    item = {
        "title": getattr(data, "title", None),
        "year": getattr(data, "year", None),
        "status": getattr(data, "status", None),
        "aired_at": formatted_aired_at,
        "imdb_id": getattr(data.ids, "imdb", None),
        "tvdb_id": getattr(data.ids, "tvdb", None),
        "tmdb_id": getattr(data.ids, "tmdb", None),
        "genres": genres,
        "network": getattr(data, "network", None),
        "country": getattr(data, "country", None),
        "language": getattr(data, "language", None),
        "requested_at": datetime.now(),
    }

    item["is_anime"] = (
        ("anime" in genres) 
        or ("animation" in genres and (item["country"] in ("jp", "kr") or item["language"] == "ja"))
        if genres
        else False
    )

    match item_type:
        case "movie":
            item["aliases"] = get_show_aliases(item["imdb_id"], "movies")
            return Movie(item)
        case "show":
            item["aliases"] = get_show_aliases(item["imdb_id"], "shows")
            return Show(item)
        case "season":
            item["number"] = data.number
            return Season(item)
        case "episode":
            item["number"] = data.number
            return Episode(item)
        case _:
            logger.error(f"Unknown item type {item_type} for {data.title} not found in list of acceptable items")
            return None


def _get_formatted_date(data, item_type: str) -> Optional[datetime]:
    """Get the formatted aired date from the data."""
    if item_type in ["show", "season", "episode"] and (first_aired := getattr(data, "first_aired", None)):
        return datetime.strptime(first_aired, "%Y-%m-%dT%H:%M:%S.%fZ")
    if item_type == "movie" and (released := getattr(data, "released", None)):
        return datetime.strptime(released, "%Y-%m-%d")
    return None


def get_show(imdb_id: str) -> dict:
    """Wrapper for trakt.tv API show method."""
    if not imdb_id:
        return {}
    url = f"https://api.trakt.tv/shows/{imdb_id}/seasons?extended=episodes,full"
    response = get(url, timeout=30, additional_headers={"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
    return response.data if response.is_ok and response.data else {}


def get_show_aliases(imdb_id: str, item_type: str) -> List[dict]:
    """Wrapper for trakt.tv API show method."""
    if not imdb_id:
        return []
    url = f"https://api.trakt.tv/{item_type}/{imdb_id}/aliases"
    try:
        response = get(url, timeout=30, additional_headers={"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
        if response.is_ok and response.data:
            aliases = {}
            for ns in response.data:
                country = ns.country
                title = ns.title
                if title and title.startswith("Anime-"):
                    title = title[len("Anime-"):]
                if country not in aliases:
                    aliases[country] = []
                if title not in aliases[country]:
                    aliases[country].append(title)
            return aliases
    except Exception:
        logger.error(f"Failed to get show aliases for {imdb_id}")
    return {}


def create_item_from_imdb_id(imdb_id: str, type: str = None) -> Optional[MediaItem]:
    """Wrapper for trakt.tv API search method."""
    url = f"https://api.trakt.tv/search/imdb/{imdb_id}?extended=full"
    response = get(url, timeout=30, additional_headers={"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
    if not response.is_ok or not response.data:
        logger.error(f"Failed to create item using imdb id: {imdb_id}")  # This returns an empty list for response.data
        return None

    data = next((d for d in response.data if d.type == type), None)
    if not data:
        clause = lambda x: x.type == type if type else x in ["show", "movie", "season", "episode"]
        data = next((d for d in response.data if clause), None)

    return _map_item_from_data(getattr(data, data.type), data.type) if data else None


def get_imdbid_from_tmdb(tmdb_id: str, type: str = "movie") -> Optional[str]:
    """Wrapper for trakt.tv API search method."""
    url = f"https://api.trakt.tv/search/tmdb/{tmdb_id}" # ?extended=full
    response = get(url, timeout=30, additional_headers={"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
    if not response.is_ok or not response.data:
        return None
    imdb_id = get_imdb_id_from_list(response.data, id_type="tmdb", _id=tmdb_id, type=type)
    if imdb_id and imdb_id.startswith("tt"):
        return imdb_id
    logger.error(f"Failed to fetch imdb_id for tmdb_id: {tmdb_id}")
    return None


def get_imdbid_from_tvdb(tvdb_id: str, type: str = "show") -> Optional[str]:
    """Wrapper for trakt.tv API search method."""
    url = f"https://api.trakt.tv/search/tvdb/{tvdb_id}"
    response = get(url, timeout=30, additional_headers={"trakt-api-version": "2", "trakt-api-key": CLIENT_ID})
    if not response.is_ok or not response.data:
        return None
    imdb_id = get_imdb_id_from_list(response.data, id_type="tvdb", _id=tvdb_id, type=type)
    if imdb_id and imdb_id.startswith("tt"):
        return imdb_id
    logger.error(f"Failed to fetch imdb_id for tvdb_id: {tvdb_id}")
    return None


def get_imdb_id_from_list(namespaces: List[SimpleNamespace], id_type: str = None, _id: str = None, type: str = None) -> Optional[str]:
    """Get the imdb_id from the list of namespaces."""
    if not any([id_type, _id, type]):
        return None

    for ns in namespaces:
        if type == "movie" and hasattr(ns, 'movie') and hasattr(ns.movie, 'ids') and hasattr(ns.movie.ids, 'imdb'):
            if str(getattr(ns.movie.ids, id_type)) == str(_id):
                return ns.movie.ids.imdb
        elif type == "show" and hasattr(ns, 'show') and hasattr(ns.show, 'ids') and hasattr(ns.show.ids, 'imdb'):
            if str(getattr(ns.show.ids, id_type)) == str(_id):
                return ns.show.ids.imdb
        elif type == "season" and hasattr(ns, 'season') and hasattr(ns.season, 'ids') and hasattr(ns.season.ids, 'imdb'):
            if str(getattr(ns.season.ids, id_type)) == str(_id):
                return ns.season.ids.imdb
        elif type == "episode" and hasattr(ns, 'episode') and hasattr(ns.episode, 'ids') and hasattr(ns.episode.ids, 'imdb'):
            if str(getattr(ns.episode.ids, id_type)) == str(_id):
                return ns.episode.ids.imdb
    return None