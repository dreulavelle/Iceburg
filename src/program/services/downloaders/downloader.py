from datetime import datetime, timedelta
from queue import Queue
import time
from typing import List, Optional, Union
from loguru import logger

from program.media.item import MediaItem, Show, Season, Episode, Movie
from program.media.state import States
from program.media.stream import Stream
from program.settings.manager import settings_manager
from program.services.downloaders.shared import parse_filename
from program.services.downloaders.models import (
    DebridFile, DownloadStatus, ParsedFileData, TorrentContainer, TorrentInfo,
    DownloadedTorrent, NoMatchingFilesException, NotCachedException
)

from .alldebrid import AllDebridDownloader
from .realdebrid import RealDebridDownloader
from .torbox import TorBoxDownloader
import program.db.db_functions as db_functions


class Downloader:
    def __init__(self):
        self.key = "downloader"
        self.initialized = False
        self.services = {
            RealDebridDownloader: RealDebridDownloader(),
            TorBoxDownloader: TorBoxDownloader(),
            AllDebridDownloader: AllDebridDownloader()
        }
        self.service = next((service for service in self.services.values() if service.initialized), None)
        self.concurrent_downloads = []
        self.concurrent_limit = self.service.concurrent_download_limit
        self.initialized = self.validate()

    def validate(self):
        if self.service is None:
            logger.error(
                "No downloader service is initialized. Please initialize a downloader service."
            )
            return False
        self.concurrent_downloads = [item.id for item in db_functions.get_items_by_state(States.Downloading)] 
        return True

    def run(self, item: MediaItem):
        logger.debug(f"Starting download process for {item.log_string} ({item.id})")

        if item.state == States.Downloaded:
            logger.debug(f"Skipping {item.log_string} ({item.id}) as it has already been downloaded by another download session")
            return

        run_at = None

        # Uncached stuff
        if item.last_state == States.Downloading:
            if len(self.concurrent_downloads > self.concurrent_limit):
                logger.debug(f"Looks like we are short on downloading slots, trying again in 10 minutes...")
                run_at = datetime.now() + timedelta(minutes=10)
            else:
                try:
                    # Due to pydantic limitation we need to instantiate a new active_stream
                    item.active_stream = DownloadedTorrent(**item.active_stream.model_dump(mode="json"))

                    # Step 1 - Add Torrent
                    if not item.active_stream.id:
                        logger.debug(f"Starting uncached download for {item.log_string}")
                        item.active_stream.id = self.add_torrent(item.active_stream.infohash)
                        self.concurrent_downloads.append(item.id)
                        run_at = datetime.now() + timedelta(minutes=1)
                    else:
                        # Step 2 - Update the torrent information
                        item.active_stream.info = self.get_torrent_info(item.active_stream.id, item.type)

                        if item.active_stream.info.status == DownloadStatus.WAITING_FOR_USER:
                            logger.debug(f"Selected files for download of {item.log_string}...")
                            self.select_files(item.active_stream.id, [file.file_id for file in item.active_stream.info.files])

                        if item.active_stream.info.status == DownloadStatus.READY:
                            logger.debug(f"Looks like {item.log_string} is ready, lets complete it.")
                            the_container = TorrentContainer(infohash = item.active_stream.infohash, files=item.active_stream.info.files)
                            the_container = self.validate_container(the_container, item.type)
                            item.active_stream.container = the_container
                            if self.update_item_attributes(item, item.active_stream):
                                logger.log("DEBRID", f"Downloaded {item.log_string}")
                                self.concurrent_downloads.remove(item.id)

                        # Step 3 - Denial ;)
                        elif datetime.now() > item.active_stream.downloaded_at + timedelta(minutes=5):
                            logger.debug(f"Item {item.log_string} doesnt have enough seeders, lets try another...")
                            raise Exception(f"Not enough seeders for {item.log_string}")

                        elif item.active_stream.info.status == DownloadStatus.QUEUE:
                            logger.debug(f"Item {item.log_string} is still in downloading queue. Lets try again in a minute...")
                            run_at = datetime.now() + timedelta(minutes=1)

                        elif item.active_stream.info.status == DownloadStatus.DOWNLOADING:
                            # We could do some fancy calculating here, maybe even introduce a time ceiling
                            logger.debug(f"Item {item.log_string} is still downloading. Lets try again in a minute...")
                            run_at = datetime.now() + timedelta(minutes=1)

                        elif item.active_stream.info.status == DownloadStatus.ERROR:
                            raise Exception(f"Debrid service is reporting error for {item.log_string}...")

                        else:
                            raise Exception("WE DONT HAVE A DOWNLOADSTATUS, WTF!")

                except Exception as e:
                    logger.debug(f"Stream {item.active_stream.infohash} failed: {e}")
                    item.blacklist_active_stream()
                    if item.active_stream.id:
                        self.service.delete_torrent(item.active_stream.id)
                    self.concurrent_downloads.remove(item.id)
                    item.active_stream = DownloadedTorrent(infohash=item.streams[0].infohash)

        # Cached stuff
        else:
            for index, stream in enumerate(item.streams):
                try:
                    if index > settings_manager.settings.downloaders.uncached_after:
                        item.active_stream = DownloadedTorrent(infohash=item.streams[0].infohash)
                        break
                    else:
                        container = self.get_instant_availability(stream.infohash, item.type)
                        the_container = self.validate_container(container, item.type)
                        download_result = self.download_stream(stream, the_container)
                        if self.update_item_attributes(item, download_result):
                            logger.log("DEBRID", f"Downloaded cached {item.log_string} from '{stream.raw_title}' [{stream.infohash}]")
                            item.active_stream = download_result
                            break
                except Exception as e:
                    logger.debug(f"Stream {stream.infohash} failed: {e}")
                    item.blacklist_stream(stream)
                    if item.active_stream.id:
                        self.service.delete_torrent(download_result.id)

        if run_at:
            yield (item, run_at)
        else:
            yield item

    def validate_container(self, container: TorrentContainer, item_type: str) -> Optional[TorrentContainer]:
        """
        Validate a single container by ensuring its files match the item's requirements.
        """
        valid_files = []
        for file in container.files or []:
            debrid_file = DebridFile.create(
                filename=file.filename,
                filesize_bytes=file.filesize,
                filetype=item_type,
                file_id=file.file_id
            )
            if debrid_file:
                valid_files.append(debrid_file)

        if valid_files:
            container.files = valid_files
            return container

        raise Exception("Stream container does not contain required files")

    def update_item_attributes(self, item: MediaItem, download_result: DownloadedTorrent) -> bool:
        """Update the item attributes with the downloaded files and active stream."""
        found = False
        for file in download_result.container.files:
            file_data: ParsedFileData = parse_filename(file.filename)
            if self.match_file_to_item(item, file_data, file, download_result):
                found = True
                if item.type in ["movie", "episode"]:
                    return found
        if not found:
            raise NoMatchingFilesException(f"No valid files found for stream {download_result.infohash}")

        return found

    def match_file_to_item(self, item: MediaItem, file_data: ParsedFileData, file: DebridFile, download_result: DownloadedTorrent) -> bool:
        """Check if the file matches the item and update attributes."""
        found = False
        if item.type == "movie" and file_data.item_type == "movie":
            self._update_attributes(item, file, download_result)
            return True

        if item.type in ("show", "season", "episode"):
            if not (file_data.season and file_data.episodes):
                return False

            show: Show = item if item.type == "show" else (item.parent if item.type == "season" else item.parent.parent)
            season: Season = next((season for season in show.seasons if season.number == file_data.season), None)
            for file_episode in file_data.episodes:
                episode: Episode = next((episode for episode in season.episodes if episode.number == file_episode), None)
                if episode and episode.state not in [States.Completed, States.Symlinked, States.Downloaded]:
                    self._update_attributes(episode, file, download_result)
                    episode.store_state()
                    found = True

        return found

    def download_stream(self, stream: Stream, container: TorrentContainer) -> DownloadedTorrent:
        """Download a cached stream"""
        torrent_id: int = self.add_torrent(stream.infohash)
        info: TorrentInfo = self.get_torrent_info(torrent_id)
        if container.file_ids:
            self.select_files(torrent_id, container.file_ids)
        return DownloadedTorrent(id=torrent_id, info=info, infohash=stream.infohash, container=container)

    def _update_attributes(self, item: Union[Movie, Episode], debrid_file: DebridFile, download_result: DownloadedTorrent) -> None:
        """Update the item attributes with the downloaded files and active stream"""
        item.file = debrid_file.filename
        item.folder = download_result.info.name
        item.alternative_folder = download_result.info.alternative_filename
        item.active_stream = download_result

    def get_instant_availability(self, infohash: str, item_type: str) -> List[TorrentContainer]:
        """Check if the torrent is cached"""
        container = self.service.get_instant_availability(infohash, item_type)
        if not container:
            raise Exception(f"Stream {infohash} is not cached")
        return container

    def add_torrent(self, infohash: str) -> int:
        """Add a torrent by infohash"""
        return self.service.add_torrent(infohash)

    def get_torrent_info(self, torrent_id: int, item_type: str = None) -> TorrentInfo:
        """Get information about a torrent"""
        return self.service.get_torrent_info(torrent_id, item_type)

    def select_files(self, torrent_id: int, container: list[str]) -> None:
        """Select files from a torrent"""
        self.service.select_files(torrent_id, container)

    def delete_torrent(self, torrent_id: int) -> None:
        """Delete a torrent"""
        self.service.delete_torrent(torrent_id)
