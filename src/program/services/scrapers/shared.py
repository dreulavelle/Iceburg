"""Shared functions for scrapers."""
from typing import Dict, Optional, Set, Type, Union

from loguru import logger
from RTN import RTN, ParsedData, Torrent, sort_torrents
from RTN.exceptions import GarbageTorrent

from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.media.state import States
from program.media.stream import Stream
from program.settings.manager import settings_manager
from program.settings.versions import models
from program.utils.request import (
    BaseRequestHandler,
    HttpMethod,
    ResponseObject,
    ResponseType,
    Session,
)

bucket_limit = settings_manager.settings.scraping.bucket_limit or 5
enable_aliases = settings_manager.settings.scraping.enable_aliases
ranking_settings = settings_manager.settings.ranking
ranking_model = models.get(ranking_settings.profile)
rtn = RTN(ranking_settings, ranking_model)


class ScraperRequestHandler(BaseRequestHandler):
    def __init__(self, session: Session, response_type=ResponseType.SIMPLE_NAMESPACE, custom_exception: Optional[Type[Exception]] = None, request_logging: bool = False):
        super().__init__(session, response_type=response_type, custom_exception=custom_exception, request_logging=request_logging)

    def execute(self, method: HttpMethod, endpoint: str, overriden_response_type: ResponseType = None, **kwargs) -> ResponseObject:
        return super()._request(method, endpoint, overriden_response_type=overriden_response_type, **kwargs)


def _get_stremio_identifier(item: MediaItem) -> tuple[str | None, str, str]:
    """Get the stremio identifier for a media item based on its type."""
    if isinstance(item, Show):
        identifier, scrape_type, imdb_id = ":1:1", "series", item.imdb_id
    elif isinstance(item, Season):
        identifier, scrape_type, imdb_id = f":{item.number}:1", "series", item.parent.imdb_id
    elif isinstance(item, Episode):
        identifier, scrape_type, imdb_id = f":{item.parent.number}:{item.number}", "series", item.parent.parent.imdb_id
    elif isinstance(item, Movie):
        identifier, scrape_type, imdb_id = None, "movie", item.imdb_id
    else:
        return None, None, None
    return identifier, scrape_type, imdb_id


