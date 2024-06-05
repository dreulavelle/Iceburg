"""Realdebrid module"""

import contextlib
import time
from os.path import splitext
from pathlib import Path
from types import SimpleNamespace
from typing import Generator, List, Union

import regex
from program.media.item import Episode, MediaItem, Movie, Season, Show
from program.media.state import States
from program.settings.manager import settings_manager
from requests import ConnectTimeout
from RTN import extract_episodes, parsett
from RTN.exceptions import GarbageTorrent
from RTN.parser import parse, title_match
from utils.logger import logger
from utils.request import get, ping, post

WANTED_FORMATS = {".mkv", ".mp4", ".avi"}
RD_BASE_URL = "https://api.real-debrid.com/rest/1.0"


class Debrid:
    """Real-Debrid API Wrapper"""

    def __init__(self, hash_cache):
        self.key = "realdebrid"
        self.settings = settings_manager.settings.downloaders.real_debrid
        self.auth_headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        self.initialized = self.validate()
        if not self.initialized:
            return
        self.hash_cache = hash_cache
        logger.success("Real Debrid initialized!")

    def validate(self) -> bool:
        """Validate Real-Debrid settings and API key"""
        if not self.settings.enabled:
            logger.warning("Real-Debrid is set to disabled")
            return False
        if not self.settings.api_key:
            logger.warning("Real-Debrid API key is not set")
            return False

        try:
            response = ping(f"{RD_BASE_URL}/user", additional_headers=self.auth_headers)
            if response.ok:
                user_info = response.json()
                return user_info.get("premium", 0) > 0
        except ConnectTimeout:
            logger.error("Connection to Real-Debrid timed out.")
        except Exception as e:
            logger.exception(f"Failed to validate Real-Debrid settings: {e}")
        return False

    def run(self, item: MediaItem) -> Generator[MediaItem, None, None]:
        """Download media item from real-debrid.com"""
        if isinstance(item, Show) or (item.file and item.folder):
            return
        if not self.is_cached(item):
            return
        if not self._is_downloaded(item):
            self._download_item(item)
        self.log_item(item)
        yield item

    @staticmethod
    def log_item(item: MediaItem) -> None:
        """Log only the files downloaded for the item based on its type."""
        if isinstance(item, Movie):
            if item.file and item.folder:
                logger.log("DEBRID", f"Downloaded {item.log_string} with file: {item.file}")
            else:
                logger.debug(f"Movie item missing file or folder: {item.log_string}")
        elif isinstance(item, Episode):
            if item.file and item.folder:
                logger.log("DEBRID", f"Downloaded {item.log_string} with file: {item.file}")
            else:
                logger.debug(f"Episode item missing file or folder: {item.log_string}")
        elif isinstance(item, Season):
            for episode in item.episodes:
                if episode.file and episode.folder:
                    logger.log("DEBRID", f"Downloaded {episode.log_string} with file: {episode.file}")
                elif not episode.file:
                    logger.debug(f"Episode item missing file: {episode.log_string}")
                elif not episode.folder:
                    logger.debug(f"Episode item missing folder: {episode.log_string}")
        elif isinstance(item, Show):
            for season in item.seasons:
                for episode in season.episodes:
                    if episode.file and episode.folder:
                        logger.log("DEBRID", f"Downloaded {episode.log_string} with file: {episode.file}")
                    elif not episode.file:
                        logger.debug(f"Episode item missing file or folder: {episode.log_string}")
                    elif not episode.folder:
                        logger.debug(f"Episode item missing folder: {episode.log_string}")
        else:
            logger.debug(f"Unknown item type: {item.log_string}")

    def is_cached(self, item: MediaItem) -> bool:
        """Check if item is cached on real-debrid.com"""
        if not item.get("streams", {}):
            return False

        def _chunked(lst: List, n: int) -> Generator[List, None, None]:
            """Yield successive n-sized chunks from lst."""
            for i in range(0, len(lst), n):
                yield lst[i:i + n]

        logger.log("DEBRID", f"Processing {len(item.streams)} streams for {item.log_string}")

        processed_stream_hashes = set()
        filtered_streams = [hash for hash in item.streams if hash and hash not in processed_stream_hashes]
        if not filtered_streams:
            logger.log("NOT_FOUND", f"No streams found from filtering: {item.log_string}")
            return False

        for stream_chunk in _chunked(filtered_streams, 5):
            streams = "/".join(stream_chunk)
            try:
                response = get(f"{RD_BASE_URL}/torrents/instantAvailability/{streams}/", additional_headers=self.auth_headers, response_type=dict)
                if response.is_ok and self._evaluate_stream_response(response.data, processed_stream_hashes, item):
                    return True
            except Exception:
                logger.exception("Error checking cache for streams")

        item.set("streams", {})
        logger.log("NOT_FOUND", f"No wanted cached streams found for {item.log_string} out of {len(filtered_streams)}")
        return False

    def _evaluate_stream_response(self, data, processed_stream_hashes, item):
        """Evaluate the response data from the stream availability check."""
        for stream_hash, provider_list in data.items():
            if stream_hash in processed_stream_hashes or self.hash_cache.is_blacklisted(stream_hash):
                continue
            if not provider_list or not provider_list.get("rd"):
                self.hash_cache.blacklist(stream_hash)
                continue
            processed_stream_hashes.add(stream_hash)
            if self._process_providers(item, provider_list, stream_hash):
                return True
            self.hash_cache.blacklist(stream_hash)
        return False

    def _process_providers(self, item: MediaItem, provider_list: dict, stream_hash: str) -> bool:
        """Process providers for an item"""
        if not provider_list or not stream_hash:
            return False

        # Flatten and sort containers by descending order of file count.
        sorted_containers = sorted(
            (container for containers in provider_list.values() for container in containers),
            key=lambda container: -len(container)
        )

        # Check the instance type once and process accordingly
        if isinstance(item, Movie):
            for container in sorted_containers:
                if self._is_wanted_movie(container, item):
                    item.set("active_stream", {"hash": stream_hash, "files": container, "id": None})
                    return True
        elif isinstance(item, Episode):
            for container in sorted_containers:
                if self._is_wanted_episode(container, item):
                    item.set("active_stream", {"hash": stream_hash, "files": container, "id": None})
                    return True
        elif isinstance(item, Season):
            for container in sorted_containers:
                if self._is_wanted_season(container, item):
                    item.set("active_stream", {"hash": stream_hash, "files": container, "id": None})
                    return True

        # If no cached files were found in any of the containers, return False
        return False

    def _is_wanted_movie(self, container: dict, item: Movie) -> bool:
        """Check if container has wanted files for a movie"""
        if not isinstance(item, Movie):
            logger.error(f"Item is not a Movie instance: {item.log_string}")
            return False

        filenames = sorted(
            (file for file in container.values() if file and file["filesize"] > 2e+8 and splitext(file["filename"].lower())[1] in WANTED_FORMATS),
            key=lambda file: file["filesize"], reverse=True
        )

        if not filenames:
            return False

        for file in filenames:
            if not file or not file.get("filename"):
                continue
            with contextlib.suppress(GarbageTorrent, TypeError):
                parsed_file = parse(file["filename"], remove_trash=True)
                if not parsed_file or not parsed_file.parsed_title:
                    continue
                if parsed_file.type == "movie":
                    item.set("folder", item.active_stream.get("name")) # TODO: to get this and alt_name we need info from the torrent
                    item.set("alternative_folder", item.active_stream.get("alternative_name", None))
                    item.set("file", file["filename"]) # TODO: Does this need to be a dict instead of str to be downloaded?
                    return True        
        return False

    def _is_wanted_episode(self, container: dict, item: Episode) -> bool:
        """Check if container has wanted files for an episode"""
        if not isinstance(item, Episode):
            logger.error(f"Item is not an Episode instance: {item.log_string}")
            return False

        filenames = [
            file for file in container.values()
            if file and file["filesize"] > 4e+7
            and splitext(file["filename"].lower())[1] in WANTED_FORMATS
        ]

        if not filenames:
            return False

        one_season = len(item.parent.parent.seasons) == 1

        for file in filenames:
            if not file or not file.get("filename"):
                continue
            with contextlib.suppress(GarbageTorrent, TypeError):
                parsed_file = parse(file["filename"], remove_trash=True)
                if not parsed_file or not parsed_file.episode or 0 in parsed_file.season:
                    continue
                if item.number in parsed_file.episode and item.parent.number in parsed_file.season:
                    item.set("folder", item.active_stream.get("name"))
                    item.set("alternative_folder", item.active_stream.get("alternative_name"))
                    item.set("file", file["filename"])
                    return True
                elif one_season and item.number in parsed_file.episode:
                    item.set("folder", item.active_stream.get("name"))
                    item.set("alternative_folder", item.active_stream.get("alternative_name"))
                    item.set("file", file["filename"])
                    return True
        return False

    def _is_wanted_season(self, container: dict, item: Season) -> bool:
        """Check if container has wanted files for a season"""
        if not isinstance(item, Season):
            logger.error(f"Item is not a Season instance: {item.log_string}")
            return False

        # Filter and sort files once to improve performance
        filenames = [
            file for file in container.values()
            if file and file["filesize"] > 4e+7 and splitext(file["filename"].lower())[1] in WANTED_FORMATS
        ]

        if not filenames:
            return False

        needed_episodes = {episode.number: episode for episode in item.episodes if episode.state in [States.Indexed, States.Scraped, States.Unknown, States.Failed]}
        one_season = len(item.parent.seasons) == 1

        # Dictionary to hold the matched files for each episode
        matched_files = {}
        season_num = item.number

        # Parse files once and assign to episodes
        for file in filenames:
            if not file or not file.get("filename"):
                continue
            with contextlib.suppress(GarbageTorrent, TypeError):
                parsed_file = parse(file["filename"], remove_trash=True)
                if not parsed_file or not parsed_file.episode or 0 in parsed_file.season:
                    continue
                # Check if the file's season matches the item's season or if there's only one season
                if season_num in parsed_file.season:
                    for ep_num in parsed_file.episode:
                        if ep_num in needed_episodes:
                            matched_files[ep_num] = file["filename"]
                elif one_season:
                    for ep_num in parsed_file.episode:
                        if ep_num in needed_episodes:
                            matched_files[ep_num] = file["filename"]
        if not matched_files:
            return False

        # Check if all needed episodes are captured (or atleast half)
        if (needed_episodes.keys() == matched_files.keys()) or (len(matched_files) >= len(needed_episodes) // 2):
            # Set the necessary attributes for each episode
            for ep_num, filename in matched_files.items():
                ep = needed_episodes[ep_num]
                ep.set("folder", item.active_stream.get("name"))
                ep.set("alternative_folder", item.active_stream.get("alternative_name"))
                ep.set("file", filename)
            return True
        return False

    def _is_downloaded(self, item: MediaItem) -> bool:
        """Check if item is already downloaded after checking if it was cached"""
        hash_key = item.active_stream.get("hash", None)
        if not hash_key:
            logger.log("DEBRID", f"Item missing hash, skipping check: {item.log_string}")
            return False

        if self.hash_cache.is_blacklisted(hash_key):
            logger.log("DEBRID", f"Skipping download check for blacklisted hash: {hash_key}")
            return False

        if self.hash_cache.is_downloaded(hash_key) and item.active_stream.get("id", None):
            logger.log("DEBRID", f"Item already downloaded for hash: {hash_key}")
            return True

        logger.debug(f"Checking if torrent is already downloaded for item: {item.log_string}")
        torrents = self.get_torrents(1000)
        torrent = torrents.get(hash_key)

        if not torrent:
            logger.debug(f"No matching torrent found for hash: {hash_key}")
            return False

        if item.active_stream.get("id", None):
            logger.debug(f"Item already has an active stream ID: {item.active_stream.get('id')}")
            return True

        info = self.get_torrent_info(torrent.id)
        if not info:
            logger.debug(f"Failed to get torrent info for ID: {torrent.id}")
            self.hash_cache.blacklist(torrent.hash)
            return False

        if not _matches_item(info, item):
            self.hash_cache.blacklist(torrent.hash)
            return False

        # Cache this as downloaded
        logger.debug(f"Marking torrent as downloaded for hash: {torrent.hash}")
        self.hash_cache.mark_downloaded(torrent.hash)
        item.set("active_stream.id", torrent.id)
        self.set_active_files(item)
        logger.debug(f"Set active files for item: {item.log_string} with {len(item.active_stream.get('files', {}))} total files")
        return True

    def _download_item(self, item: MediaItem):
        """Download item from real-debrid.com"""
        logger.debug(f"Starting download for item: {item.log_string}")
        request_id = self.add_magnet(item) # uses item.active_stream.hash
        logger.debug(f"Magnet added to Real-Debrid, request ID: {request_id} for {item.log_string}")
        item.set("active_stream.id", request_id)
        self.set_active_files(item)
        logger.debug(f"Active files set for item: {item.log_string} with {len(item.active_stream.get('files', {}))} total files")
        time.sleep(0.5)
        self.select_files(request_id, item)
        logger.debug(f"Files selected for request ID: {request_id} for {item.log_string}")
        self.hash_cache.mark_downloaded(item.active_stream["hash"])
        logger.debug(f"Item marked as downloaded: {item.log_string}")

    def set_active_files(self, item: MediaItem) -> None:
        """Set active files for item from real-debrid.com"""
        active_stream = item.get("active_stream")
        if not active_stream or "id" not in active_stream:
            logger.error(f"Invalid active stream data for item: {item.log_string}")
            return

        info = self.get_torrent_info(active_stream["id"])
        if not info:
            logger.error(f"Failed to get torrent info for item: {item.log_string}")
            return

        item.active_stream["alternative_name"] = getattr(info, "original_filename", None)
        item.active_stream["name"] = getattr(info, "filename", None)

        if not item.folder or not item.alternative_folder:
            item.set("folder", item.active_stream.get("name"))
            item.set("alternative_folder", item.active_stream.get("alternative_name"))
        
        # this is only for Movie and Episode instances
        if isinstance(item, (Movie, Episode)):
            if not item.folder or not item.alternative_folder or not item.file:
                logger.error(f"Missing folder or alternative_folder or file for item: {item.log_string}")
                return
            
        if isinstance(item, Season) and item.folder:
            for episode in item.episodes:
                if episode.file and not episode.folder:
                    episode.set("folder", item.folder)

    def _is_wanted_item(self, item: Union[Movie, Episode, Season]) -> bool:
        """Check if item is wanted"""
        if isinstance(item, Movie):
            return self._is_wanted_movie(item.active_stream.get("files", {}), item)
        elif isinstance(item, Season):
            return self._is_wanted_season(item.active_stream.get("files", {}), item)
        elif isinstance(item, Episode):
            return self._is_wanted_episode(item.active_stream.get("files", {}), item)
        else:
            logger.error(f"Unsupported item type: {type(item).__name__}")
            return False


    ### API Methods for Real-Debrid below

    def add_magnet(self, item: MediaItem) -> str:
        """Add magnet link to real-debrid.com"""
        if not item.active_stream.get("hash"):
            logger.error(f"No active stream or hash found for {item.log_string}")
            return None

        try:
            hash = item.active_stream.get("hash")
            response = post(
                f"{RD_BASE_URL}/torrents/addMagnet",
                {"magnet": f"magnet:?xt=urn:btih:{hash}&dn=&tr="},
                additional_headers=self.auth_headers,
            )
            if response.is_ok:
                return response.data.id
            logger.error(f"Failed to add magnet: {response.data}")
        except Exception as e:
            logger.error(f"Error adding magnet for {item.log_string}: {e}")
        return None

    def get_torrent_info(self, request_id: str) -> dict:
        """Get torrent info from real-debrid.com"""
        if not request_id:
            logger.error("No request ID found")
            return {}

        try:
            response = get(
                f"{RD_BASE_URL}/torrents/info/{request_id}", 
                additional_headers=self.auth_headers
            )
            if response.is_ok:
                return response.data
        except Exception as e:
            logger.error(f"Error getting torrent info for {request_id or 'UNKNOWN'}: {e}")
        return {}

    def select_files(self, request_id: str, item: MediaItem) -> bool:
        """Select files from real-debrid.com"""
        files = item.active_stream.get("files")
        # we need to make sure that every file is in our wanted formats
        files = {key: value for key, value in files.items() if splitext(value["filename"].lower())[1] in WANTED_FORMATS}

        if not files:
            logger.error(f"No files to select for {item.log_string}")
            return False

        try:
            response = post(
                f"{RD_BASE_URL}/torrents/selectFiles/{request_id}",
                {"files": ",".join(files.keys())},
                additional_headers=self.auth_headers,
            )
            return response.is_ok
        except Exception as e:
            logger.error(f"Error selecting files for {item.log_string}: {e}")
            return False

    def get_torrents(self, limit: int) -> dict[str, SimpleNamespace]:
        """Get torrents from real-debrid.com"""
        try:
            response = get(
                f"{RD_BASE_URL}/torrents?limit={str(limit)}",
                additional_headers=self.auth_headers
            )
            if response.is_ok and response.data:
                # Example response.data: 
                # namespace(id='JXQWAQ5GPXJWG', filename='Kill Bill - The Whole Bloody Affair (2011) Reconstruction (1080p BluRay HEVC x265 10bit AC3 5.1)[DHB].mkv', hash='5336e4e408378d70593f3ec7ed7abf15480acedb', bytes=17253577745, host='real-debrid.com', split=2000, progress=100, status='downloaded', added='2024-06-01T15:18:44.000Z', links=['https://real-debrid.com/d/TWJXDFV2XS2T737NMKH4HISF24'], ended='2023-05-21T15:52:34.000Z')
                return {torrent.hash: torrent for torrent in response.data}
        except Exception as e:
            logger.error(f"Error getting torrents from Real-Debrid: {e}")
        return {}


## Helper functions for Real-Debrid below


def _matches_item(torrent_info: SimpleNamespace, item: MediaItem) -> bool:
    """Check if the downloaded torrent matches the item specifics."""
    logger.debug(f"Checking if torrent matches item: {item.log_string}")

    def check_movie():
        for file in torrent_info.files:
            if file.selected == 1 and file.bytes > 200_000_000:
                file_size_mb = file.bytes / (1024 * 1024)
                if file_size_mb >= 1024:
                    file_size_gb = file_size_mb / 1024
                    logger.debug(f"Selected file: {Path(file.path).name} with size: {file_size_gb:.2f} GB")
                else:
                    logger.debug(f"Selected file: {Path(file.path).name} with size: {file_size_mb:.2f} MB")
                return True
        return False

    def check_episode():
        one_season = len(item.parent.parent.seasons) == 1
        item_number = item.number
        parent_number = item.parent.number
        for file in torrent_info.files:
            if file.selected == 1:
                file_episodes = extract_episodes(Path(file.path).name)
                if (item_number in file_episodes and parent_number in file_episodes) or (one_season and item_number in file_episodes):
                    logger.debug(f"File {Path(file.path).name} selected for episode {item_number} in season {parent_number}")
                    return True
        return False

    def check_season():
        season_number = item.number
        episodes_in_season = {episode.number for episode in item.episodes}
        matched_episodes = set()
        one_season = len(item.parent.seasons) == 1
        for file in torrent_info.files:
            if file.selected == 1:
                file_episodes = extract_episodes(Path(file.path).name)
                if season_number in file_episodes:
                    matched_episodes.update(file_episodes)
                elif one_season and file_episodes:
                    matched_episodes.update(file_episodes)
        return len(matched_episodes) >= len(episodes_in_season) // 2

    if isinstance(item, Movie):
        if check_movie():
            logger.info(f"Movie {item.log_string} already exists in Real-Debrid account.")
            return True
    elif isinstance(item, Episode):
        if check_episode():
            logger.info(f"Episode {item.log_string} already exists in Real-Debrid account.")
            return True
    elif isinstance(item, Season):
        if check_season():
            logger.info(f"Season {item.log_string} already exists in Real-Debrid account.")
            return True

    logger.debug(f"No matching item found for {item.log_string}")
    return False
