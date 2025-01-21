"""Program module."""

from loguru import logger

from program.media.item import MediaItem  # noqa: F401
from program.program import Event, Program  # noqa: F401

# Add custom log levels
logger.level("RELEASE", no=35, color="<magenta>")