def _parse_results(item: MediaItem, results: Dict[str, str], log_msg: bool = True) -> Dict[str, Stream]:
    """Parse the results from the scrapers into Torrent objects."""
    torrents: Set[Torrent] = set()
    processed_infohashes: Set[str] = set()
    correct_title: str = item.get_top_title()

    logger.log("SCRAPER", f"Processing {len(results)} results for {item.log_string}")

    if item.type in ["show", "season", "episode"]:
        needed_seasons: list[int] = _get_needed_seasons(item)

    for infohash, raw_title in results.items():
        if infohash in processed_infohashes:
            continue

        try:
            torrent: Torrent = rtn.rank(
                raw_title=raw_title,
                infohash=infohash,
                correct_title=correct_title,
                remove_trash=settings_manager.settings.ranking.options["remove_all_trash"],
                aliases=item.get_aliases() if enable_aliases else {}  # in some cases we want to disable aliases
            )


            if torrent.data.country and not item.is_anime:
                if _get_item_country(item) != torrent.data.country:
                    if settings_manager.settings.scraping.parse_debug:
                        logger.debug(f"Skipping torrent for incorrect country with {item.log_string}: {raw_title}")
                    continue

            if item.type in ["show", "season", "episode"]:
                if torrent.data.complete:
                    torrents.add(torrent)
                    processed_infohashes.add(infohash)
                    continue

            if item.type == "movie":
                # Check if a movie is within a year range of +/- 1 year.
                # Ex: [2018, 2019, 2020] for a 2019 movie
                if _check_item_year(item, torrent.data):
                    torrents.add(torrent)
                else:
                    logger.debug(f"Ignoring torrent {torrent.infohash}:{raw_title} due to not in time year range, {item.log_string}")

            elif item.type == "show":
                if torrent.data.seasons and not torrent.data.episodes:
                    # We subtract one because Trakt doesn't always index 
                    # shows according to uploaders
                    if len(torrent.data.seasons) >= (len(needed_seasons) - 1):
                        torrents.add(torrent)
                    else:
                        logger.debug(f"Ignoring torrent {torrent.infohash}:{raw_title} due mismatch in season size, required {len(needed_seasons)} current {len(torrent.data.seasons)}, {item.log_string}")

            elif item.type == "season":
                # If the torrent has the needed seasons and no episodes, we can add it
                if any(season in torrent.data.seasons for season in needed_seasons) and not torrent.data.episodes:
                    torrents.add(torrent)
                else:
                    logger.debug(f"Ignoring torrent {torrent.infohash}:{raw_title} due to missing required season, {item.log_string}")


            elif item.type == "episode":
                # If the torrent has the season and episode numbers, we can add it
                if (
                    item.number in torrent.data.episodes
                    and item.parent.number in torrent.data.seasons
                ) or (
                    len(item.parent.parent.seasons) == 1
                    and not torrent.data.seasons
                    and item.number in torrent.data.episodes
                ) or any(
                    season in torrent.data.seasons
                    for season in needed_seasons
                ) and not torrent.data.episodes:
                    torrents.add(torrent)
                else:
                    logger.debug(f"Ignoring torrent {torrent.infohash}:{raw_title} due to mismatch in season and episode numbers, {item.log_string}")

            else:
                logger.debug(f"Skipping torrent {infohash}:{raw_title} as it does not fit to any category. {item.log_string}")
            processed_infohashes.add(infohash)

        except (ValueError, AttributeError) as e:
            logger.debug(f"Invalid torrent {raw_title} : {e}")
            # The only stuff I've seen that show up here is titles with a date.
            # Dates can be sometimes parsed incorrectly by Arrow library,
            # so we'll just ignore them.
            if settings_manager.settings.scraping.parse_debug and log_msg:
                logger.debug(f"Skipping torrent: '{raw_title}' - {e}")
            continue
        except GarbageTorrent as e:
            logger.debug(f"GarbageTorrent {raw_title} due to {e}")
            if settings_manager.settings.scraping.parse_debug and log_msg:
                logger.debug(e)
            continue

    if torrents:
        logger.log("SCRAPER", f"Found {len(torrents)} streams for {item.log_string}")
        torrents = sort_torrents(torrents, bucket_limit=bucket_limit)
        torrents_dict = {}
        for torrent in torrents.values():
            torrents_dict[torrent.infohash] = Stream(torrent)
        logger.log("SCRAPER", f"Kept {len(torrents_dict)} streams for {item.log_string} after processing bucket limit")
        return torrents_dict
    logger.debug(f"No valid torrent remains after filtering for {item.log_string}")
    return {}


# helper functions

def _check_item_year(item: MediaItem, data: ParsedData) -> bool:
    """Check if the year of the torrent is within the range of the item."""
    year_range = [item.aired_at.year - 1, item.aired_at.year, item.aired_at.year + 1]
    if item.type == "movie" and data.year:
        return data.year in year_range
    return False

def _get_item_country(item: MediaItem) -> str:
    """Get the country code for a country."""
    if item.type == "season":
        return item.parent.country.upper()
    elif item.type == "episode":
        return item.parent.parent.country.upper()
    return item.country.upper()

def _get_needed_seasons(item: Union[Show, Season, Episode]) -> list[int]:
    """Get the seasons that are needed for the item."""
    if item.type == "show":
        return [season.number for season in item.seasons if season.last_state != States.Completed]
    elif item.type == "season":
        return [season.number for season in item.parent.seasons if season.last_state != States.Completed]
    elif item.type == "episode":
        return [season.number for season in item.parent.parent.seasons if season.last_state != States.Completed]
    return []
