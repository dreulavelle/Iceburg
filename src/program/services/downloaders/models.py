from datetime import datetime
from typing import Dict, List, Literal, Optional, Union

from loguru import logger
from pydantic import BaseModel, Field

from program.settings.manager import settings_manager

DEFAULT_VIDEO_EXTENSIONS = ["mp4", "mkv", "avi"]
ALLOWED_VIDEO_EXTENSIONS = [
    "mp4", "mkv", "avi", "mov", "wmv", "flv",
    "m4v", "webm", "mpg","mpeg", "m2ts", "ts",
]

VIDEO_EXTENSIONS: list[str] = settings_manager.settings.downloaders.video_extensions or DEFAULT_VIDEO_EXTENSIONS
VIDEO_EXTENSIONS = [ext for ext in VIDEO_EXTENSIONS if ext in ALLOWED_VIDEO_EXTENSIONS]
if not VIDEO_EXTENSIONS:
    VIDEO_EXTENSIONS = DEFAULT_VIDEO_EXTENSIONS

movie_min_filesize: int = settings_manager.settings.downloaders.movie_filesize_mb_min
movie_max_filesize: int = settings_manager.settings.downloaders.movie_filesize_mb_max
episode_min_filesize: int = settings_manager.settings.downloaders.episode_filesize_mb_min
episode_max_filesize: int = settings_manager.settings.downloaders.episode_filesize_mb_max

# constraints for filesizes, follows the format tuple(min, max)
FILESIZE_MOVIE_CONSTRAINT: tuple[int, int] = (
    movie_min_filesize if movie_min_filesize >= 0 else 0,
    movie_max_filesize if movie_max_filesize > 0 else float("inf")
)
FILESIZE_EPISODE_CONSTRAINT: tuple[int, int] = (
    episode_min_filesize if episode_min_filesize >= 0 else 0,
    episode_max_filesize if episode_max_filesize > 0 else float("inf")
)


class NotCachedException(Exception):
    """Exception raised for torrents that are not cached"""

class NoMatchingFilesException(Exception):
    """Exception raised for torrents that do not match the expected files"""


class DebridFile(BaseModel):
    """Represents a file from a debrid service"""
    file_id: Optional[int] = Field(default=None)
    filename: Optional[str] = Field(default=None)
    filesize: Optional[int] = Field(default=None)

    @classmethod
    def create(
        cls,
        filename: str,
        filesize_bytes: int,
        filetype: Literal["movie", "show", "season", "episode"],
        file_id: Optional[int] = None,
        limit_filesize: bool = True

    ) -> Optional["DebridFile"]:
        """Factory method to validate and create a DebridFile"""
        if not any(filename.endswith(ext) for ext in VIDEO_EXTENSIONS) or "sample" in filename.lower():
            return None

        if limit_filesize:
            filesize_mb = filesize_bytes / 1_000_000
            if filetype == "movie":
                if not (FILESIZE_MOVIE_CONSTRAINT[0] <= filesize_mb <= FILESIZE_MOVIE_CONSTRAINT[1]):
                    return None
            elif filetype in ["show", "season", "episode"]:
                if not (FILESIZE_EPISODE_CONSTRAINT[0] <= filesize_mb <= FILESIZE_EPISODE_CONSTRAINT[1]):
                    return None

        return cls(filename=filename, filesize=filesize_bytes, file_id=file_id)


class ParsedFileData(BaseModel):
    """Represents a parsed file from a filename"""
    item_type: Literal["movie", "show"]
    season: Optional[int] = Field(default=None)
    episodes: Optional[List[int]] = Field(default_factory=list)


class TorrentContainer(BaseModel):
    """Represents a collection of files from an infohash from a debrid service"""
    infohash: str
    files: List[DebridFile] = Field(default_factory=list)

    @property
    def cached(self) -> bool:
        """Check if the torrent is cached"""
        return len(self.files) > 0

    @property
    def file_ids(self) -> List[int]:
        """Get the file ids of the cached files"""
        return [file.file_id for file in self.files if file.file_id is not None]


class TorrentInfo(BaseModel):
    """Torrent information from a debrid service"""
    id: Union[int, str]
    name: str
    status: str = Field(default=None)
    infohash: str = Field(default=None)
    progress: float = Field(default=None)
    bytes: int = Field(default=None)
    created_at: datetime = Field(default=None)
    expires_at: datetime = Field(default=None)
    completed_at: datetime = Field(default=None)
    alternative_filename: str = Field(default=None)
    files: Dict[int, Dict[str, int | str]] = Field(default_factory=dict)  # Real-Debrid only

    @property
    def size_mb(self) -> float:
        """Convert bytes to megabytes"""
        return self.bytes / 1_000_000


class DownloadedTorrent(BaseModel):
    """Represents the result of a download operation"""
    id: Union[int, str]
    infohash: str
    container: TorrentContainer
    info: TorrentInfo
