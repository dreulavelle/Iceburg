"""Realdebrid module"""
import time
from pathlib import Path
from requests import ConnectTimeout
from utils.logger import logger
from utils.request import get, post, ping
from program.settings.manager import settings_manager
from utils.parser import parser
from program.media.item import Season, Movie, Episode


WANTED_FORMATS = [".mkv", ".mp4", ".avi"]
RD_BASE_URL = "https://api.real-debrid.com/rest/1.0"


class Debrid:
    """Real-Debrid API Wrapper"""

    def __init__(self):
        # Realdebrid class library is a necessity
        self.initialized = False
        self.key = "real_debrid"
        self.settings = settings_manager.settings.real_debrid
        self.auth_headers = {"Authorization": f"Bearer {self.settings.api_key}"}
        self.running = False
        if not self._validate():
            logger.error("Realdebrid settings incorrect or not premium!")
            return
        logger.info("Real Debrid initialized!")
        self.processed_torrents = set()
        self.initialized = True

    def _validate(self):
        try:
            response = ping(
                f"{RD_BASE_URL}/user",
                additional_headers=self.auth_headers,
            )
            if response.ok:
                json = response.json()
                return json["premium"] > 0
        except ConnectTimeout:
            return False

    def run(self, item):
        """Download movie from real-debrid.com"""
        if not self.is_cached(item):
            return
        if not self._is_downloaded(item):
            self._download_item(item)
        self._set_file_paths(item)
        yield item


    def _is_downloaded(self, item):
        """Check if item is already downloaded"""
        torrents = self.get_torrents()
        for torrent in torrents:
            if torrent.hash == item.active_stream.get("hash"):
                info = self.get_torrent_info(torrent.id)
                if item.type == "episode":
                    if not any(
                        file
                        for file in info.files
                        if file.selected == 1
                        and item.number
                        in parser.episodes_in_season(
                            item.parent.number, Path(file.path).name
                        )
                    ):
                        return False

                item.set("active_stream.id", torrent.id)
                self.set_active_files(item)
                logger.debug("Torrent for %s already downloaded", item.log_string)
                return True
        return False

    def _download_item(self, item):
        """Download item from real-debrid.com"""
        request_id = self.add_magnet(item)
        item.set("active_stream.id", request_id)
        self.set_active_files(item)
        time.sleep(0.5)
        self.select_files(request_id, item)
        item.set("active_stream.id", request_id)
        logger.debug("Downloaded %s", item.log_string)

    def set_active_files(self, item):
        """Set active files for item from real-debrid.com"""
        info = self.get_torrent_info(item.get("active_stream")["id"])
        item.active_stream["alternative_name"] = info.original_filename
        item.active_stream["name"] = info.filename

    def is_cached(self, item):
        """Check if item is cached on real-debrid.com"""
        if len(item.streams) == 0:
            return

        def chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i : i + n]

        filtered_streams = [hash for hash in item.streams if hash is not None]
        stream_chunks = list(chunks(filtered_streams, 5))

        for stream_chunk in stream_chunks:
            streams = "/".join(stream_chunk)
            response = get(
                f"{RD_BASE_URL}/torrents/instantAvailability/{streams}/",
                additional_headers=self.auth_headers,
                response_type=dict,
            )
            for stream_hash, provider_list in response.data.items():
                if len(provider_list) == 0:
                    continue
                for containers in provider_list.values():
                    for container in containers:
                        wanted_files = {}
                        if isinstance(item, Movie) and all(file["filesize"] > 200000 for file in container.values()):
                            wanted_files = container
                        if isinstance(item, Season) and all(any(episode.number in parser.episodes_in_season(item.number, file["filename"]) for file in container.values()) for episode in item.episodes):
                            wanted_files = container
                        if isinstance(item, Episode) and any(item.number in parser.episodes_in_season(item.parent.number, episode["filename"]) for episode in container.values()):
                            wanted_files = container
                        if len(wanted_files) > 0 and all(item for item in wanted_files.values() if Path(item["filename"]).suffix in WANTED_FORMATS):
                            item.set(
                                "active_stream",
                                {"hash": stream_hash, "files": wanted_files, "id": None},
                            )
                            return True
                    item.streams[stream_hash] = None
        logger.debug("[%s] No cached streams found for item: %s", stream_hash[-6:], item.log_string)
        return False

    def _set_file_paths(self, item):
        """Set file paths for item from real-debrid.com"""
        if isinstance(item, Movie):
            self._handle_movie_paths(item)
        elif isinstance(item, Season):
            self._handle_season_paths(item)
        elif isinstance(item, Episode):
            self._handle_episode_paths(item)

    def _handle_movie_paths(self, item):
        """Set file paths for movie from real-debrid.com"""
        item.set("folder", item.active_stream.get("name"))
        item.set("alternative_folder", item.active_stream.get("alternative_name"))
        item.set("file", next(file for file in item.active_stream.get("files").values())["filename"])

    def _handle_season_paths(self, season):
        """Set file paths for season from real-debrid.com"""
        for file in season.active_stream["files"].values():
            for episode in parser.episodes_in_season(season.number, file["filename"]):
                if episode - 1 in range(len(season.episodes)):
                    season.episodes[episode - 1].set(
                        "folder", season.active_stream.get("name")
                    )
                    season.episodes[episode - 1].set(
                        "alternative_folder", season.active_stream.get("alternative_name")
                    )
                    season.episodes[episode - 1].set("file", file["filename"])

    def _handle_episode_paths(self, episode):
        """Set file paths for episode from real-debrid.com"""
        file = next(file for file in episode.active_stream.get("files").values() if episode.number in parser.episodes_in_season(episode.parent.number, file["filename"]))
        episode.set("folder", episode.active_stream.get("name"))
        episode.set("alternative_folder", episode.active_stream.get("alternative_name"))
        episode.set("file", file["filename"])

    def add_magnet(self, item) -> str:
        """Add magnet link to real-debrid.com"""
        if not item.active_stream.get("hash"):
            return None
        response = post(
            f"{RD_BASE_URL}/torrents/addMagnet",
            {
                "magnet": "magnet:?xt=urn:btih:"
                + item.active_stream["hash"]
                + "&dn=&tr="
            },
            additional_headers=self.auth_headers,
        )
        if response.is_ok:
            return response.data.id
        return None

    def get_torrents(self) -> str:
        """Add magnet link to real-debrid.com"""
        response = get(
            f"{RD_BASE_URL}/torrents/",
            data={"offset": 0, "limit": 2500},
            additional_headers=self.auth_headers,
        )
        if response.is_ok:
            return response.data
        return None

    def select_files(self, request_id, item) -> bool:
        """Select files from real-debrid.com"""
        files = item.active_stream.get("files")
        response = post(
            f"{RD_BASE_URL}/torrents/selectFiles/{request_id}",
            {"files": ",".join(files.keys())},
            additional_headers=self.auth_headers,
        )
        return response.is_ok

    def get_torrent_info(self, request_id):
        """Get torrent info from real-debrid.com"""
        response = get(
            f"{RD_BASE_URL}/torrents/info/{request_id}",
            additional_headers=self.auth_headers,
        )
        if response.is_ok:
            return response.data