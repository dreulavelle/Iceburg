import contextlib
from datetime import datetime
from posixpath import splitext
from typing import Generator
from pathlib import Path
from RTN import parse
from RTN.exceptions import GarbageTorrent
from requests import ConnectTimeout

from program.media.state import States
from program.media.item import MediaItem
from program.settings.manager import settings_manager
from RTN import parse
from RTN.exceptions import GarbageTorrent
from utils.logger import logger
from utils.request import get, post

API_URL = "https://api.torbox.app/v1/api"
WANTED_FORMATS = {".mkv", ".mp4", ".avi"}


class TorBoxDownloader:
    """TorBox Downloader"""

    def __init__(self, hash_cache):
        self.key = "torbox_downloader"
        self.settings = settings_manager.settings.downloaders.torbox
        self.api_key = self.settings.api_key
        self.base_url = "https://api.torbox.app/v1/api"
        self.headers = {"Authorization": f"Bearer {self.api_key}"}
        self.hash_cache = hash_cache
        self.initialized = self.validate()
        if not self.initialized:
            return
        logger.success("TorBox Downloader initialized!")

    def validate(self) -> bool:
        """Validate the TorBox Downloader as a service"""
        if not self.settings.enabled:
            logger.info("Torbox downloader is not enabled")
            return False
        if not self.settings.api_key:
            logger.error("Torbox API key is not set")
        try:
            response = get(f"{self.base_url}/user/me", additional_headers=self.headers)
            if response.is_ok:
                user_info = response.data.data
                expiration = user_info.premium_expires_at
                expiration_date_time = datetime.fromisoformat(expiration)
                expiration_date_time.replace(tzinfo=None)
                delta = expiration_date_time - datetime.now().replace(
                    tzinfo=expiration_date_time.tzinfo
                )

                if delta.days > 0:
                    expiration_message = f"Your account expires in {delta.days} days."
                else:
                    expiration_message = "Your account expires soon."

                if user_info.plan == 0:
                    logger.error("You are not a premium member.")
                    return False
                else:
                    logger.log("DEBRID", expiration_message)

                return user_info.plan != 0
        except ConnectTimeout:
            logger.error("Connection to All-Debrid timed out.")
        except Exception as e:
            logger.exception(f"Failed to validate Torbox settings: {e}")
        return False

    def run(self, item: MediaItem) -> Generator[MediaItem, None, None]:
        """Download media item from torbox.app"""
        cached_hashes = self.get_torrent_cached([hash for hash in item.streams])
        if cached_hashes:
            for cache in cached_hashes.values():
                item.active_stream = cache
                if self.find_required_files(item, cache["files"]):
                    logger.log(
                        "DEBRID", f"Item is cached, proceeding with: {item.log_string}"
                    )
                    item.set(
                        "active_stream",
                        {"hash": cache["hash"], "files": cache["files"], "id": None},
                    )
                    self.download(item)
                    break
                else:
                    self.hash_cache.blacklist(cache["hash"])
        else:
            logger.log("DEBRID", f"Item is not cached: {item.log_string}")
            for hash in item.streams:
                logger.log(
                    "DEBUG", f"Blacklisting hash ({hash}) for item: {item.log_string}"
                )
                self.hash_cache.blacklist(hash)
            item.streams = {}
        yield item

    def find_required_files(self, item, container):

        files = [
            file
            for file in container
            if file
            and file["size"] > 10000
            and splitext(file["name"].lower())[1] in WANTED_FORMATS
        ]

        if item.type == "movie":
            for file in files:
                parsed_file = parse(file["name"], remove_trash=True)
                if parsed_file.type == "movie":
                    return [file]
        if item.type == "show":
            # Create a dictionary to map seasons and episodes needed
            needed_episodes = {}
            acceptable_states = [
                States.Indexed,
                States.Scraped,
                States.Unknown,
                States.Failed,
            ]

            for season in item.seasons:
                if season.state in acceptable_states and season.is_released_nolog:
                    needed_episode_numbers = {
                        episode.number
                        for episode in season.episodes
                        if episode.state in acceptable_states
                        and episode.is_released_nolog
                    }
                    if needed_episode_numbers:
                        needed_episodes[season.number] = needed_episode_numbers
            if not needed_episodes:
                return False

            # Iterate over each file to check if it matches
            # the season and episode within the show
            matched_files = []
            for file in files:
                with contextlib.suppress(GarbageTorrent, TypeError):
                    parsed_file = parse(file["name"], remove_trash=True)
                    if (
                        not parsed_file
                        or not parsed_file.parsed_title
                        or 0 in parsed_file.season
                    ):
                        continue
                    # Check each season and episode to find a match
                    for season_number, episodes in needed_episodes.items():
                        if season_number in parsed_file.season:
                            for episode_number in list(episodes):
                                if episode_number in parsed_file.episode:
                                    # Store the matched file for this episode
                                    matched_files.append(file)
                                    episodes.remove(episode_number)
            if not matched_files:
                return False

            if all(len(episodes) == 0 for episodes in needed_episodes.values()):
                return matched_files
        if item.type == "season":
            needed_episodes = {
                episode.number: episode
                for episode in item.episodes
                if episode.state
                in [States.Indexed, States.Scraped, States.Unknown, States.Failed]
            }
            one_season = len(item.parent.seasons) == 1

            # Dictionary to hold the matched files for each episode
            matched_files = []
            season_num = item.number

            # Parse files once and assign to episodes
            for file in files:
                if not file or not file.get("name"):
                    continue
                with contextlib.suppress(GarbageTorrent, TypeError):
                    parsed_file = parse(file["name"], remove_trash=True)
                    if (
                        not parsed_file
                        or not parsed_file.episode
                        or 0 in parsed_file.season
                    ):
                        continue
                    # Check if the file's season matches the item's season or if there's only one season
                    if season_num in parsed_file.season:
                        for ep_num in parsed_file.episode:
                            if ep_num in needed_episodes:
                                matched_files.append(file)
                    elif one_season:
                        for ep_num in parsed_file.episode:
                            if ep_num in needed_episodes:
                                matched_files.append(file)
            if not matched_files:
                return False

            # Check if all needed episodes are captured (or atleast half)
            if len(needed_episodes) == len(matched_files):
                return matched_files
        if item.type == "episode":
            for file in files:
                if not file or not file.get("name"):
                    continue
                with contextlib.suppress(GarbageTorrent, TypeError):
                    parsed_file = parse(file["name"], remove_trash=True)
                    if (
                        item.number in parsed_file.episode
                        and item.parent.number in parsed_file.season
                    ):
                        return [file]

        return []

    def download(self, item: MediaItem):
        # Check if the torrent already exists
        exists = False
        torrent_list = self.get_torrent_list()
        for torrent in torrent_list:
            if item.active_stream["hash"] == torrent["hash"]:
                id = torrent["id"]
                exists = True
                break

        # If it doesnt, lets download it and refresh the torrent_list
        if not exists:
            id = self.create_torrent(item.active_stream["hash"])
            torrent_list = self.get_torrent_list()

        # Find the torrent, correct file and we gucci
        for torrent in torrent_list:
            if torrent["id"] == id:
                if item.type == "movie":
                    file = self.find_required_files(item, item.active_stream["files"])[
                        0
                    ]
                    _file_path = Path(file["name"])
                    item.set("folder", _file_path.parent.name)
                    item.set("alternative_folder", ".")
                    item.set("file", _file_path.name)
                if item.type == "show":
                    files = self.find_required_files(item, item.active_stream["files"])
                    for season in item.seasons:
                        for episode in season.episodes:
                            file = self.find_required_files(episode, files)[0]
                            _file_path = Path(file["name"])
                            episode.set("folder", _file_path.parent.name)
                            episode.set("alternative_folder", ".")
                            episode.set("file", _file_path.name)
                if item.type == "season":
                    files = self.find_required_files(item, item.active_stream["files"])
                    for episode in item.episodes:
                        file = self.find_required_files(episode, files)[0]
                        _file_path = Path(file["name"])
                        episode.set("folder", _file_path.parent.name)
                        episode.set("alternative_folder", ".")
                        episode.set("file", _file_path.name)
                if item.type == "episode":
                    file = self.find_required_files(episode, files)[0]
                    _file_path = Path(file["name"])
                    item.set("folder", _file_path.parent.name)
                    item.set("alternative_folder", ".")
                    item.set("file", _file_path.name)
                logger.log("DEBRID", f"Downloaded {item.log_string}")

    def get_torrent_cached(self, hash_list):
        hash_string = ",".join(hash_list)
        response = get(
            f"{self.base_url}/torrents/checkcached?hash={hash_string}&list_files=True",
            additional_headers=self.headers,
            response_type=dict,
        )
        return response.data["data"]

    def create_torrent(self, hash) -> int:
        magnet_url = f"magnet:?xt=urn:btih:{hash}"
        response = post(
            f"{self.base_url}/torrents/createtorrent",
            data={"magnet": magnet_url, "seed": 1, "allow_zip": False},
            additional_headers=self.headers,
        )
        return response.data.data.torrent_id

    def get_torrent_list(self) -> list:
        response = get(
            f"{self.base_url}/torrents/mylist?bypass_cache=true",
            additional_headers=self.headers,
            response_type=dict,
        )
        return response.data["data"]
