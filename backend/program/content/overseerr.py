"""Overseerr content module"""

from program.indexers.trakt import get_imdbid_from_tmdb
from program.media.container import MediaItemContainer
from program.media.item import MediaItem
from program.media.state import States
from program.settings.manager import settings_manager
from utils.logger import logger
from utils.request import delete, get, ping, post


class Overseerr:
    """Content class for overseerr"""

    def __init__(self, media_items: MediaItemContainer):
        self.key = "overseerr"
        self.media_items = media_items
        self.settings = settings_manager.settings.content.overseerr
        self.headers = {"X-Api-Key": self.settings.api_key}
        self.initialized = self.validate()
        if not self.initialized:
            return
        logger.info("Overseerr initialized!")

    def validate(self) -> bool:
        if not self.settings.enabled:
            logger.debug("Overseerr is set to disabled.")
            return False
        if self.settings.api_key == "" or len(self.settings.api_key) != 68:
            logger.error("Overseerr api key is not set.")
            return False
        try:
            response = ping(
                self.settings.url + "/api/v1/auth/me",
                additional_headers=self.headers,
                timeout=15,
            )
            if response.status_code >= 201:
                logger.error(
                    f"Overseerr ping failed - Status Code: {response.status_code}, Reason: {response.reason}"
                )
                return False
            return response.ok
        except Exception:
            logger.error("Overseerr url is not reachable.")
            return False

    def run(self):
        """Fetch new media from `Overseerr`"""
        response = get(
            self.settings.url + f"/api/v1/request?take={10000}",
            additional_headers=self.headers,
        )
        if not response.is_ok or response.data.pageInfo.results == 0:
            return

        # Lets look at approved items only that are only in the pending state
        pending_items = [
            item
            for item in response.data.results
            if item.status == 2 and item.media.status == 3
        ]
        for item in pending_items:
            mediaId: int = int(item.media.id)
            if not item.media.imdbId:
                imdb_id = self.get_imdb_id(item.media)
            else:
                imdb_id = item.media.imdbId

            if not imdb_id:
                logger.debug("Imdb id not found for tmdb-%s, deleting request on Overseerr", item.media.tmdbId or "N/A")
                # Delete request if imdb_id is not found
                self.delete_request(item.id)
                continue

            # Check if the item is already completed in the media container
            existing_item = self.media_items.get_imdbid(imdb_id)
            if existing_item:
                continue

            yield MediaItem({"imdb_id": imdb_id, "requested_by": self.key, "overseerr_id": mediaId})

    def get_imdb_id(self, data) -> str:
        """Get imdbId for item from overseerr"""
        if data.mediaType == "show":
            external_id = data.tvdbId
            data.mediaType = "tv"
        else:
            external_id = data.tmdbId

        response = get(
            self.settings.url + f"/api/v1/{data.mediaType}/{external_id}?language=en",
            additional_headers=self.headers,
        )
        if not response.is_ok or not hasattr(response.data, "externalIds"):
            return None

        title = getattr(response.data, "title", None) or getattr(
            response.data, "originalName", None
        )
        imdb_id = getattr(response.data.externalIds, "imdbId", None)
        if imdb_id:
            return imdb_id

        # Try alternate IDs if IMDb ID is not available
        alternate_ids = [("tmdbId", get_imdbid_from_tmdb)]
        for id_attr, fetcher in alternate_ids:
            external_id_value = getattr(response.data.externalIds, id_attr, None)
            if external_id_value:
                new_imdb_id = fetcher(external_id_value)
                if new_imdb_id:
                    logger.debug(
                        f"Found imdbId for {title} from {id_attr}: {external_id_value}"
                    )
                    return new_imdb_id
        return None

    @staticmethod
    def delete_request(mediaId: int) -> bool:
        """Delete request from `Overseerr`"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = delete(
                settings.url + f"/api/v1/request/{mediaId}",
                additional_headers=headers,
            )
            logger.info("Deleted request %s from overseerr", mediaId)
            return response.is_ok
        except Exception as e:
            logger.error("Failed to delete request from overseerr ")
            logger.error(e)
            return False

    @staticmethod
    def mark_processing(mediaId: int) -> bool:
        """Mark item as processing in overseerr"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = post(
                settings.url + f"/api/v1/media/{mediaId}/pending",
                additional_headers=headers,
                data={"is4k": False},
            )
            logger.info("Marked media %s as processing in overseerr", mediaId)
            return response.is_ok
        except Exception as e:
            logger.error("Failed to mark media as processing in overseerr with id %s", mediaId)
            logger.error(e)
            return False

    @staticmethod
    def mark_partially_available(mediaId: int) -> bool:
        """Mark item as partially available in overseerr"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = post(
                settings.url + f"/api/v1/media/{mediaId}/partial",
                additional_headers=headers,
                data={"is4k": False},
            )
            logger.info("Marked media %s as partially available in overseerr", mediaId)
            return response.is_ok
        except Exception as e:
            logger.error("Failed to mark media as partially available in overseerr with id %s", mediaId)
            logger.error(e)
            return False

    @staticmethod
    def mark_completed(mediaId: int) -> bool:
        """Mark item as completed in overseerr"""
        settings = settings_manager.settings.content.overseerr
        headers = {"X-Api-Key": settings.api_key}
        try:
            response = post(
                settings.url + f"/api/v1/media/{mediaId}/available",
                additional_headers=headers,
                data={"is4k": False},
            )
            logger.info("Marked media %s as completed in overseerr", mediaId)
            return response.is_ok
        except Exception as e:
            logger.error("Failed to mark media as completed in overseerr with id %s", mediaId)
            logger.error(e)
            return False


# Statuses for Media Requests endpoint /api/v1/request:
# item.status:
# 1 = PENDING APPROVAL, 
# 2 = APPROVED, 
# 3 = DECLINED

# Statuses for Media Info endpoint /api/v1/media:
# item.media.status:
# 1 = UNKNOWN, 
# 2 = PENDING, 
# 3 = PROCESSING, 
# 4 = PARTIALLY_AVAILABLE, 
# 5 = AVAILABLE
