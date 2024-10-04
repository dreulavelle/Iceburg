"""Shared functions for scrapers."""
from typing import Dict, Set, Union

from RTN import RTN, ParsedData, Torrent, sort_torrents
from RTN.exceptions import GarbageTorrent

from program.media.item import ProfileData
from program.media.state import States
from program.media.stream import Stream
from program.settings.manager import settings_manager
from program.settings.versions import models
from utils.logger import logger

enable_aliases = settings_manager.settings.scraping.enable_aliases
settings_model = settings_manager.settings.ranking
ranking_model = models.get(settings_model.profile)
rtn = RTN(settings_model, ranking_model)


def _get_stremio_identifier(profile: ProfileData) -> tuple[str | None, str, str]:
    """Get the stremio identifier for a media item based on its type."""
    imdb_id = profile.parent.get_top_imdb_id()
    if profile.parent.type == "show":
        identifier, scrape_type, imdb_id = ":1:1", "series", imdb_id
    elif profile.parent.type == "season":
        identifier, scrape_type, imdb_id = f":{profile.parent.number}:1", "series", imdb_id
    elif profile.parent.type == "episode":
        identifier, scrape_type, imdb_id = f":{profile.parent.parent.number}:{profile.parent.number}", "series", imdb_id
    elif profile.parent.type == "movie":
        identifier, scrape_type, imdb_id = None, "movie", imdb_id
    else:
        return None, None, None
    return identifier, scrape_type, imdb_id


def _parse_results(profile: ProfileData, results: Dict[str, str], log_msg: bool = True) -> Dict[str, Stream]:
    """Parse the results from the scrapers into Torrent objects."""
    torrents: Set[Torrent] = set()
    processed_infohashes: Set[str] = set()
    correct_title: str = profile.parent.get_top_title()

    logger.log("SCRAPER", f"Processing {len(results)} results for {profile.log_string}")

    if profile.parent.type in ["show", "season", "episode"]:
        needed_seasons: list[int] = _get_needed_seasons(profile.parent)

    for infohash, raw_title in results.items():
        if infohash in processed_infohashes:
            continue

        try:
            torrent: Torrent = rtn.rank(
                raw_title=raw_title,
                infohash=infohash,
                correct_title=correct_title,
                remove_trash=profile.profile.model["options"]["remove_all_trash"],
                aliases=profile.parent.get_aliases() if enable_aliases else {}  # in some cases we want to disable aliases
            )


            if torrent.data.country and not profile.parent.is_anime:
                if _get_item_country(profile.parent) != torrent.data.country:
                    if settings_manager.settings.scraping.parse_debug:
                        logger.debug(f"Skipping torrent for incorrect country with {profile.log_string}: {raw_title}")
                    continue

            if profile.parent.type in ["show", "season", "episode"]:
                if torrent.data.complete:
                    torrents.add(torrent)
                    processed_infohashes.add(infohash)
                    continue

            if profile.parent.type == "movie":
                # Check if a movie is within a year range of +/- 1 year.
                # Ex: [2018, 2019, 2020] for a 2019 movie
                if _check_item_year(profile.parent, torrent.data):
                    torrents.add(torrent)

            elif profile.parent.type == "show":
                if torrent.data.seasons and not torrent.data.episodes:
                    # We subtract one because Trakt doesn't always index
                    # shows according to uploaders
                    if len(torrent.data.seasons) >= (len(needed_seasons) - 1):
                        torrents.add(torrent)

            elif profile.parent.type == "season":
                # If the torrent has the needed seasons and no episodes, we can add it
                if any(season in torrent.data.seasons for season in needed_seasons) and not torrent.data.episodes:
                    torrents.add(torrent)

            elif profile.parent.type == "episode":
                # If the torrent has the season and episode numbers, we can add it
                if (
                    profile.parent.number in torrent.data.episodes
                    and profile.parent.parent.number in torrent.data.seasons
                ):
                    torrents.add(torrent)
                # Anime edge cases where no season number is present for single season shows
                elif (
                    len(profile.parent.parent.parent.seasons) == 1
                    and not torrent.data.seasons
                    and profile.parent.number in torrent.data.episodes
                ):
                    torrents.add(torrent)
                # If no episodes are present but the needed seasons are, we'll add it
                elif any(
                    season in torrent.data.seasons
                    for season in needed_seasons
                ) and not torrent.data.episodes:
                    torrents.add(torrent)

            processed_infohashes.add(infohash)

        except (ValueError, AttributeError) as e:
            # The only stuff I've seen that show up here is titles with a date.
            # Dates can be sometimes parsed incorrectly by Arrow library,
            # so we'll just ignore them.
            if settings_manager.settings.scraping.parse_debug and log_msg:
                logger.debug(f"Skipping torrent: '{raw_title}' - {e}")
            continue
        except GarbageTorrent as e:
            if settings_manager.settings.scraping.parse_debug and log_msg:
                logger.debug(f"Trashing torrent for {profile.log_string}: '{raw_title}'")
            continue

    if torrents:
        logger.log("SCRAPER", f"Processed {len(torrents)} matches for {profile.log_string}")
        torrents = sort_torrents(torrents)
        torrents_dict = {}
        for torrent in torrents.values():
            torrents_dict[torrent.infohash] = Stream(torrent)
        return torrents_dict
    return {}


# helper functions

def _check_item_year(profile: ProfileData, parsed_data: ParsedData) -> bool:
    """Check if the year of the torrent is within the range of the item."""
    year_range = [profile.parent.aired_at.year - 1, profile.parent.aired_at.year, profile.parent.aired_at.year + 1]
    if profile.parent.type == "movie" and parsed_data.year:
        return parsed_data.year in year_range
    return False

def _get_item_country(profile: ProfileData) -> str:
    """Get the country code for a country."""
    if profile.parent.type == "season":
        return profile.parent.country.upper()
    elif profile.parent.type == "episode":
        return profile.parent.parent.country.upper()
    return profile.parent.country.upper()

def _get_needed_seasons(profile: ProfileData) -> list[int]:
    """Get the seasons that are needed for the item."""
    if profile.parent.type == "show":
        return [season.number for season in profile.parent.seasons if season.last_state != States.Completed]
    elif profile.parent.type == "season":
        return [season.number for season in profile.parent.seasons if season.last_state != States.Completed]
    elif profile.parent.type == "episode":
        return [season.number for season in profile.parent.parent.seasons if season.last_state != States.Completed]
    return []
