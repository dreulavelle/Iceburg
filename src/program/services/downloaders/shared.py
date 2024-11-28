from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from RTN import ParsedData, parse

from program.services.downloaders.models import (
    ParsedFileData,
    TorrentContainer,
    TorrentInfo,
)
from program.settings.manager import settings_manager


class DownloaderBase(ABC):
    """The abstract base class for all Downloader implementations."""
    PROXY_URL: str = settings_manager.settings.downloaders.proxy_url
    concurrent_download_limit: str

    @abstractmethod
    def validate(self) -> bool:
        """
        Validate the downloader configuration and premium status

        Returns:
            ValidationResult: Contains validation status and any error messages
        """
        pass

    @abstractmethod
    def get_instant_availability(self, infohash: str, item_type: str) -> Optional[TorrentContainer]:
        """
        Get instant availability for a single infohash

        Args:
            infohash: The hash of the torrent to check
            item_type: The type of media item being checked

        Returns:
            Optional[TorrentContainer]: Cached status and available files for the hash, or None if not available
        """
        pass

    @abstractmethod
    def add_torrent(self, infohash: str) -> int:
        """
        Add a torrent and return its information

        Args:
            infohash: The hash of the torrent to add

        Returns:
            str: The ID of the added torrent
        """
        pass

    @abstractmethod
    def select_files(self, request: list[int]) -> None:
        """
        Select which files to download from the torrent

        Args:
            request: File selection details including torrent ID and file IDs
        """
        pass

    @abstractmethod
    def get_torrent_info(self, torrent_id: str, item_type: str = None) -> TorrentInfo:
        """
        Get information about a specific torrent using its ID

        Args:
            torrent_id: ID of the torrent to get info for
            item_type: item type as string

        Returns:
            TorrentInfo: Current information about the torrent
        """
        pass

    @abstractmethod
    def delete_torrent(self, torrent_id: str) -> None:
        """
        Delete a torrent from the service

        Args:
            torrent_id: ID of the torrent to delete
        """
        pass


def parse_filename(filename: str) -> ParsedFileData:
    """Parse a filename into a ParsedFileData object"""
    parsed_data: ParsedData = parse(filename)
    season: int | None = parsed_data.seasons[0] if parsed_data.seasons else None
    return ParsedFileData(item_type=parsed_data.type, season=season, episodes=parsed_data.episodes)


def premium_days_left(expiration: datetime) -> str:
    """Convert an expiration date into a message showing days remaining on the user's premium account"""
    time_left = expiration - datetime.utcnow()
    days_left = time_left.days
    hours_left, minutes_left = divmod(time_left.seconds // 3600, 60)
    expiration_message = ""

    if days_left > 0:
        expiration_message = f"Your account expires in {days_left} days."
    elif hours_left > 0:
        expiration_message = (
            f"Your account expires in {hours_left} hours and {minutes_left} minutes."
        )
    else:
        expiration_message = "Your account expires soon."
    return expiration_message
