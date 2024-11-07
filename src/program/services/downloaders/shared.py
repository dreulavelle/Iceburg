from abc import ABC, abstractmethod
from datetime import datetime
from typing import Tuple

from loguru import logger
from RTN import parse

from program.settings.manager import settings_manager

DEFAULT_VIDEO_EXTENSIONS = ["mp4", "mkv", "avi"]
ALLOWED_VIDEO_EXTENSIONS = [
    "mp4",
    "mkv",
    "avi",
    "mov",
    "wmv",
    "flv",
    "m4v",
    "webm",
    "mpg",
    "mpeg",
    "m2ts",
    "ts",
]

VIDEO_EXTENSIONS = (
    settings_manager.settings.downloaders.video_extensions or DEFAULT_VIDEO_EXTENSIONS
)
VIDEO_EXTENSIONS = [ext for ext in VIDEO_EXTENSIONS if ext in ALLOWED_VIDEO_EXTENSIONS]

if not VIDEO_EXTENSIONS:
    VIDEO_EXTENSIONS = DEFAULT_VIDEO_EXTENSIONS

# Type aliases
InfoHash = str  # A torrent hash
DebridTorrentId = (
    str  # Identifier issued by the debrid service for a torrent in their cache
)


class DownloaderBase(ABC):
    """
    The abstract base class for all Downloader implementations.
    """

    @abstractmethod
    def validate():
        pass
    @abstractmethod
    def get_instant_availability():
        pass

    @abstractmethod
    def add_torrent():
        pass

    @abstractmethod
    def select_files():
        pass

    @abstractmethod
    def get_torrent_info():
        pass

    @abstractmethod
    def delete_torrent():
        pass

class FileFinder:
    """
    A class that helps you find files.

    Attributes:
        filename_attr (str): The name of the file attribute.
    """

    def __init__(self, name, size):
        self.filename_attr = name
        self.filesize_attr = size

    def container_file_matches_episode(self, file):
        filename = file[self.filename_attr]
        try:
            parsed_data = parse(filename)
            return parsed_data.seasons[0], parsed_data.episodes
        except Exception:
            return None, None

    def container_file_matches_movie(self, file):
        filename = file[self.filename_attr]
        try:
            parsed_data = parse(filename)
            return parsed_data.type == "movie"
        except Exception:
            return None

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


def hash_from_uri(magnet_uri: str) -> str:
    if len(magnet_uri) == 40:
        # Probably already a hash
        return magnet_uri
    start = magnet_uri.index("urn:btih:") + len("urn:btih:")
    return magnet_uri[start : start + 40]

min_movie_filesize = settings_manager.settings.downloaders.movie_filesize_mb_min
max_movie_filesize = settings_manager.settings.downloaders.movie_filesize_mb_max
min_episode_filesize = settings_manager.settings.downloaders.episode_filesize_mb_min
max_episode_filesize = settings_manager.settings.downloaders.episode_filesize_mb_max
are_filesizes_valid = False

def _validate_filesize_setting(value: int, setting_name: str) -> bool:
    """Validate a single filesize setting."""
    if not isinstance(value, int) or value < -1:
        logger.error(f"{setting_name} is not valid. Got {value}, expected integer >= -1")
        return False
    return True

def _validate_filesizes() -> bool:
    """
    Validate all filesize settings from configuration.
    Returns True if all settings are valid integers >= -1, False otherwise.
    """
    settings = settings_manager.settings.downloaders
    return all([
        _validate_filesize_setting(settings.movie_filesize_mb_min, "Movie filesize min"),
        _validate_filesize_setting(settings.movie_filesize_mb_max, "Movie filesize max"),
        _validate_filesize_setting(settings.episode_filesize_mb_min, "Episode filesize min"),
        _validate_filesize_setting(settings.episode_filesize_mb_max, "Episode filesize max")
    ])

are_filesizes_valid = _validate_filesizes()

BYTES_PER_MB = 1_000_000

def _convert_to_bytes(size_mb: int) -> int:
    """Convert size from megabytes to bytes."""
    return size_mb * BYTES_PER_MB

def _get_size_limits(media_type: str) -> Tuple[int, int]:
    """Get min and max size limits in MB for given media type."""
    settings = settings_manager.settings.downloaders
    if media_type == "movie":
        return (settings.movie_filesize_mb_min, settings.movie_filesize_mb_max)
    return (settings.episode_filesize_mb_min, settings.episode_filesize_mb_max)

def _validate_filesize(filesize: int, media_type: str) -> bool:
    """
    Validate file size against configured limits.
    
    Args:
        filesize: Size in bytes to validate
        media_type: Type of media being validated
        
    Returns:
        bool: True if size is within configured range
    """
    if not are_filesizes_valid:
        logger.error(f"Filesize settings are invalid, {media_type} file sizes will not be checked.")
        return True
        
    min_mb, max_mb = _get_size_limits(media_type)
    min_size = 0 if min_mb == -1 else _convert_to_bytes(min_mb)
    max_size = float("inf") if max_mb == -1 else _convert_to_bytes(max_mb)
    
    is_acceptable = min_size <= filesize <= max_size
    if not is_acceptable:
        logger.debug(f"{media_type} filesize {filesize} bytes is not within acceptable range {min_size} - {max_size} bytes")
    return is_acceptable


def filesize_is_acceptable_movie(filesize: int) -> bool:
    return _validate_filesize(filesize, "movie")

def filesize_is_acceptable_show(filesize: int) -> bool:
    return _validate_filesize(filesize, "show")