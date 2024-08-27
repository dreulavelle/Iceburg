from datetime import datetime
from subliminal import Movie
from program.media.item import MediaItem, Show, Movie
from program.media.state import States
from program.post_processing.subliminal import Subliminal
from program.settings.manager import settings_manager
from utils.notifications import notify_on_complete
from loguru import logger


class PostProcessing:
    def __init__(self):
        self.key = "post_processing"
        self.initialized = False
        self.settings = settings_manager.settings.post_processing
        self.services = {
            Subliminal: Subliminal()
        }
        self.initialized = True

    def run(self, item: MediaItem):
        if Subliminal.should_submit(item):
            self.services[Subliminal].run(item)
        yield item

def notify(item: MediaItem):
    show = None
    if item.type in ["show", "movie"]:
        _notify(item)
    elif item.type == "episode":
        show = item.parent.parent
    elif item.type == "season":
        show = item.parent
    if show and show == States.Completed:
        _notify(show)

def _notify(_item: Show | Movie):
    duration = round((datetime.now() - _item.requested_at).total_seconds())
    logger.success(f"{_item.log_string} has been completed in {duration} seconds.")
    if settings_manager.settings.notifications.enabled:
            notify_on_complete(_item)